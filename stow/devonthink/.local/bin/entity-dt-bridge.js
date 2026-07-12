#!/usr/bin/osascript -l JavaScript
// entity-dt-bridge.js — single DEVONthink I/O gateway for the entity layer.
//
// Executes a batch of operations described in a JSON file and prints a JSON
// result to stdout. All entity-layer AppleEvents to DEVONthink flow through
// this script so the Python orchestrators (dt-morning-brief.py,
// entity-filing.py) stay stdlib-only and JSON never has to round-trip
// through AppleScript records.
//
// Wire format:
//   argv[0]  path to a JSON file: {"ops": [{"op": "<name>", ...}, ...]}
//   stdout   {"ok": true, "results": [...]}                on success
//            {"ok": false, "error": "...", "failed_op": i,
//             "results": [...partial...]}                  on first failure
//            {"ok": false, "unavailable": true, "error": "..."}
//                                                          DT/db not reachable
//
// Ops:
//   dump_people        {include_bodies?}                -> [{uuid,name,aliases,md,body?}]
//   list_sources       {}                               -> [{uuid,name,kind,eventdate,
//                                                            modified,ready,...}]
//   get_source         {uuid}                            -> {uuid,name,kind,eventdate,...}
//   list_group         {path}                           -> [{uuid,name}]
//   search             {query,limit?}                   -> [{uuid,name,eventdate,documenttype}]
//   get_at_path        {path}                           -> {uuid,name} | null
//   get_text           {uuid}                           -> {uuid,text}
//   set_text           {uuid,text}                      -> {uuid}
//   ensure_group       {path,exclude_chat?}             -> {uuid,created,chat_excluded}
//   get_fields         {uuid,fields}                    -> {uuid,fields:{k:v}}
//   set_fields         {uuid,fields}                    -> {uuid}
//   chat               {prompt,role}                    -> {text}
//   ensure_person      {name,aliases?,fields?,log_lines?} -> {uuid,created}
//   ensure_event       {name,date,location?,attendees?,summary?,source_uuid,
//                       log_line?}                      -> {uuid,created,merged?}
//   append_log         {uuid,lines}                     -> {uuid,appended,skipped}
//   set_field          {uuid,field,value,effective_date?,
//                       expected_previous?,transition_line?}
//                                                       -> {uuid,changed,previous,stale?}
//   bump_lastcontact   {uuid,date}                      -> {uuid,changed}
//   mark_filed         {uuid}                           -> {uuid}
//   create_record      {name,path,text,fields?,tags?}   -> {uuid}
//   get_or_create_daily {date,heading}                  -> {uuid,text,created}
//   upsert_section     {uuid,header,content}            -> {uuid,changed,replaced}
//   relink_entities    {}                               -> {records,changed}
//   trash              {uuid}                           -> {uuid}

ObjC.import('Foundation')

const DB_NAME = 'Lorebook'
const ENTITIES_PATH = '/20_ENTITIES'
const PEOPLE_PATH = ENTITIES_PATH + '/People'
const PLACES_PATH = ENTITIES_PATH + '/Places'
const EVENTS_PATH = ENTITIES_PATH + '/Events'
const DAILY_PATH = '/10_DAILY'
const JOURNAL_PATH = '/15_JOURNAL'
const NOTES_SECTION = "## Today's Notes"
const LOG_SECTION = '## Biographical Log'
const EVENT_LOG_SECTION = '## Log'
const TEMPLATE_DIR =
  $.NSHomeDirectory().js +
  '/Library/Application Support/DEVONthink/Templates.noindex/Entities/'
const PERSON_TEMPLATE = TEMPLATE_DIR + 'Person.md'
const EVENT_TEMPLATE = TEMPLATE_DIR + 'Event.md'

function readFile(path) {
  const s = $.NSString.stringWithContentsOfFileEncodingError(
    path, $.NSUTF8StringEncoding, null)
  return s.isNil() ? null : s.js
}

function mdValue(rec, key) {
  const md = rec.customMetaData() || {}
  const v = md['md' + key]
  return v === undefined || v === null ? '' : String(v)
}

