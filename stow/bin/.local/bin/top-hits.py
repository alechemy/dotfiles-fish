#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "mutagen",
# ]
# ///
"""Build one "Top 50 Hits of <year>" compilation per Billboard year-end chart.

Stages write state under ~/.local/state/top-hits/ and each rereads the
previous stage's output, so any stage can be rerun:

    top-hits.py chart   <year>   Wikipedia year-end Hot 100 -> charts/<year>.json
    top-hits.py resolve <year>   Qobuz candidates, scored    -> manifests/<year>.json, review/<year>.md

Overrides (applied by resolve, survive re-resolves):
    overrides/<year>.json   {"<rank>": {"qobuz_id": "123"} | {"skip": "reason"}}

Design and version policy: ~/.dotfiles/.context/top-hits-plan.md
"""

from __future__ import annotations

import argparse
import datetime as dt
import difflib
import hashlib
import html
import json
import os
import re
import statistics
import sys
import time
import tomllib
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

STATE_DIR = os.path.expanduser(os.environ.get("TOP_HITS_STATE", "~/.local/state/top-hits"))
STREAMRIP_CONFIG = os.path.expanduser("~/Library/Application Support/streamrip/config.toml")
QOBUZ_BASE_URL = "https://www.qobuz.com/api.json/0.2"
WIKI_API = "https://en.wikipedia.org/w/api.php"
USER_AGENT = "top-hits/0.1 (personal music library tooling)"
TOP_N = 50

ALLOWED_GENRES = (
    "Ambient", "Bluegrass", "Classical", "Country", "Electronic",
    "Experimental", "Folk", "Hip-Hop", "Jazz", "Lo-Fi", "Mashup",
    "Pop", "R&B", "Reggae", "Rock", "Soundtrack", "Unknown",
)

GENRE_MAP = (
    ("pop/rock", "Pop"),
    ("hip-hop", "Hip-Hop"), ("hip hop", "Hip-Hop"), ("rap", "Hip-Hop"),
    ("r&b", "R&B"), ("soul", "R&B"), ("funk", "R&B"),
    ("country", "Country"),
    ("electro", "Electronic"), ("dance", "Electronic"), ("house", "Electronic"),
    ("techno", "Electronic"), ("trance", "Electronic"),
    ("reggae", "Reggae"), ("dancehall", "Reggae"),
    ("folk", "Folk"), ("americana", "Folk"),
    ("jazz", "Jazz"),
    ("soundtrack", "Soundtrack"), ("film", "Soundtrack"),
    ("classical", "Classical"),
    ("rock", "Rock"), ("alternative", "Rock"), ("indie", "Rock"),
    ("metal", "Rock"), ("punk", "Rock"),
    ("pop", "Pop"),
)


# ----------------------------------------------------------------- utilities
def state_path(*parts):
    path = os.path.join(STATE_DIR, *parts)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return path


def read_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def write_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path)


def now_iso():
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def http_get_json(url, params, headers=None, retries=3):
    full = f"{url}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(full, headers={"User-Agent": USER_AGENT, **(headers or {})})
    delay = 2.0
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise SystemExit(f"ERROR: HTTP {e.code} from {url}; authentication failed.")
            if e.code == 429 or e.code >= 500:
                if attempt == retries - 1:
                    raise
                time.sleep(delay)
                delay *= 3
                continue
            raise
        except (urllib.error.URLError, TimeoutError):
            if attempt == retries - 1:
                raise
            time.sleep(delay)
            delay *= 3
    raise RuntimeError("unreachable")


# ------------------------------------------------------------ text normalizing
_APOS = str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"', "‐": "-", "–": "-", "—": "-"})
_FEAT_SPLIT = re.compile(r"\s+(?:featuring|feat\.?|ft\.?|with|x)\s+", re.I)
_CREDIT_SPLIT = re.compile(r"\s*(?:,|&|\band\b|\+|\bvs\.?\b|\bx\b)\s*", re.I)
_PAREN = re.compile(r"\s*[\(\[][^\)\]]*[\)\]]")
_DASH_TAIL = re.compile(r"\s+-\s+.*$")


