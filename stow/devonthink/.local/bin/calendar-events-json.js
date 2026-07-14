#!/usr/bin/osascript -l JavaScript
// calendar-events-json.js — dump one day of calendar events as JSON.
//
// Queries EventKit directly from osascript so the Calendars TCC grant
// attaches to Apple-signed /usr/bin/osascript and survives interpreter
// upgrades (same principle as the pipeline's AppleEvents rules; see
// dotfiles CLAUDE.md "Launch Agents and AppleEvents"). First run must be
// interactive to answer the one-time Calendars permission prompt:
//
//   osascript -l JavaScript ~/.local/bin/calendar-events-json.js
//
// Usage: calendar-events-json.js [YYYY-MM-DD [YYYY-MM-DD]]
//        one day (default: today, local time), or an inclusive day range.
//
// stdout: {"ok": true, "date": "...", "events": [{title, calendar, date,
//          start, end, all_day, location, rsvp, organizer_is_self,
//          attendees: [{name, email, is_self, is_person}]}]}
//         {"ok": false, "error": "..."} on denied/undetermined access.
//
// Each event carries its own local `date` so a range dump stays usable.
//
// `rsvp` is your own participant status, and is null when the event carries
// no invitation for you personally: either it has no attendees (your own
// event) or it reached you through a distribution list, where Exchange lists
// the list, never you, and so has no RSVP of yours to report. Note that
// declining an Exchange invite removes the event from the calendar outright,
// so "declined" is essentially never seen here — "unknown" (invited, never
// responded) is the status that actually distinguishes a meeting you are
// going to from one you ignored.
//
// Exchange reports conference rooms with participantType Person, identical
// to humans on every EventKit field, so `is_person` cannot exclude them —
// consumers filter rooms by name.

ObjC.import('EventKit')
ObjC.import('Foundation')

// EKParticipantStatus, in enum order.
const RSVP = ['unknown', 'pending', 'accepted', 'declined', 'tentative',
              'delegated', 'completed', 'in-process']

function str(nsvalue) {
  try {
    if (!nsvalue || (typeof nsvalue.isNil === 'function' && nsvalue.isNil())) return ''
    return String(nsvalue.js !== undefined ? nsvalue.js : nsvalue)
  } catch (e) {
    return ''
  }
}

function isoLocal(nsdate) {
  const d = new Date(nsdate.timeIntervalSince1970 * 1000)
  const local = new Date(d.getTime() - d.getTimezoneOffset() * 60000)
  return local.toISOString().slice(0, 19)
}

function requestAccess(store) {
  let finished = false
  const handler = (granted, err) => { finished = true }
  if (store.respondsToSelector('requestFullAccessToEventsWithCompletion:')) {
    store.requestFullAccessToEventsWithCompletion(handler)
  } else {
    store.requestAccessToEntityTypeCompletion($.EKEntityTypeEvent, handler)
  }
  const rl = $.NSRunLoop.currentRunLoop
  const deadline = Date.now() + 30000
  while (!finished && Date.now() < deadline) {
    rl.runModeBeforeDate(
      $.NSDefaultRunLoopMode, $.NSDate.dateWithTimeIntervalSinceNow(0.2))
  }
}

function run(argv) {
  let dateStr = argv[0]
  if (!dateStr) {
    const now = new Date()
    const local = new Date(now.getTime() - now.getTimezoneOffset() * 60000)
    dateStr = local.toISOString().slice(0, 10)
  }
  const endStr = argv[1] || dateStr
  for (const d of [dateStr, endStr]) {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(d)) {
      return JSON.stringify({ ok: false, error: 'bad date: ' + d })
    }
  }
  if (endStr < dateStr) {
    return JSON.stringify({ ok: false, error: 'end before start: ' + endStr })
  }

  const authorized = () => {
    // 3 = authorized/full access; 4 (write-only) cannot read events.
    return Number(
      $.EKEventStore.authorizationStatusForEntityType($.EKEntityTypeEvent)
    ) === 3
  }

  const store = $.EKEventStore.alloc.init
  if (!authorized()) {
    requestAccess(store)
    if (!authorized()) {
      return JSON.stringify({
        ok: false,
        error: 'Calendar access not granted for osascript. Run this script ' +
          'once in a terminal and approve the prompt, or enable osascript ' +
          'under System Settings > Privacy & Security > Calendars.',
      })
    }
  }

  const parts = dateStr.split('-').map(Number)
  const endParts = endStr.split('-').map(Number)
  const dayStart = new Date(parts[0], parts[1] - 1, parts[2]).getTime() / 1000
  const dayEnd =
    new Date(endParts[0], endParts[1] - 1, endParts[2]).getTime() / 1000 + 86400
  const start = $.NSDate.dateWithTimeIntervalSince1970(dayStart)
  const end = $.NSDate.dateWithTimeIntervalSince1970(dayEnd)
  const pred = store.predicateForEventsWithStartDateEndDateCalendars(start, end, $())
  const events = store.eventsMatchingPredicate(pred)

  const out = []
  for (let i = 0; i < events.count; i++) {
    const ev = events.objectAtIndex(i)
    const attendees = []
    let rsvp = null
    const atts = ev.attendees
    if (atts && !atts.isNil()) {
      for (let j = 0; j < atts.count; j++) {
        const p = atts.objectAtIndex(j)
        const isSelf = !!p.isCurrentUser
        // JXA hands these NSInteger enums back as strings, so coerce on Number().
        if (isSelf) rsvp = RSVP[Number(p.participantStatus)] || 'unknown'
        let email = ''
        const url = p.URL
        if (url && !url.isNil()) {
          email = str(url.absoluteString).replace(/^mailto:/i, '')
        }
        attendees.push({
          name: str(p.name),
          email: email,
          is_self: isSelf,
          // EKParticipantTypePerson = 1
          is_person: Number(p.participantType) === 1,
        })
      }
    }
    const org = ev.organizer
    out.push({
      title: str(ev.title),
      calendar: str(ev.calendar.title),
      date: isoLocal(ev.startDate).slice(0, 10),
      start: isoLocal(ev.startDate),
      end: isoLocal(ev.endDate),
      all_day: !!ev.allDay,
      location: str(ev.location),
      rsvp: rsvp,
      organizer_is_self: org && !org.isNil() ? !!org.isCurrentUser : false,
      // EKEventStatusCanceled = 3. Exchange keeps a cancelled meeting on the
      // calendar, retitled "Canceled: …", with your acceptance intact.
      canceled: Number(ev.status) === 3,
      attendees: attendees,
    })
  }
  out.sort((a, b) => (a.start < b.start ? -1 : a.start > b.start ? 1 : 0))
  return JSON.stringify({ ok: true, date: dateStr, events: out })
}