// A flag set by script reads back as '1'; the same flag ticked in the GUI
// reads back as 'true' (see CLAUDE.md on DT's boolean representation).
function flagSet(v) {
  return v === '1' || v === 'true'
}

function isoStamp(d) {
  return d
    ? new Date(d.getTime() - d.getTimezoneOffset() * 60000)
        .toISOString().slice(0, 19)
    : ''
}

function normName(s) {
  return String(s || '').toLowerCase().normalize('NFKD')
    .replace(/[̀-ͯ]/g, '').replace(/\s+/g, ' ').trim()
}

function personBrief(rec, includeBody) {
  const out = {
    uuid: rec.uuid(),
    name: rec.name(),
    aliases: String(rec.aliases() || ''),
    md: rec.customMetaData() || {},
  }
  if (includeBody) out.body = rec.plainText()
  return out
}

// Person lookup key set: record name plus each comma-separated alias.
function personKeys(rec) {
  const keys = [normName(rec.name())]
  String(rec.aliases() || '').split(',').forEach(a => {
    const k = normName(a)
    if (k) keys.push(k)
  })
  return keys
}

function insertUnderSection(body, header, lines) {
  const bodyLines = body.split('\n')
  let headerIdx = -1
  for (let i = 0; i < bodyLines.length; i++) {
    if (bodyLines[i].trim() === header) { headerIdx = i; break }
  }
  if (headerIdx === -1) {
    while (bodyLines.length && bodyLines[bodyLines.length - 1].trim() === '')
      bodyLines.pop()
    return bodyLines.concat(['', header, ''], lines).join('\n') + '\n'
  }
  let end = bodyLines.length
  for (let i = headerIdx + 1; i < bodyLines.length; i++) {
    if (/^#{1,2}\s/.test(bodyLines[i])) { end = i; break }
  }
  let insertAt = end
  while (insertAt > headerIdx + 1 && bodyLines[insertAt - 1].trim() === '')
    insertAt--
  const block = insertAt === headerIdx + 1 ? [''].concat(lines) : lines
  bodyLines.splice(insertAt, 0, ...block)
  return bodyLines.join('\n')
}

// Set by run(); lets module-level helpers reach the DT session.
let bridgeCtx = null
let entityIndex = null

function escapeRe(s) {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

// Names/aliases of every existing entity record, longest first so
// "Maya Chen" wins over "Maya". Rebuilt after any create.
function buildEntityIndex() {
  const entries = []
  for (const path of [PEOPLE_PATH, PLACES_PATH, EVENTS_PATH]) {
    let group
    try { group = bridgeCtx.groupAt(path) } catch (e) { continue }
    for (const rec of group.children()) {
      if (String(rec.type()) !== 'markdown') continue
      const uuid = rec.uuid()
      const names = [rec.name()].concat(String(rec.aliases() || '').split(','))
      for (const n of names) {
        const t = n.trim()
        if (t.length >= 3) entries.push({ name: t, uuid: uuid })
      }
    }
  }
  entries.sort((a, b) => b.name.length - a.name.length)
  return entries
}

// Markdown-link spans land at odd indices after a capture-group split.
const LINK_SPLIT_RE = /(\[[^\]]*\]\([^)]*\))/

// Wrap the first mention of each known entity in an item link so hand-
// authored Places/Events (and other People) accrue backlinks from every
// filed fact. Never links inside an existing link, never links the target
// record to itself, skips entities the line already links to.
function linkEntities(line, excludeUuid) {
  if (entityIndex === null) entityIndex = buildEntityIndex()
  let out = line
  for (const e of entityIndex) {
    if (e.uuid === excludeUuid || out.indexOf(e.uuid) !== -1) continue
    const re = new RegExp(
      '(^|[^\\w\\[])(' + escapeRe(e.name) + ')(?![\\w\\]])', 'i')
    const parts = out.split(LINK_SPLIT_RE)
    for (let i = 0; i < parts.length; i += 2) {
      const m = parts[i].match(re)
      if (m) {
        parts[i] = parts[i].replace(
          re, m[1] + '[' + m[2] + '](x-devonthink-item://' + e.uuid + ')')
        out = parts.join('')
        break
      }
    }
  }
  return out
}