def norm(s):
    s = html.unescape(str(s or "")).translate(_APOS)
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.lower().replace("&", " and ").replace("$", "s")
    s = re.sub(r"(?<=[a-z])!(?=[a-z])", "i", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    s = re.sub(r"\bpt\b", "part", s)
    return re.sub(r"\s+", " ", s).strip()


def base_title(s):
    s = html.unescape(str(s or "")).translate(_APOS)
    s = _PAREN.sub("", s)
    s = _DASH_TAIL.sub("", s)
    return norm(s)


def similarity(a, b):
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def split_credit(credit):
    """Return (main_names, featured_names), each a list of normalized artist names."""
    credit = html.unescape(str(credit or "")).translate(_APOS)
    parts = _FEAT_SPLIT.split(credit, maxsplit=1)
    main, feats = parts[0], parts[1] if len(parts) > 1 else ""
    main_names = [norm(p) for p in _CREDIT_SPLIT.split(main) if norm(p)]
    feat_names = [norm(p) for p in _CREDIT_SPLIT.split(feats) if norm(p)]
    return main_names, feat_names


def name_in(name, haystack):
    """True when artist `name` appears in normalized text `haystack`, fuzzily."""
    if not name or not haystack:
        return False
    if f" {name} " in f" {haystack} ":
        return True
    for chunk in re.split(r"\b(?:and|featuring|feat|ft|with|x|vs)\b|,", haystack):
        if similarity(name, chunk.strip()) >= 0.85:
            return True
    return False


# ----------------------------------------------------------------- stage: chart
def chart_page_title(year):
    return f"Billboard Year-End Hot 100 singles of {year}"


def fetch_wikitext(year):
    data = http_get_json(WIKI_API, {
        "action": "parse", "page": chart_page_title(year),
        "prop": "wikitext", "format": "json", "formatversion": "2",
    })
    if "error" in data:
        raise SystemExit(f"ERROR: Wikipedia: {data['error'].get('info')}")
    return data["parse"]["wikitext"]


_REF = re.compile(r"<ref[^>/]*/>|<ref[^>]*>.*?</ref>", re.S | re.I)
_LINK = re.compile(r"\[\[(?:[^\]|]*\|)?([^\]]*)\]\]")
_TEMPLATE = re.compile(r"\{\{([^{}]*)\}\}")
_HTML_TAG = re.compile(r"<[^>]+>")
_CELL_ATTRS = re.compile(r'^\s*(?:[\w-]+\s*=\s*(?:"[^"]*"|\S+)\s*)+\|(?!\|)')


def _expand_template(m):
    body = m.group(1)
    name, _, rest = body.partition("|")
    name = name.strip().lower()
    args = [a for a in rest.split("|") if "=" not in a]
    if name in ("sortname", "sort name") and len(args) >= 2:
        return f"{args[0].strip()} {args[1].strip()}"
    if name in ("nowrap", "sort", "hlist", "small", "lang"):
        return args[-1].strip() if args else ""
    return ""


def clean_wikitext(cell):
    cell = _REF.sub("", cell)
    for _ in range(3):
        cell = _TEMPLATE.sub(_expand_template, cell)
    cell = _LINK.sub(r"\1", cell)
    cell = _HTML_TAG.sub("", cell)
    cell = cell.replace("'''", "").replace("''", "")
    cell = html.unescape(cell).translate(_APOS)
    return re.sub(r"\s+", " ", cell).strip()


def _split_cells(row_lines):
    """Yield (attrs, text) cells from the lines of one wikitable row."""
    for line in row_lines:
        line = line.strip()
        if not line.startswith("|") or line.startswith("|-") or line.startswith("|}"):
            continue
        for raw in line[1:].split("||"):
            attrs = ""
            m = _CELL_ATTRS.match(raw)
            if m and "[[" not in raw[: m.end()]:
                attrs, raw = raw[: m.end() - 1], raw[m.end():]
            yield attrs, raw


def parse_chart(wikitext, top_n=TOP_N):
    """Return [{rank, title, artist}] for the first No./Title/Artist wikitable."""
    tables = re.findall(r"\{\|.*?\n\|\}", wikitext, re.S)
    for table in tables:
        header = " ".join(l for l in table.splitlines() if l.startswith("!")).lower()
        if "title" not in header or "artist" not in header:
            continue
        rows = re.split(r"\n\|-[^\n]*", table)
        entries, carry = [], {}
        for row in rows[1:]:
            cells = [(a, clean_wikitext(t)) for a, t in _split_cells(row.splitlines())]
            if not cells:
                continue
            values = []
            col = 0
            while len(values) < 3:
                if col in carry:
                    text, left = carry[col]
                    values.append(text)
                    if left <= 1:
                        del carry[col]
                    else:
                        carry[col] = (text, left - 1)
                elif cells:
                    attrs, text = cells.pop(0)
                    span = re.search(r'rowspan\s*=\s*"?(\d+)', attrs)
                    if span and int(span.group(1)) > 1:
                        carry[col] = (text, int(span.group(1)) - 1)
                    values.append(text)
                else:
                    break
                col += 1
            if len(values) < 3:
                continue
            rank_text, title, artist = values[:3]
            m = re.match(r"\s*(\d+)", rank_text)
            if not m:
                continue
            entries.append({"rank": int(m.group(1)), "title": title.strip('"').strip(), "artist": artist})
        if entries:
            entries = [e for e in entries if e["rank"] <= top_n]
            return entries
    raise ValueError("no chart table found")


def cmd_chart(args):
    year = args.year
    wikitext = fetch_wikitext(year)
    raw_path = state_path("charts", f"{year}.wikitext")
    with open(raw_path, "w", encoding="utf-8") as f:
        f.write(wikitext)
    entries = parse_chart(wikitext)
    ranks = [e["rank"] for e in entries]
    if ranks != list(range(1, TOP_N + 1)):
        raise SystemExit(f"ERROR: parsed ranks {ranks[:5]}…{ranks[-3:]} ({len(ranks)}) are not 1..{TOP_N}; raw saved at {raw_path}")
    source = "https://en.wikipedia.org/wiki/" + urllib.parse.quote(chart_page_title(year).replace(" ", "_"))
    out = {"year": year, "source_url": source, "retrieved_at": now_iso(), "entries": entries}
    path = state_path("charts", f"{year}.json")
    write_json(path, out)
    print(f"{year}: {len(entries)} entries -> {path}")
    for e in entries[:5]:
        print(f"  {e['rank']:>2}. {e['title']} — {e['artist']}")


# ---------------------------------------------------------------- qobuz client
class Qobuz:
    def __init__(self, config_path=STREAMRIP_CONFIG, min_interval=1.05):
        with open(config_path, "rb") as f:
            q = tomllib.load(f)["qobuz"]
        if not q.get("use_auth_token"):
            raise SystemExit("ERROR: streamrip config must use an auth token (use_auth_token = true).")
        self.headers = {"X-App-Id": str(q["app_id"]), "X-User-Auth-Token": str(q["password_or_token"])}
        self.min_interval = min_interval
        self._last = 0.0
        self.calls = 0

    def _get(self, endpoint, params):
        key = hashlib.sha256(f"{endpoint}?{urllib.parse.urlencode(sorted(params.items()))}".encode()).hexdigest()
        cache = state_path("cache", "qobuz", f"{key}.json")
        cached = read_json(cache)
        if cached is not None:
            return cached
        wait = self.min_interval - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        data = http_get_json(f"{QOBUZ_BASE_URL}/{endpoint}", params, self.headers)
        self._last = time.monotonic()
        self.calls += 1
        write_json(cache, data)
        return data

    def search_tracks(self, query, limit=50):
        data = self._get("track/search", {"query": query, "limit": limit})
        return data.get("tracks", {}).get("items", [])

    def track(self, track_id):
        return self._get("track/get", {"track_id": str(track_id)})


def candidate_from_item(item):
    album = item.get("album") or {}
    performer = (item.get("performer") or {}).get("name") or ""
    genre = (album.get("genre") or {}).get("name")
    return {
        "id": str(item["id"]),
        "title": item.get("title") or "",
        "version": item.get("version") or "",
        "performer": performer,
        "performers": item.get("performers") or "",
        "album": album.get("title") or "",
        "album_version": album.get("version") or "",
        "album_artist": (album.get("artist") or {}).get("name") or "",
        "album_id": str(album.get("id") or ""),
        "tracks_count": album.get("tracks_count") or 0,
        "duration": item.get("duration") or 0,
        "explicit": bool(item.get("parental_warning")),
        "isrc": item.get("isrc") or "",
        "hires": bool(item.get("hires")),
        "bit_depth": item.get("maximum_bit_depth"),
        "sampling_rate": item.get("maximum_sampling_rate"),
        "released": album.get("release_date_original") or item.get("release_date_original") or "",
        "streamable": bool(item.get("streamable", True)),
        "genre": genre,
        "genres_list": album.get("genres_list"),
        "url": f"https://open.qobuz.com/track/{item['id']}",
    }


_ARTIST_ROLES = re.compile(r"main ?artist|featured ?artist|\bperformer\b|\bartist\b|vocalist|remixer", re.I)


def credited_artists(performers):
    """Names from Qobuz's `performers` credit string whose roles are performing roles."""
    names = []
    for entry in re.split(r"\s+-\s+|\r\n?|\n", performers or ""):
        name, _, roles = entry.partition(",")
        if name.strip() and _ARTIST_ROLES.search(roles):
            names.append(name.strip())
    return names


# --------------------------------------------------------------- stage: resolve
JUNK = re.compile(r"karaoke|tribute|made famous|in the style of|originally performed|instrumental|cover version|as performed by|8[- ]bit|lullab|music box|ringtone|kidz bop|glee cast|spieluhr|for babies|sleep|workout|fitness|parody", re.I)
EDIT = re.compile(r"radio (?:edit|version|mix)|\bclean\b|\bedited\b|single (?:version|edit|mix)|\bedit\b", re.I)
LIVE = re.compile(r"\blive\b|acoustic|unplugged|\bdemo\b|a ?cappella|acapella|orchestral|piano version|stripped|symphonic", re.I)
SPEED = re.compile(r"sped[- ]up|slowed|nightcore|reverb", re.I)
REMIX = re.compile(r"remix|rework|re-?edit|\b(?:club|dub|extended|radio|dance) mix\b|\bmixes\b|extended", re.I)
REMASTER = re.compile(r"remaster", re.I)
NEUTRAL_VERSION = re.compile(r"album version|\bmain\b|original|explicit|clean|feat|featuring|with |duet|stereo|mono|bonus|deluxe|single|edition|version", re.I)
ALBUM_VERSION = re.compile(r"album version|main version|original version|original mix", re.I)
COMPILATION_TITLE = re.compile(r"greatest hits|best of|the hits|hits\b|collection|anthology|essential|now that|the very best|number ones|#1s|playlist|ultimate|complete|years of|decade|classics", re.I)


def score_candidate(cand, entry_ctx, any_explicit, median_duration):
    """Return (score, penalties, hard_reject_reason) for one Qobuz candidate."""
    chart_year = entry_ctx["year"]
    main_names, feat_names = entry_ctx["main_names"], entry_ctx["feat_names"]
    chart_is_remix = bool(REMIX.search(entry_ctx["chart_title"]))

    if not cand["streamable"]:
        return 0, [], "not_streamable"
    blob = " ".join([cand["title"], cand["version"], cand["album"], cand["album_version"], cand["performer"], cand["album_artist"]])
    if JUNK.search(blob):
        return 0, [], "junk"

    artist_credits = norm(" , ".join([cand["performer"], cand["album_artist"], *credited_artists(cand["performers"])]))
    if not any(name_in(n, artist_credits) for n in main_names):
        return 0, [], "artist_mismatch"

    title_sim = title_similarity(entry_ctx, cand["title"])
    if title_sim < 0.85:
        return 0, [], "title_mismatch"

    penalties = []
    title_extras = " ".join(_PAREN.findall(cand["title"])) + " " + (cand["title"].split(" - ", 1)[1] if " - " in cand["title"] else "")
    version_blob = f"{title_extras} {cand['version']}"
    album_blob = f"{cand['album']} {cand['album_version']}"
    if EDIT.search(version_blob):
        penalties.append(("edit", 40))
    if LIVE.search(version_blob):
        penalties.append(("live", 60))
    if SPEED.search(version_blob):
        penalties.append(("speed", 60))
    if LIVE.search(album_blob) or SPEED.search(album_blob):
        penalties.append(("live_album", 30))
    feature_credits = norm(" , ".join([artist_credits, cand["title"], cand["version"]]))
    missing_feats = [n for n in feat_names if not name_in(n, feature_credits)]
    covers_all_feats = bool(feat_names) and not missing_feats
    remix_waived = chart_is_remix or (entry_ctx.get("remix_needed") and covers_all_feats)
    if REMIX.search(album_blob) and not remix_waived:
        penalties.append(("remix_album", 30))
    if REMIX.search(version_blob) and not remix_waived:
        penalties.append(("remix", 35))
    if missing_feats:
        penalties.append(("missing_feature", 25))
    if cand["version"] and not any(rx.search(cand["version"]) for rx in (NEUTRAL_VERSION, EDIT, LIVE, SPEED, REMIX, REMASTER)):
        penalties.append(("unknown_version", 20))

    if any_explicit and not cand["explicit"]:
        penalties.append(("clean_when_explicit_exists", 20))

    album_artist_n = norm(cand["album_artist"])
    own_album = any(name_in(n, album_artist_n) for n in main_names)
    if album_artist_n in ("various artists", "various") or COMPILATION_TITLE.search(cand["album"]):
        penalties.append(("compilation", 15 if own_album else 20))
    elif cand["tracks_count"] and cand["tracks_count"] <= 3:
        penalties.append(("single", 8))
    elif not own_album:
        penalties.append(("other_artist_album", 10))

    year_m = re.match(r"(\d{4})", cand["released"] or "")
    rel_year = int(year_m.group(1)) if year_m else None
    if rel_year and rel_year > chart_year:
        penalties.append(("later_release", min(30, 6 * (rel_year - chart_year))))
    if REMASTER.search(f"{version_blob} {cand['album_version']}"):
        penalties.append(("remaster", 5))
    if median_duration and cand["duration"] and abs(cand["duration"] - median_duration) > 25:
        penalties.append(("duration_outlier", 10))

    score = 100 - sum(p for _, p in penalties)
    if own_album and cand["tracks_count"] > 3 and not COMPILATION_TITLE.search(cand["album"]):
        score += 10
    if ALBUM_VERSION.search(cand["version"]):
        score += 5
    if cand["explicit"]:
        score += 5
    if cand["hires"]:
        score += 2
    if rel_year and chart_year - 1 <= rel_year <= chart_year:
        score += 3
    score += round(10 * (title_sim - 0.85))
    return score, penalties, None


def title_similarity(entry_ctx, cand_title):
    chart_forms = (entry_ctx["chart_base"], entry_ctx["chart_full"])
    cand_forms = (base_title(cand_title), norm(cand_title))
    return max(similarity(a, b) for a in chart_forms for b in cand_forms)


REVIEW_PENALTIES = {"edit", "live", "speed", "live_album", "remix", "remix_album", "missing_feature", "compilation", "other_artist_album", "unknown_version"}
VERSION_PENALTIES = {"edit", "live", "speed", "live_album", "remix", "remix_album", "unknown_version"}


def resolve_entry(entry, year, candidates):
    main_names, feat_names = split_credit(entry["artist"])
    ctx = {
        "year": year, "main_names": main_names, "feat_names": feat_names,
        "chart_title": entry["title"], "chart_base": base_title(entry["title"]), "chart_full": norm(entry["title"]),
    }
    seen, cands = set(), []
    for item in candidates:
        c = candidate_from_item(item)
        if c["id"] in seen:
            continue
        seen.add(c["id"])
        cands.append(c)

    first_pass = []
    for c in cands:
        _, pens, reject = score_candidate(c, ctx, any_explicit=False, median_duration=None)
        if reject is None:
            first_pass.append((c, {name for name, _ in pens}))
    any_explicit = any(c["explicit"] for c, pens in first_pass if not pens & VERSION_PENALTIES)
    original_covers_feats = any(not pens & {"remix", "remix_album", "missing_feature"} for _, pens in first_pass)
    ctx["remix_needed"] = bool(feat_names) and not original_covers_feats
    first_pass = [c for c, _ in first_pass]
    durations = [c["duration"] for c in first_pass if c["duration"]]
    median_duration = statistics.median(durations) if durations else None

    scored = []
    rejects = {}
    for c in cands:
        s, pens, reject = score_candidate(c, ctx, any_explicit, median_duration)
        if reject:
            rejects[reject] = rejects.get(reject, 0) + 1
            continue
        scored.append((s, pens, c))
    scored.sort(key=lambda t: (-t[0], -(t[2]["tracks_count"] or 0), t[2]["id"]))

    result = {
        "rank": entry["rank"], "chart_title": entry["title"], "chart_artist": entry["artist"],
        "status": "unresolved", "flags": [], "confidence": None, "skip_reason": None,
        "qobuz": None, "genre": None, "alternatives": [], "rejected": rejects,
    }
    if not scored:
        result["flags"].append("no_candidates")
        return result

    top_score, top_pens, top = scored[0]
    flags = [name for name, _ in top_pens if name in REVIEW_PENALTIES]
    if top_score < 50:
        flags.append("low_score")
    if title_similarity(ctx, top["title"]) < 0.95:
        flags.append("title_fuzzy")
    gap = None
    top_pen_names = {name for name, _ in top_pens}
    for s, pens, c in scored[1:]:
        if same_recording(top, c) or not {name for name, _ in pens} <= top_pen_names:
            continue
        gap = top_score - s
        break
    if gap is not None and gap < 8:
        flags.append("close_call")

    result.update({
        "status": "review" if flags else "auto",
        "flags": flags,
        "confidence": gap,
        "qobuz": top,
        "score": top_score,
        "penalties": top_pens,
        "alternatives": [
            {"id": c["id"], "score": s, "title": c["title"], "version": c["version"], "album": c["album"],
             "album_artist": c["album_artist"], "released": c["released"], "explicit": c["explicit"],
             "duration": c["duration"], "isrc": c["isrc"], "penalties": [n for n, _ in pens], "url": c["url"]}
            for s, pens, c in scored[1:4]
        ],
    })
    return result


def same_recording(a, b):
    if a["isrc"] and b["isrc"] and a["isrc"] == b["isrc"]:
        return True
    return (norm(a["performer"]) == norm(b["performer"]) and base_title(a["title"]) == base_title(b["title"])
            and abs((a["duration"] or 0) - (b["duration"] or 0)) <= 3)


def map_genre(qobuz_genre):
    """Return (library_genre, mapped_flag)."""
    g = (qobuz_genre or "").lower()
    for needle, target in GENRE_MAP:
        if needle in g:
            return target, True
    return "Pop", False


def enrich_from_track_get(row, qb):
    """Refresh the chosen candidate from track/get, which carries the leaf genre."""
    data = qb.track(row["qobuz"]["id"])
    if "album" in data:
        fresh = candidate_from_item(data)
        fresh["url"] = row["qobuz"]["url"]
        row["qobuz"] = fresh
    genre, mapped = map_genre(row["qobuz"].get("genre"))
    row["genre"] = genre
    if not mapped and "genre_unmapped" not in row["flags"]:
        row["flags"].append("genre_unmapped")


def apply_override(row, override, qb):
    if "skip" in override:
        row.update({"status": "skip", "skip_reason": override["skip"], "qobuz": None, "genre": None})
        return
    if "qobuz_id" in override:
        row["qobuz"] = {"id": str(override["qobuz_id"]), "url": f"https://open.qobuz.com/track/{override['qobuz_id']}"}
        row["flags"] = []
        enrich_from_track_get(row, qb)
        row["status"] = "manual"


def build_queries(entry):
    main = html.unescape(entry["artist"]).translate(_APOS)
    main = _FEAT_SPLIT.split(main, maxsplit=1)[0]
    first = _CREDIT_SPLIT.split(main)[0].strip() or main
    title = _PAREN.sub("", html.unescape(entry["title"]).translate(_APOS)).strip()
    queries = [f"{first} {title}"]
    if first != main.strip():
        queries.append(f"{main.strip()} {title}")
    queries.append(title)
    return queries


def search_entry(qb, entry, year, min_accepted=3):
    """Run successive queries until enough non-rejected candidates exist; return (query_used, items)."""
    items, used = [], []
    for q in build_queries(entry):
        items.extend(qb.search_tracks(q))
        used.append(q)
        probe = resolve_entry(entry, year, items)
        accepted = 1 + len(probe["alternatives"]) if probe["qobuz"] else 0
        if accepted >= min_accepted:
            break
    return " | ".join(used), items


def cmd_resolve(args):
    year = args.year
    chart = read_json(state_path("charts", f"{year}.json"))
    if not chart:
        raise SystemExit(f"ERROR: no chart for {year}; run `top-hits.py chart {year}` first.")
    overrides = read_json(state_path("overrides", f"{year}.json"), default={})
    qb = Qobuz()
    rows = []
    for entry in chart["entries"]:
        if args.rank and entry["rank"] not in args.rank:
            continue
        query, items = search_entry(qb, entry, year)
        row = resolve_entry(entry, year, items)
        row["query"] = query
        override = overrides.get(str(entry["rank"]))
        if override:
            apply_override(row, override, qb)
        elif row["qobuz"]:
            enrich_from_track_get(row, qb)
        rows.append(row)
        pick = row["qobuz"]
        label = f"{pick['title']} · {pick['album']} ({(pick['released'] or '')[:4]})" if pick else (row["skip_reason"] or "—")
        print(f"  {entry['rank']:>2}. [{row['status']:<10}] {entry['title']} — {entry['artist']}  =>  {label}"
              + (f"  [{', '.join(row['flags'])}]" if row["flags"] else ""))

    manifest = {
        "year": year, "album": f"Top 50 Hits of {year}", "album_artist": "Various Artists",
        "chart_source_url": chart["source_url"], "chart_retrieved_at": chart["retrieved_at"],
        "resolved_at": now_iso(), "entries": rows,
    }
    if args.rank:
        existing = read_json(state_path("manifests", f"{year}.json"))
        if existing:
            by_rank = {r["rank"]: r for r in existing["entries"]}
            by_rank.update({r["rank"]: r for r in rows})
            manifest["entries"] = [by_rank[k] for k in sorted(by_rank)]
    path = state_path("manifests", f"{year}.json")
    write_json(path, manifest)
    review = write_review(manifest)
    counts = {}
    for r in manifest["entries"]:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    print(f"\n{year}: {counts}  api_calls={qb.calls}\n  manifest: {path}\n  review:   {review}")


def _fmt_dur(seconds):
    return f"{int(seconds) // 60}:{int(seconds) % 60:02d}" if seconds else "?"


def write_review(manifest):
    year = manifest["year"]
    lines = [f"# {manifest['album']} — resolve review", "",
             f"Chart: {manifest['chart_source_url']} (retrieved {manifest['chart_retrieved_at'][:10]}); resolved {manifest['resolved_at'][:16]}Z.", "",
             "Override a pick with `overrides/" + f"{year}.json`: " + '`{"<rank>": {"qobuz_id": "<id>"}}` or `{"<rank>": {"skip": "<reason>"}}`, then rerun `top-hits.py resolve ' + f"{year}`.", "",
             "| # | Chart | Pick | Album (year) | Ver | Expl | Dur | Genre | Status |", "|--:|---|---|---|---|:-:|--:|---|---|"]
    for r in manifest["entries"]:
        q = r["qobuz"]
        chart = f"{r['chart_title']} — {r['chart_artist']}"
        if q:
            pick = f"[{q['title']} — {q['performer']}]({q['url']})"
            album = f"{q['album']} ({(q['released'] or '')[:4]})"
            ver = q.get("version") or ""
            expl = "E" if q.get("explicit") else ""
            dur = _fmt_dur(q.get("duration"))
            genre = f"{r['genre']} ← {q.get('genre') or '?'}"
        else:
            pick = album = ver = expl = dur = genre = ""
        status = r["status"] + (f" ({', '.join(r['flags'])})" if r["flags"] else "") + (f": {r['skip_reason']}" if r.get("skip_reason") else "")
        lines.append(f"| {r['rank']} | {chart} | {pick} | {album} | {ver} | {expl} | {dur} | {genre} | {status} |")
    flagged = [r for r in manifest["entries"] if r["status"] in ("review", "unresolved")]
    if flagged:
        lines += ["", "## Flagged rows — alternatives", ""]
        for r in flagged:
            lines.append(f"**{r['rank']}. {r['chart_title']} — {r['chart_artist']}** ({', '.join(r['flags'])}; rejected: {r.get('rejected')})")
            if r["qobuz"]:
                q = r["qobuz"]
                lines.append(f"- pick `{q['id']}` score {r.get('score')}: {q['title']} [{q.get('version') or ''}] · {q['album']} ({(q['released'] or '')[:4]}) · {q['album_artist']} · {_fmt_dur(q['duration'])} · {'E' if q['explicit'] else 'clean'} · penalties {[n for n, _ in r.get('penalties', [])]}")
            for a in r["alternatives"]:
                lines.append(f"- alt `{a['id']}` score {a['score']}: {a['title']} [{a['version']}] · {a['album']} ({(a['released'] or '')[:4]}) · {a['album_artist']} · {_fmt_dur(a['duration'])} · {'E' if a['explicit'] else 'clean'} · penalties {a['penalties']}")
            lines.append("")
    path = state_path("review", f"{year}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return path


# ------------------------------------------------------------------------ main
def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("chart", help="Fetch and parse the Billboard year-end top 50.")
    p.add_argument("year", type=int)
    p.set_defaults(func=cmd_chart)
    p = sub.add_parser("resolve", help="Match chart entries to Qobuz tracks.")
    p.add_argument("year", type=int)
    p.add_argument("--rank", type=int, nargs="*", help="Only re-resolve these ranks (merged into the manifest).")
    p.set_defaults(func=cmd_resolve)
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
