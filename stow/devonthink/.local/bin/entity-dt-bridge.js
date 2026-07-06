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
//
// Ops:
//   dump_people        {include_bodies?}                -> [{uuid,name,aliases,md,body?}]
//   list_sources       {}                               -> [{uuid,name,kind,eventdate}]
//   get_source         {uuid}                            -> {uuid,name,kind,eventdate,...}
//   list_group         {path}                           -> [{uuid,name}]
//   get_text           {uuid}                           -> {uuid,text}
//   set_text           {uuid,text}                      -> {uuid}
//   chat               {prompt,role}                    -> {text}
//   ensure_person      {name,aliases?,fields?,log_lines?} -> {uuid,created}
//   ensure_event       {name,date,location?,attendees?,summary?,source_uuid}
//                                                       -> {uuid,created}
//   append_log         {uuid,lines}                     -> {uuid,appended,skipped}
//   set_field          {uuid,field,value}               -> {uuid,changed,previous}
//   bump_lastcontact   {uuid,date}                      -> {uuid,changed}
//   mark_filed         {uuid}                           -> {uuid}
//   create_record      {name,path,text,fields?,tags?}   -> {uuid}
//   get_or_create_daily {date,heading}                  -> {uuid,text,created}
//   trash              {uuid}                           -> {uuid}

ObjC.import('Foundation')

const DB_NAME = 'Lorebook'
const ENTITIES_PATH = '/20_ENTITIES'
const PEOPLE_PATH = ENTITIES_PATH + '/People'
const PLACES_PATH = ENTITIES_PATH + '/Places'
const EVENTS_PATH = ENTITIES_PATH + '/Events'
const DAILY_PATH = '/10_DAILY'
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
      '(^|[^\\w\\[])(' + escapeRe(e.name) + ')(?![\\w\\]])')
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

// Drop lines whose source item-link already appears in the body, link known
// entities in the remainder, then insert under the given section header.
function appendLogLines(rec, lines, section) {
  const body = rec.plainText()
  const uuid = rec.uuid()
  const fresh = []
  let skipped = 0
  for (const line of lines || []) {
    const src = (line.match(/x-devonthink-item:\/\/([0-9A-Fa-f-]+)/) || [])[1]
    if (src && body.indexOf(src) !== -1) { skipped++; continue }
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
  const db = dt.databases.byName(DB_NAME)

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
          added: added
            ? new Date(added.getTime() - added.getTimezoneOffset() * 60000)
                .toISOString().slice(0, 10)
            : '',
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
      return out
    },

    // Classify one record the same way list_sources buckets its hits, so
    // --force can target a record the database sweep never surfaces.
    get_source(op) {
      const r = byUuid(op.uuid)
      const hw = mdValue(r, 'handwritten')
      let kind = 'other'
      if (String(r.location() || '').indexOf(DAILY_PATH) === 0) kind = 'daily'
      else if (hw === '1' || hw === 'true') kind = 'handwritten'
      else if (mdValue(r, 'documenttype').indexOf('Meeting') !== -1) kind = 'meeting'
      const added = r.additionDate()
      return {
        uuid: r.uuid(),
        name: r.name(),
        kind: kind,
        eventdate: mdValue(r, 'eventdate'),
        participants: mdValue(r, 'granolaparticipants'),
        added: added
          ? new Date(added.getTime() - added.getTimezoneOffset() * 60000)
              .toISOString().slice(0, 10)
          : '',
      }
    },

    list_group(op) {
      return groupAt(op.path).children().map(r => ({ uuid: r.uuid(), name: r.name() }))
    },

    get_text(op) {
      const r = byUuid(op.uuid)
      return { uuid: op.uuid, text: r.plainText() }
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
        if (value) dt.addCustomMetaData(value, { for: field, to: rec })
      }
      if ((op.log_lines || []).length) {
        appendLogLines(rec, op.log_lines)
      }
      return { uuid: rec.uuid(), created: created }
    },

    ensure_event(op) {
      const hits = findByNameOrAlias(EVENTS_PATH, op.name)
      if (hits.length > 0) {
        const rec = hits[0]
        if (op.summary && op.source_uuid) {
          appendLogLines(rec, [
            '- ' + op.date + ' — ' + op.summary +
            ' ([source](x-devonthink-item://' + op.source_uuid + '))',
          ], EVENT_LOG_SECTION)
        }
        return { uuid: rec.uuid(), created: false }
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
      const rec = dt.createRecordWith(
        { name: op.name, type: 'markdown' }, { in: groupAt(EVENTS_PATH) })
      rec.plainText = lines.join('\n')
      entityIndex = null
      dt.addCustomMetaData('Event', { for: 'entitytype', to: rec })
      dt.addCustomMetaData(op.date, { for: 'eventdate', to: rec })
      if (op.summary && op.source_uuid) {
        appendLogLines(rec, [
          '- ' + op.date + ' — ' + op.summary +
          ' ([source](x-devonthink-item://' + op.source_uuid + '))',
        ], EVENT_LOG_SECTION)
      }
      return { uuid: rec.uuid(), created: true }
    },

    append_log(op) {
      const rec = byUuid(op.uuid)
      const counts = appendLogLines(rec, op.lines)
      return { uuid: op.uuid, appended: counts.appended, skipped: counts.skipped }
    },

    set_field(op) {
      const rec = byUuid(op.uuid)
      const previous = mdValue(rec, op.field)
      if (previous === String(op.value)) {
        return { uuid: op.uuid, changed: false, previous: previous }
      }
      dt.addCustomMetaData(op.value, { for: op.field, to: rec })
      return { uuid: op.uuid, changed: true, previous: previous }
    },

    bump_lastcontact(op) {
      const rec = byUuid(op.uuid)
      const current = mdValue(rec, 'lastcontact')
      if (current && current >= op.date) {
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