// A filed fact's dedup identity: its source-note UUID plus the line's text
// with every item-link flattened to its label and the fact: provenance
// marker removed. Keying on content rather than just the source link keeps
// re-applies idempotent while letting a corrected re-extraction of the same
// note file the genuinely new facts it surfaces; flattening links and
// dropping the marker means neither auto-linking nor a hand-deleted marker
// ever changes the identity.
function factSignature(line) {
  const src = (line.match(
    /\[source\]\(x-devonthink-item:\/\/([0-9A-Fa-f-]+)\)/) || [])[1] || ''
  const text = line
    .replace(/<!--\s*fact:[0-9a-f]+\s*-->/g, '')
    .replace(/\[([^\]]*)\]\(x-devonthink-item:\/\/[0-9A-Fa-f-]+\)/g, '$1')
    .replace(/\s+/g, ' ').trim()
  return src + '|' + text
}

// Skip lines already filed (by fact signature), link known entities in the
// remainder, then insert under the given section header.
function appendLogLines(rec, lines, section) {
  const body = rec.plainText()
  const uuid = rec.uuid()
  const seen = Object.create(null)
  for (const bl of body.split('\n')) seen[factSignature(bl)] = true
  const fresh = []
  let skipped = 0
  for (const line of lines || []) {
    const sig = factSignature(line)
    if (seen[sig]) { skipped++; continue }
    seen[sig] = true
    fresh.push(linkEntities(line, uuid))
  }
  if (fresh.length) {
    rec.plainText = insertUnderSection(body, section || LOG_SECTION, fresh)
  }
  return { appended: fresh.length, skipped: skipped }
}

