#!/usr/bin/osascript -l JavaScript
// contacts-json.js — dump macOS Contacts as JSON.
//
// Queries the Contacts framework directly from osascript so the Contacts
// TCC grant attaches to Apple-signed /usr/bin/osascript and survives
// interpreter upgrades (same principle as calendar-events-json.js; see
// dotfiles CLAUDE.md "Launch Agents and AppleEvents"). First run must be
// interactive to answer the one-time Contacts permission prompt:
//
//   osascript -l JavaScript ~/.local/bin/contacts-json.js
//
// Usage: contacts-json.js
//
// stdout: {"ok": true, "contacts": [{id, name, nickname, emails: [...],
//          phones: [...], birthday: {month, day, year?} | null}]}
//         {"ok": false, "error": "..."} on denied/undetermined access.
//
// Identifiers only (matching keys for the entity layer), never facts.
// Contacts allows year-less birthdays — the year is NSDateComponentUndefined
// (NSIntegerMax) on those cards, so it is emitted only when plausible.

ObjC.import('Contacts')
ObjC.import('Foundation')

function str(nsvalue) {
  try {
    if (!nsvalue || (typeof nsvalue.isNil === 'function' && nsvalue.isNil())) return ''
    return String(nsvalue.js !== undefined ? nsvalue.js : nsvalue)
  } catch (e) {
    return ''
  }
}

function requestAccess(store) {
  let finished = false
  const handler = (granted, err) => { finished = true }
  store.requestAccessForEntityTypeCompletionHandler($.CNEntityTypeContacts, handler)
  const rl = $.NSRunLoop.currentRunLoop
  const deadline = Date.now() + 30000
  while (!finished && Date.now() < deadline) {
    rl.runModeBeforeDate(
      $.NSDefaultRunLoopMode, $.NSDate.dateWithTimeIntervalSinceNow(0.2))
  }
}

function run() {
  const authorized = () => {
    // JXA hands these NSInteger enums back as strings, so compare on Number().
    // 3 = authorized; 4 = limited, which still reads the granted subset.
    const s = Number(
      $.CNContactStore.authorizationStatusForEntityType($.CNEntityTypeContacts))
    return s === 3 || s === 4
  }

  const store = $.CNContactStore.alloc.init
  if (!authorized()) {
    requestAccess(store)
    if (!authorized()) {
      return JSON.stringify({
        ok: false,
        error: 'Contacts access not granted for osascript. Run this script ' +
          'once in a terminal and approve the prompt, or enable osascript ' +
          'under System Settings > Privacy & Security > Contacts.',
      })
    }
  }

  // keysToFetch must be built ObjC-side: a JS array marshals its ObjC
  // elements into dictionaries, and CNContactStore then throws
  // "unrecognized selector _cn_requiredKeys" on the first fetch.
  const FULL_NAME = Number($.CNContactFormatterStyleFullName)
  const keys = $.NSMutableArray.array
  keys.addObject($.CNContactNicknameKey)
  keys.addObject($.CNContactEmailAddressesKey)
  keys.addObject($.CNContactPhoneNumbersKey)
  keys.addObject($.CNContactBirthdayKey)
  keys.addObject($.CNContactFormatter.descriptorForRequiredKeysForStyle(FULL_NAME))

  // The nil predicate must be $() — a JS null yields zero containers with
  // no error instead of "all containers".
  const containers = store.containersMatchingPredicateError($(), Ref())
  if (!containers || containers.isNil()) {
    return JSON.stringify({ ok: false, error: 'could not enumerate contact containers' })
  }

  const out = []
  const seen = {}
  let failedContainers = 0
  for (let i = 0; i < containers.count; i++) {
    const cid = containers.objectAtIndex(i).identifier
    const pred = $.CNContact.predicateForContactsInContainerWithIdentifier(cid)
    const errRef = Ref()
    const contacts = store.unifiedContactsMatchingPredicateKeysToFetchError(
      pred, keys, errRef)
    if (!contacts || contacts.isNil()) {
      failedContainers++
      continue
    }
    for (let j = 0; j < contacts.count; j++) {
      const c = contacts.objectAtIndex(j)
      // A unified contact linked across containers repeats per container.
      const id = str(c.identifier)
      if (seen[id]) continue
      seen[id] = true
      const emails = []
      const ea = c.emailAddresses
      if (ea && !ea.isNil()) {
        for (let k = 0; k < ea.count; k++) {
          const v = str(ea.objectAtIndex(k).value)
          if (v) emails.push(v)
        }
      }
      const phones = []
      const pa = c.phoneNumbers
      if (pa && !pa.isNil()) {
        for (let k = 0; k < pa.count; k++) {
          const v = str(pa.objectAtIndex(k).value.stringValue)
          if (v) phones.push(v)
        }
      }
      let birthday = null
      const bd = c.birthday
      if (bd && !bd.isNil()) {
        const month = Number(bd.month)
        const day = Number(bd.day)
        const year = Number(bd.year)
        if (month >= 1 && month <= 12 && day >= 1 && day <= 31) {
          birthday = { month: month, day: day }
          if (year >= 1 && year <= 9999) birthday.year = year
        }
      }
      out.push({
        id: id,
        name: str($.CNContactFormatter.stringFromContactStyle(c, FULL_NAME)),
        nickname: str(c.nickname),
        emails: emails,
        phones: phones,
        birthday: birthday,
      })
    }
  }
  // A partial dump must not pass ok:true — the caller's BriefingSuppressed
  // redaction guard is fail-closed only when it can tell "unavailable" from
  // "empty on purpose".
  if (failedContainers > 0) {
    return JSON.stringify({
      ok: false,
      error: failedContainers + ' of ' + containers.count +
        ' contact container(s) failed to fetch',
    })
  }
  out.sort((a, b) => (a.name < b.name ? -1 : a.name > b.name ? 1 : 0))
  return JSON.stringify({ ok: true, contacts: out })
}