function run(argv) {
  const opsRaw = readFile(argv[0])
  if (opsRaw === null) {
    return JSON.stringify({ ok: false, error: 'cannot read ops file: ' + argv[0] })
  }
  const ops = JSON.parse(opsRaw).ops || []

  const dt = Application('com.devon-technologies.think')

  // A byName() specifier resolves lazily, so a closed or still-loading
  // database would surface as a bare "Can't get object." from whichever op
  // touches it first. Enumerate, and report unavailability as its own answer.
  let db = null
  try {
    const dbs = dt.databases()
    for (let i = 0; i < dbs.length; i++) {
      if (String(dbs[i].name()) === DB_NAME) { db = dbs[i]; break }
    }
  } catch (e) {
    return JSON.stringify({
      ok: false,
      unavailable: true,
      error: 'DEVONthink is not answering: ' + String(e.message || e),
    })
  }
  if (db === null) {
    return JSON.stringify({
      ok: false,
      unavailable: true,
      error: 'database not open: ' + DB_NAME,
    })
  }

  function groupAt(path) {
    const g = dt.getRecordAt(path, { in: db })
    if (!g) throw new Error('group not found: ' + path)
    return g
  }

  function byUuid(uuid) {
    const r = dt.getRecordWithUuid(uuid)
    if (!r) throw new Error('record not found: ' + uuid)
    return r
  }

  bridgeCtx = { groupAt: groupAt }

  function findByNameOrAlias(path, name) {
    const want = normName(name)
    const hits = []
    for (const rec of groupAt(path).children()) {
      if (personKeys(rec).indexOf(want) !== -1) hits.push(rec)
    }
    return hits
  }

  function findPerson(name) {
    return findByNameOrAlias(PEOPLE_PATH, name)
  }

  function personLink(name) {
    const hits = findPerson(name)
    if (hits.length === 1) {
      return '[' + hits[0].name() + '](x-devonthink-item://' + hits[0].uuid() + ')'
    }
    return name
  }

  function personSkeleton(name) {
    let tpl = readFile(PERSON_TEMPLATE)
    if (tpl === null) {
      tpl = '# Person Name\n\n**Role:** —\n**City:** —\n**Partner:** —\n' +
        '**How we met:** —\n\n' + LOG_SECTION + '\n'
    }
    const lines = tpl.split('\n')
    for (let i = 0; i < lines.length; i++) {
      if (/^#\s/.test(lines[i])) { lines[i] = '# ' + name; break }
    }
    return lines.join('\n')
  }

  const handlers = {
    dump_people(op) {
      const includeBodies = op.include_bodies !== false
      return groupAt(PEOPLE_PATH).children()
        .filter(r => String(r.type()) === 'markdown')
        .map(r => personBrief(r, includeBodies))
    },

    list_sources() {
      const out = []
      const seen = {}
      const add = (rec, kind) => {
        const uuid = rec.uuid()
        if (seen[uuid]) return
        seen[uuid] = true
        const added = rec.additionDate()
        out.push({
          uuid: uuid,
          name: rec.name(),
          kind: kind,
          eventdate: mdValue(rec, 'eventdate'),
          participants: mdValue(rec, 'granolaparticipants'),
          added: added ? isoStamp(added).slice(0, 10) : '',
          modified: isoStamp(rec.modificationDate()),
          entityfiled: flagSet(mdValue(rec, 'entityfiled')),
          // NeedsProcessing=1 means the smart-rule pipeline (OCR, comment
          // formatting, enrichment) hasn't finished — the record's text may
          // not exist yet. Daily notes never carry the flag.
          ready: kind === 'daily'
            ? true
            : !flagSet(mdValue(rec, 'needsprocessing')),
        })
      }
      // Quoted-phrase equality (mddocumenttype=="Meeting Notes") matches
      // nothing in DT's query parser; substring match is the reliable form.
      dt.search('mddocumenttype:~Meeting', { in: db.root() })
        .forEach(r => add(r, 'meeting'))
      dt.search('mdhandwritten==1', { in: db.root() })
        .forEach(r => add(r, 'handwritten'))
      const today = new Date()
      const localToday = new Date(today.getTime() - today.getTimezoneOffset() * 60000)
        .toISOString().slice(0, 10)
      for (const rec of groupAt(DAILY_PATH).children()) {
        const name = rec.name()
        if (/^\d{4}-\d{2}-\d{2}$/.test(name) && name < localToday) {
          add(rec, 'daily')
        }
      }
      // Journal entries live under year subgroups; today's entry is
      // skipped like today's daily note — it may still gain content.
      let journalGroup = null
      try { journalGroup = groupAt(JOURNAL_PATH) } catch (e) {}
      if (journalGroup) {
        for (const yearGroup of journalGroup.children()) {
          if (String(yearGroup.type()) !== 'group') continue
          for (const rec of yearGroup.children()) {
            const name = rec.name()
            if (/^\d{4}-\d{2}-\d{2} Journal$/.test(name) &&
                name.slice(0, 10) < localToday) {
              add(rec, 'journal')
            }
          }
        }
      }
      return out
    },

    // Classify one record the same way list_sources buckets its hits, so
    // --force can target a record the database sweep never surfaces.
    get_source(op) {
      const r = byUuid(op.uuid)
      const hw = mdValue(r, 'handwritten')
      let kind = 'other'
      if (String(r.location() || '').indexOf(DAILY_PATH) === 0) kind = 'daily'
      else if (String(r.location() || '').indexOf(JOURNAL_PATH) === 0) kind = 'journal'
      else if (flagSet(hw)) kind = 'handwritten'
      else if (mdValue(r, 'documenttype').indexOf('Meeting') !== -1) kind = 'meeting'
      const added = r.additionDate()
      return {
        uuid: r.uuid(),
        name: r.name(),
        kind: kind,
        eventdate: mdValue(r, 'eventdate'),
        participants: mdValue(r, 'granolaparticipants'),
        added: added ? isoStamp(added).slice(0, 10) : '',
        modified: isoStamp(r.modificationDate()),
        entityfiled: flagSet(mdValue(r, 'entityfiled')),
        ready: kind === 'daily'
          ? true
          : !flagSet(mdValue(r, 'needsprocessing')),
      }
    },

    list_group(op) {
      return groupAt(op.path).children().map(r => ({ uuid: r.uuid(), name: r.name() }))
    },

    search(op) {
      const cap = op.limit || 50
      return dt.search(String(op.query || ''), { in: db.root() })
        .slice(0, cap)
        .map(r => ({
          uuid: r.uuid(),
          name: r.name(),
          eventdate: mdValue(r, 'eventdate'),
          documenttype: mdValue(r, 'documenttype'),
        }))
    },

    get_at_path(op) {
      const r = dt.getRecordAt(op.path, { in: db })
      if (!r) return null
      return { uuid: r.uuid(), name: r.name() }
    },

    get_text(op) {
      const r = byUuid(op.uuid)
      return { uuid: op.uuid, text: r.plainText() }
    },

    ensure_group(op) {
      let g = dt.getRecordAt(op.path, { in: db })
      const created = !g
      if (!g) g = dt.createLocation(op.path, { in: db })
      let chatExcluded = false
      if (op.exclude_chat) {
        // Privacy boundary, same as /10_DAILY: journal content must never
        // be readable by DT chat, which may be a cloud provider.
        try {
          g.excludeFromChat = true
          chatExcluded = flagSet(String(g.excludeFromChat()))
        } catch (e) {
          chatExcluded = false
        }
      }
      return { uuid: g.uuid(), created: created, chat_excluded: chatExcluded }
    },

    get_fields(op) {
      const out = {}
      const r = byUuid(op.uuid)
      for (const field of op.fields || []) {
        out[field] = mdValue(r, field.toLowerCase().replace(/\s/g, ''))
      }
      return { uuid: op.uuid, fields: out }
    },

    set_fields(op) {
      const r = byUuid(op.uuid)
      for (const [field, value] of Object.entries(op.fields || {})) {
        dt.addCustomMetaData(value, { for: field, to: r })
      }
      return { uuid: op.uuid }
    },

    set_text(op) {
      const r = byUuid(op.uuid)
      r.plainText = op.text
      return { uuid: op.uuid }
    },

    chat(op) {
      const text = dt.getChatResponseForMessage(op.prompt, {
        role: op.role || 'You are an assistant that responds only with JSON.',
        mode: 'text',
        thinking: false,
        toolCalls: false,
        as: 'text',
      })
      return { text: String(text) }
    },

    ensure_person(op) {
      const hits = findPerson(op.name)
      if (hits.length > 1) {
        throw new Error('ambiguous person: ' + op.name + ' (' + hits.length + ' matches)')
      }
      let rec
      let created = false
      if (hits.length === 1) {
        rec = hits[0]
      } else {
        rec = dt.createRecordWith(
          { name: op.name, type: 'markdown' }, { in: groupAt(PEOPLE_PATH) })
        rec.plainText = personSkeleton(op.name)
        created = true
        entityIndex = null
        dt.addCustomMetaData('Person', { for: 'entitytype', to: rec })
        dt.addCustomMetaData('active', { for: 'entitystatus', to: rec })
      }
      if (op.aliases) rec.aliases = op.aliases
      for (const [field, value] of Object.entries(op.fields || {})) {
        if (!value) continue
        // ensure_person can resolve to an existing record (a re-run proposal),
        // so LastContact keeps the bump op's monotonicity instead of being
        // overwritten backwards.
        if (field === 'lastcontact') {
          const current = mdValue(rec, 'lastcontact')
          const comparable = /^\d{4}-\d{2}-\d{2}$/.test(current) ? current : ''
          if (!comparable || String(value) > comparable) {
            dt.addCustomMetaData(value, { for: field, to: rec })
          }
          continue
        }
        dt.addCustomMetaData(value, { for: field, to: rec })
      }
      if ((op.log_lines || []).length) {
        appendLogLines(rec, op.log_lines)
      }
      return { uuid: rec.uuid(), created: created }
    },

    // Event identity is normalized name PLUS date proximity: same-name
    // events dated within EVENT_MATCH_DAYS merge (closest date wins);
    // outside the window a new record is created, so "Christmas Party"
    // 2025 and 2026 stay distinct. A matched event merges its structured
    // fields — attendee union, missing Date/Where filled — instead of
    // freezing whatever the first note happened to mention.
    ensure_event(op) {
      const EVENT_MATCH_DAYS = 45
      const hits = findByNameOrAlias(EVENTS_PATH, op.name)
      const opDate = String(op.date || '')
      // log_line arrives pre-built (with its fact: provenance marker) from
      // ops_for_plan; the composed form covers proposals frozen before it.
      const logLine = op.log_line ||
        (op.summary && op.source_uuid
          ? '- ' + op.date + ' — ' + op.summary +
            ' ([source](x-devonthink-item://' + op.source_uuid + '))'
          : null)
      let rec = null
      let bestGap = Infinity
      for (const h of hits) {
        const d = mdValue(h, 'eventdate').slice(0, 10)
        let gap
        if (!opDate || !d) {
          // Undated on either side: date-agnostic legacy match, weakest.
          gap = EVENT_MATCH_DAYS
        } else {
          gap = Math.abs(new Date(opDate) - new Date(d)) / 86400000
        }
        if (gap <= EVENT_MATCH_DAYS && gap < bestGap) {
          rec = h
          bestGap = gap
        }
      }
      if (rec !== null) {
        const uuid = rec.uuid()
        const lines = rec.plainText().split('\n')
        let changed = false
        for (let i = 0; i < lines.length; i++) {
          const bare = v => v === '' || v === '—'
          if (/^\*\*Date:\*\*/.test(lines[i])) {
            const v = lines[i].replace(/^\*\*Date:\*\*\s*/, '').trim()
            if (bare(v) && opDate) {
              lines[i] = '**Date:** ' + opDate
              changed = true
              if (!mdValue(rec, 'eventdate')) {
                dt.addCustomMetaData(opDate, { for: 'eventdate', to: rec })
              }
            }
          } else if (/^\*\*Where:\*\*/.test(lines[i])) {
            const v = lines[i].replace(/^\*\*Where:\*\*\s*/, '').trim()
            if (bare(v) && op.location) {
              lines[i] = '**Where:** ' + linkEntities(op.location, uuid)
              changed = true
            }
          } else if (/^\*\*Who:\*\*/.test(lines[i])) {
            const raw = lines[i].replace(/^\*\*Who:\*\*\s*/, '').trim()
            const existing = (raw === '' || raw === '—')
              ? [] : raw.split(',').map(s => s.trim()).filter(Boolean)
            const seen = Object.create(null)
            for (const e of existing) {
              seen[normName(e.replace(/^\[([^\]]*)\].*$/, '$1'))] = true
            }
            const additions = []
            for (const a of (op.attendees || [])) {
              const k = normName(a)
              if (k && !seen[k]) {
                seen[k] = true
                additions.push(personLink(a))
              }
            }
            if (additions.length) {
              lines[i] = '**Who:** ' + existing.concat(additions).join(', ')
              changed = true
            }
          }
        }
        if (changed) rec.plainText = lines.join('\n')
        if (logLine) appendLogLines(rec, [logLine], EVENT_LOG_SECTION)
        return { uuid: uuid, created: false, merged: changed }
      }
      let tpl = readFile(EVENT_TEMPLATE)
      if (tpl === null) {
        tpl = '# Event Name\n\n**Date:** —\n**Where:** —\n**Who:** —\n\n' +
          EVENT_LOG_SECTION + '\n'
      }
      const attendees = (op.attendees || []).map(personLink)
      const lines = tpl.split('\n')
      for (let i = 0; i < lines.length; i++) {
        if (/^#\s/.test(lines[i])) lines[i] = '# ' + op.name
        else if (/^\*\*Date:\*\*/.test(lines[i])) lines[i] = '**Date:** ' + op.date
        else if (/^\*\*Where:\*\*/.test(lines[i]) && op.location) {
          lines[i] = '**Where:** ' + linkEntities(op.location, null)
        } else if (/^\*\*Who:\*\*/.test(lines[i]) && attendees.length) {
          lines[i] = '**Who:** ' + attendees.join(', ')
        }
      }
      rec = dt.createRecordWith(
        { name: op.name, type: 'markdown' }, { in: groupAt(EVENTS_PATH) })
      rec.plainText = lines.join('\n')
      entityIndex = null
      dt.addCustomMetaData('Event', { for: 'entitytype', to: rec })
      dt.addCustomMetaData(op.date, { for: 'eventdate', to: rec })
      if (logLine) appendLogLines(rec, [logLine], EVENT_LOG_SECTION)
      return { uuid: rec.uuid(), created: true }
    },

    append_log(op) {
      const rec = byUuid(op.uuid)
      const counts = appendLogLines(rec, op.lines)
      return { uuid: op.uuid, appended: counts.appended, skipped: counts.skipped }
    },

    // Temporal conflict protection: each applied update records the source's
    // date in the FieldAsOf JSON blob, and an update dated before a field's
    // recorded date is refused (an older source processed later, or an
    // approved proposal overtaken by a newer write, must not clobber current
    // state). expected_previous is the fallback for fields with no recorded
    // date yet: a mismatch means the value changed after the ops were built.
    set_field(op) {
      const rec = byUuid(op.uuid)
      const previous = mdValue(rec, op.field)
      let dates = {}
      try { dates = JSON.parse(mdValue(rec, 'fieldasof') || '{}') || {} }
      catch (e) { dates = {} }
      const asof = typeof dates[op.field] === 'string' ? dates[op.field] : ''
      const stampAsOf = () => {
        if (op.effective_date && op.effective_date > asof) {
          dates[op.field] = op.effective_date
          dt.addCustomMetaData(JSON.stringify(dates), { for: 'fieldasof', to: rec })
        }
      }
      if (previous === String(op.value)) {
        stampAsOf()
        return { uuid: op.uuid, changed: false, previous: previous }
      }
      if (op.effective_date && asof) {
        if (op.effective_date < asof) {
          return { uuid: op.uuid, changed: false, stale: true,
                   previous: previous, asof: asof }
        }
      } else if (previous && op.expected_previous !== undefined &&
                 op.expected_previous !== null &&
                 String(op.expected_previous) !== previous) {
        return { uuid: op.uuid, changed: false, stale: true, previous: previous }
      }
      dt.addCustomMetaData(op.value, { for: op.field, to: rec })
      stampAsOf()
      if (op.transition_line) appendLogLines(rec, [op.transition_line])
      return { uuid: op.uuid, changed: true, previous: previous }
    },

    bump_lastcontact(op) {
      const rec = byUuid(op.uuid)
      const current = mdValue(rec, 'lastcontact')
      // A hand-typed non-ISO value sorts above every ISO date and would
      // freeze the field; treat it as absent so a real date can repair it.
      const comparable = /^\d{4}-\d{2}-\d{2}$/.test(current) ? current : ''
      if (comparable && comparable >= op.date) {
        return { uuid: op.uuid, changed: false }
      }
      dt.addCustomMetaData(op.date, { for: 'lastcontact', to: rec })
      return { uuid: op.uuid, changed: true }
    },

    mark_filed(op) {
      dt.addCustomMetaData(true, { for: 'entityfiled', to: byUuid(op.uuid) })
      return { uuid: op.uuid }
    },

    create_record(op) {
      const rec = dt.createRecordWith(
        { name: op.name, type: 'markdown' }, { in: groupAt(op.path) })
      rec.plainText = op.text
      for (const [field, value] of Object.entries(op.fields || {})) {
        dt.addCustomMetaData(value, { for: field, to: rec })
      }
      if (op.tags) rec.tags = op.tags
      return { uuid: rec.uuid() }
    },

    get_or_create_daily(op) {
      const group = groupAt(DAILY_PATH)
      for (const rec of group.children()) {
        if (rec.name() === op.date) {
          return { uuid: rec.uuid(), text: rec.plainText(), created: false }
        }
      }
      const rec = dt.createRecordWith(
        { name: op.date, type: 'markdown' }, { in: group })
      // Match create-daily-note.sh's skeleton exactly, trailing-space bullet included.
      const text = '# ' + op.heading + '\n\n- \n'
      rec.plainText = text
      rec.tags = ['Daily Note']
      return { uuid: rec.uuid(), text: text, created: true }
    },

    // On-demand backfill: creating a Place or Event only affects facts
    // filed afterwards, so this re-runs entity auto-linking over every
    // existing entity record's bullet lines. Fact identity is unchanged —
    // factSignature flattens links. Invoke manually:
    //   echo '{"ops":[{"op":"relink_entities"}]}' > /tmp/ops.json &&
    //   osascript -l JavaScript entity-dt-bridge.js /tmp/ops.json
    relink_entities() {
      let records = 0
      let changedRecords = 0
      for (const path of [PEOPLE_PATH, PLACES_PATH, EVENTS_PATH]) {
        let group
        try { group = groupAt(path) } catch (e) { continue }
        for (const rec of group.children()) {
          if (String(rec.type()) !== 'markdown') continue
          records++
          const uuid = rec.uuid()
          const lines = rec.plainText().split('\n')
          let changed = false
          for (let i = 0; i < lines.length; i++) {
            if (!/^- /.test(lines[i])) continue
            const linked = linkEntities(lines[i], uuid)
            if (linked !== lines[i]) {
              lines[i] = linked
              changed = true
            }
          }
          if (changed) {
            rec.plainText = lines.join('\n')
            changedRecords++
          }
        }
      }
      return { records: records, changed: changedRecords }
    },

    // Replace (or append, or remove on empty content) one generated `##`
    // section — the header line through the next `##` header — against the
    // record's LATEST body, read and written inside this single bridge
    // invocation. The old flow read the whole body, edited it in Python,
    // and wrote it back seconds later; a jot inserted in between was lost.
    // The write is skipped when the section is already byte-identical, so
    // refresh retries don't churn sync.
    upsert_section(op) {
      const rec = byUuid(op.uuid)
      const lines = rec.plainText().split('\n')
      const content = String(op.content || '')
      let start = -1
      for (let i = 0; i < lines.length; i++) {
        if (lines[i].trim() === op.header) { start = i; break }
      }
      if (start === -1) {
        if (!content.trim()) {
          return { uuid: op.uuid, changed: false, replaced: false }
        }
        let out = lines.slice()
        // Jots are inserted relative to the last bullet BEFORE this header
        // (see insert-jot-into-daily-note.py); generated sections must sit
        // after it, so guarantee it exists.
        if (out.map(l => l.trim()).indexOf(NOTES_SECTION) === -1) {
          while (out.length && out[out.length - 1].trim() === '') out.pop()
          out = out.concat(['', NOTES_SECTION])
        }
        while (out.length && out[out.length - 1].trim() === '') out.pop()
        rec.plainText =
          out.concat(['', op.header, ''], content.split('\n')).join('\n') + '\n'
        return { uuid: op.uuid, changed: true, replaced: false }
      }
      let end = lines.length
      for (let i = start + 1; i < lines.length; i++) {
        if (/^##\s/.test(lines[i].trim())) { end = i; break }
      }
      if (!content.trim()) {
        const out = lines.slice(0, start).concat(lines.slice(end))
        while (out.length && out[out.length - 1].trim() === '') out.pop()
        rec.plainText = out.join('\n') + '\n'
        return { uuid: op.uuid, changed: true, replaced: true, removed: true }
      }
      let spanEnd = end
      while (spanEnd > start && lines[spanEnd - 1].trim() === '') spanEnd--
      const section = [op.header, ''].concat(content.split('\n'))
      if (lines.slice(start, spanEnd).join('\n') === section.join('\n')) {
        return { uuid: op.uuid, changed: false, replaced: true }
      }
      const out = lines.slice(0, start)
        .concat(section, [''], lines.slice(end))
      rec.plainText = out.join('\n')
      return { uuid: op.uuid, changed: true, replaced: true }
    },

    trash(op) {
      dt.move({ record: byUuid(op.uuid), to: db.trashGroup() })
      return { uuid: op.uuid }
    },
  }

  const results = []
  for (let i = 0; i < ops.length; i++) {
    const op = ops[i]
    try {
      const handler = handlers[op.op]
      if (!handler) throw new Error('unknown op: ' + op.op)
      results.push(handler(op))
    } catch (e) {
      return JSON.stringify({
        ok: false,
        error: String(e.message || e),
        failed_op: i,
        results: results,
      })
    }
  }
  return JSON.stringify({ ok: true, results: results })
}
