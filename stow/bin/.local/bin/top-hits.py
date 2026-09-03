#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "mutagen",
#   "pillow",
# ]
# ///
"""Build one "Top 50 Hits of <year>" compilation per Billboard year-end chart.

Stages write state under ~/.local/state/top-hits/ and each rereads the
previous stage's output, so any stage can be rerun:

    top-hits.py chart    <year>  Wikipedia year-end Hot 100 -> charts/<year>.json
    top-hits.py resolve  <year>  Qobuz candidates, scored    -> manifests/<year>.json, review/<year>.md
    top-hits.py approve  <year>  pin every review-status pick (or --rank N ...) into overrides, then re-resolve
    top-hits.py download <year>  one rip per rank into ~/StreamripDownloads/top-hits/<year>/<rank>/,
                                 verified by tag title + duration -> progress/<year>.json
                                 exit 0 complete, 1 ranks still missing, 2 ABORT (auth/rate limit: stop
                                 the whole run), 3 HALT (3 consecutive failures: wait, then rerun)
    top-hits.py assemble <year>  tag as one compilation, generated cover, music-organize into the
                                 library, NAS chmod, runnability scoring
    top-hits.py status --years 2008-2025 [--require resolved|downloaded|assembled]
                                 exit 0 only when every year meets the requirement
    top-hits.py run --years 2012-2025
                                 unattended driver: per year download (retrying transient halts) then
                                 assemble; stops the whole run on ABORT; writes run-report.md

Overrides (applied by resolve, survive re-resolves):
    overrides/<year>.json   {"<rank>": {"qobuz_id": "123"} | {"skip": "reason"}}

Design and version policy: ~/.dotfiles/.context/top-hits-plan.md
"""

from __future__ import annotations

import argparse
import datetime as dt
import difflib
import fcntl
import glob
import hashlib
import html
import json
import os
import re
import shutil
import statistics
import subprocess
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
DOWNLOADS_DIR = os.path.expanduser(os.environ.get("TOP_HITS_DOWNLOADS", "~/StreamripDownloads/top-hits"))
LOCAL_RIP = os.path.expanduser("~/Developer/streamrip/.venv/bin/rip")
LIBRARY_ROOT = "/Volumes/Media/Music"
ORGANIZER = os.path.expanduser("~/.local/bin/music-organize.py")
RUNNABILITY = os.path.expanduser("~/.local/bin/runnability.py")
NAS_HOSTS = ("admin@192.168.50.54", "admin@100.89.43.9")
COVER_FONTS = (
    "/System/Library/Fonts/Supplemental/DIN Condensed Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
)

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
_FEAT_SPLIT = re.compile(r"\s+(?:featuring|feat\.?|ft\.?|with)\s+", re.I)
_CREDIT_SPLIT = re.compile(r"\s*(?:,|&|\band\b|\bvs\.?\b)\s*", re.I)
_ACRONYM = re.compile(r"(?<![\w.])((?:\w\.){2,})")
ARTIST_ALIASES = {
    "machine gun kelly": ("mgk",),
    "puff daddy": ("p diddy", "diddy"),
    "p diddy": ("diddy", "puff daddy"),
}
_PAREN = re.compile(r"\s*(?:\([^()]*\)|\[[^\[\]]*\])")
_DASH_TAIL = re.compile(r"\s+-\s+.*$")


def norm(s):
    s = html.unescape(str(s or "")).translate(_APOS)
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = _ACRONYM.sub(lambda m: m.group(1).replace(".", ""), s)
    s = s.lower().replace("&", " and ").replace("$", "s")
    s = re.sub(r"(?<=[a-z])!(?=[a-z])", "i", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    s = re.sub(r"\bpt\b", "part", s)
    return re.sub(r"\s+", " ", s).strip()


def strip_parens(s):
    while True:
        stripped = _PAREN.sub("", s)
        if stripped == s:
            return re.sub(r"\s*[\(\[].*$", "", s)
        s = stripped


def base_title(s):
    s = html.unescape(str(s or "")).translate(_APOS)
    s = strip_parens(s)
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
    if len(main_names) > 1 and norm(main) not in main_names:
        main_names.insert(0, norm(main))
    feat_names = [norm(p) for p in _CREDIT_SPLIT.split(feats) if norm(p)]
    return main_names, feat_names


def artist_variants(name):
    return (name, *ARTIST_ALIASES.get(name, ()))


CREDIT_SEP = " zzsep "


def join_credits(values):
    """Normalized credit fields joined by a separator that survives norm()."""
    return CREDIT_SEP.join(norm(v) for v in values if v and norm(v))


def name_in(name, haystack):
    """True when artist `name` is one whole credit in `haystack` (a join_credits string), fuzzily.

    A credit that merely contains the name inside a longer one ("X Cover Band", "X Piano") is not a match.
    """
    if not name or not haystack:
        return False
    for variant in artist_variants(name):
        words = variant.split()
        for field in haystack.split(CREDIT_SEP.strip()):
            field = field.strip()
            if not field:
                continue
            candidates = [field, *re.split(r"\b(?:and|featuring|feat|ft|with|vs)\b", field)]
            for chunk in candidates:
                chunk = chunk.strip()
                if not chunk:
                    continue
                if chunk == variant or similarity(variant, chunk) >= 0.85:
                    return True
                if len(words) >= 3 and chunk.split() == words[: len(chunk.split())] and len(chunk.split()) >= 2:
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
            if '" / "' in title:
                title = title.split('" / "')[0]
                artist = artist.split(" / ")[0]
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
JUNK = re.compile(r"karaoke|tribute|made famous|made popular|in the style of|originally performed|originally by|instrumental|\bcovers?\b|cover band|as performed by|8[- ]bit|lullab|music box|ringtone|kidz bop|glee cast|spieluhr|for babies|sleep|workout|fitness|parody|piano (?:pop|lounge|version)|lounge|string quartet|orchestra", re.I)
EDIT = re.compile(r"radio (?:edit|version|mix)|\bclean\b|\bedited\b|single (?:version|edit|mix)|\bedit\b", re.I)
LIVE = re.compile(r"\blive\b|acoustic|unplugged|\bdemo\b|a ?cappella|acapella|orchestral|piano version|stripped|symphonic", re.I)
SPEED = re.compile(r"sped[- ]up|slowed|nightcore|reverb", re.I)
REMIX = re.compile(r"remix|rework|re-?edit|\b(?:club|dub|extended|radio|dance) mix\b|\bmixes\b|extended", re.I)
REMASTER = re.compile(r"remaster", re.I)
NEUTRAL_VERSION = re.compile(r"album version|\bmain\b|original|explicit|clean|feat|featuring|with |duet|stereo|mono|bonus|deluxe|single|edition|version", re.I)
ALBUM_VERSION = re.compile(r"album version|main version|original version|original mix", re.I)
COMPILATION_TITLE = re.compile(r"greatest hits|best of|the hits|hits\b|collection|anthology|essential|now that|the very best|number ones|#1s|playlist|ultimate|years of|decade|classics|throwbacks|bangers", re.I)


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

    artist_credits = join_credits([cand["performer"], cand["album_artist"], *credited_artists(cand["performers"])])
    if not any(name_in(n, artist_credits) for n in main_names):
        return 0, [], "artist_mismatch"

    title_sim = title_similarity(entry_ctx, cand["title"])
    if title_sim < 0.85:
        return 0, [], "title_mismatch"

    penalties = []
    leftover = title_leftover(entry_ctx, cand["title"])
    title_extras = leftover if leftover is not None else cand["title"].replace(strip_parens(cand["title"]), "", 1)
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
    feature_credits = CREDIT_SEP.join([artist_credits, join_credits([cand["title"], cand["version"]])])
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
    elif re.search(r"\bversion\b|\bmix\b", version_blob, re.I) and not ALBUM_VERSION.search(version_blob) \
            and not any(rx.search(version_blob) for rx in (EDIT, LIVE, SPEED, REMIX, REMASTER)):
        penalties.append(("alt_version", 15))

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


MARKER_LEFTOVER = re.compile(r"^(?:(?:feat|featuring|ft|with)\b.*|(?:\S+\s+){0,2}?(?:remix|remixes|version|edit|mix|live|acoustic|remaster(?:ed)?|explicit|clean)\b.*)$")


def title_leftover(entry_ctx, cand_title):
    """What remains of the candidate title once the charted title is removed from its start, or None."""
    for chart_form in (entry_ctx["chart_full"], entry_ctx["chart_base"]):
        for cand_form in (norm(cand_title), base_title(cand_title)):
            if cand_form == chart_form:
                return ""
            if cand_form.startswith(chart_form + " "):
                return cand_form[len(chart_form) + 1:]
    return None


def censored_match(entry_ctx, cand_title):
    """True when an asterisk-censored candidate title matches the charted title."""
    if "*" not in cand_title:
        return False
    pattern = re.escape(strip_parens(cand_title).lower()).replace(r"\*", "*")
    pattern = re.sub(r"\*+", r"[a-z]+", pattern)
    return re.fullmatch(pattern, strip_parens(entry_ctx["chart_title"]).lower()) is not None


def title_similarity(entry_ctx, cand_title):
    chart_forms = (entry_ctx["chart_base"], entry_ctx["chart_full"])
    cand_forms = (base_title(cand_title), norm(cand_title))
    best = max(similarity(a, b) for a in chart_forms for b in cand_forms)
    if censored_match(entry_ctx, cand_title):
        best = max(best, 0.99)
    leftover = title_leftover(entry_ctx, cand_title)
    if leftover and MARKER_LEFTOVER.match(leftover):
        best = max(best, 0.97)
    return best


REVIEW_PENALTIES = {"edit", "live", "speed", "live_album", "remix", "remix_album", "missing_feature", "compilation", "other_artist_album", "unknown_version", "alt_version"}
VERSION_PENALTIES = {"edit", "live", "speed", "live_album", "remix", "remix_album", "unknown_version", "alt_version"}


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
        row.update({"status": "skip", "skip_reason": override["skip"], "qobuz": None, "genre": None, "flags": []})
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
    title = strip_parens(html.unescape(entry["title"]).translate(_APOS)).strip()
    queries = [f"{first} {title}"]
    if first != main.strip():
        queries.append(f"{main.strip()} {title}")
    queries.append(title)
    return queries


def search_entry(qb, entry, year, min_accepted=3):
    """Run successive queries until enough non-rejected candidates exist; return (query_used, items)."""
    items, used = [], []
    queries = build_queries(entry)
    for q in queries:
        items.extend(qb.search_tracks(q))
        used.append(q)
        probe = resolve_entry(entry, year, items)
        accepted = 1 + len(probe["alternatives"]) if probe["qobuz"] else 0
        if accepted >= min_accepted:
            break
    probe = resolve_entry(entry, year, items)
    if probe["qobuz"] and {n for n, _ in probe.get("penalties", [])} & (VERSION_PENALTIES | {"compilation", "other_artist_album", "missing_feature"}):
        items.extend(qb.search_tracks(queries[0], limit=200))
        used.append(f"{queries[0]} (wide)")
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



# --------------------------------------------------------------- stage: approve
def cmd_approve(args):
    year = args.year
    manifest = load_manifest(year)
    path = state_path("overrides", f"{year}.json")
    overrides = read_json(path, default={})
    pinned = []
    for row in manifest["entries"]:
        if row["status"] != "review" or (args.rank and row["rank"] not in args.rank) or not row["qobuz"]:
            continue
        overrides[str(row["rank"])] = {"qobuz_id": row["qobuz"]["id"], "approved_from": row["flags"]}
        pinned.append(row["rank"])
    if not pinned:
        print(f"{year}: nothing to approve.")
        return
    write_json(path, overrides)
    print(f"{year}: pinned ranks {pinned} in {path}; re-resolving.")
    cmd_resolve(argparse.Namespace(year=year, rank=None))


# -------------------------------------------------------------- stage: download
FATAL_RIP = re.compile(r"\b(?:401|403|429)\b|unauthori|authenticat|invalid app|rate limit|too many requests|login failed", re.I)
READY_STATUSES = {"auto", "manual", "skip"}


def year_lock(year, stage):
    """Exclusive per-year lock; a second download/assemble for the same year exits instead of racing."""
    handle = open(state_path("locks", f"{year}.lock"), "a+")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.seek(0)
        holder = handle.read().strip()
        raise SystemExit(f"ERROR: another top-hits process holds {year} ({holder or 'unknown'}); refusing to run {stage} concurrently.")
    handle.seek(0)
    handle.truncate()
    handle.write(f"{stage} pid {os.getpid()} since {now_iso()}")
    handle.flush()
    return handle


def library_album_dir(manifest):
    return os.path.join(LIBRARY_ROOT, "Compilations", safe_filename(manifest["album"]))


def load_manifest(year):
    manifest = read_json(state_path("manifests", f"{year}.json"))
    if not manifest:
        raise SystemExit(f"ERROR: no manifest for {year}; run `top-hits.py resolve {year}` first.")
    return manifest


def load_progress(year):
    return read_json(state_path("progress", f"{year}.json"), default={"year": year, "ranks": {}})


def save_progress(progress):
    write_json(state_path("progress", f"{progress['year']}.json"), progress)


def not_ready_rows(manifest):
    return [r for r in manifest["entries"] if r["status"] not in READY_STATUSES]


def find_audio(folder):
    hits = []
    for root, _dirs, files in os.walk(folder):
        for fn in files:
            if fn.lower().endswith(".m4a"):
                hits.append(os.path.join(root, fn))
    return sorted(hits)


def read_audio(path):
    from mutagen.mp4 import MP4
    audio = MP4(path)
    title = (audio.get("\xa9nam") or [""])[0]
    artist = (audio.get("\xa9ART") or [""])[0]
    return title, artist, audio.info.length


def verify_file(path, row):
    """Check a downloaded file against the manifest pick by title and duration."""
    q = row["qobuz"]
    try:
        title, _artist, length = read_audio(path)
    except Exception as e:
        return False, f"unreadable: {e}"
    sim = max(similarity(base_title(q["title"]), base_title(title)), similarity(norm(q["title"]), norm(title)))
    if sim < 0.85:
        return False, f"title mismatch: file={title!r} expected={q['title']!r}"
    if q["duration"] and abs(length - q["duration"]) > 4:
        return False, f"duration mismatch: file={length:.0f}s expected={q['duration']}s"
    return True, f"{title!r} {length:.0f}s"


TRANSFER_ERROR = re.compile(r"IncompleteRead|Connection broken|Persistent error downloading", re.I)


def rip_track(dest, url):
    """Rip one track; if the transfer itself keeps breaking, fall back to lower quality tiers (different CDN files)."""
    os.makedirs(dest, exist_ok=True)
    log_path = os.path.join(dest, "rip.log")
    out_all = ""
    for quality in (None, 2, 1):
        cmd = [LOCAL_RIP] + (["-q", str(quality)] if quality else []) + ["-f", dest, "url", url]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
        out = (proc.stdout or "") + (proc.stderr or "")
        out_all += f"\n===== quality={quality or 'config'} exit={proc.returncode} =====\n{out}"
        with open(log_path, "w", encoding="utf-8") as f:
            f.write(out_all)
        if find_audio(dest) or FATAL_RIP.search(out) or not TRANSFER_ERROR.search(out):
            return proc.returncode, out_all
    return proc.returncode, out_all


def cmd_download(args):
    year = args.year
    manifest = load_manifest(year)
    blocked = not_ready_rows(manifest)
    if blocked:
        ranks = ", ".join(f"{r['rank']}({r['status']})" for r in blocked)
        raise SystemExit(f"ERROR: {year} has rows needing a decision: {ranks}. Fix overrides/{year}.json and re-resolve.")
    progress = load_progress(year)
    if progress.get("assembled_at"):
        print(f"{year}: already assembled at {progress['assembled_at']}; nothing to download.")
        return
    filed = library_album_dir(manifest)
    if os.path.isdir(filed) and find_audio(filed):
        raise SystemExit(f"ERROR: {filed} already holds {len(find_audio(filed))} tracks but progress/{year}.json is not marked assembled; "
                         f"run `top-hits.py adopt {year}` to reconcile instead of re-downloading.")
    lock = year_lock(year, "download")
    staging = os.path.join(DOWNLOADS_DIR, str(year))
    consecutive = 0
    done = skipped = failed = 0
    for row in manifest["entries"]:
        rank = row["rank"]
        if row["status"] == "skip":
            skipped += 1
            continue
        rec = progress["ranks"].get(str(rank), {})
        if rec.get("verified") and os.path.exists(rec.get("path", "")):
            done += 1
            continue
        dest = os.path.join(staging, f"{rank:02d}")
        existing = find_audio(dest)
        if existing:
            ok, detail = verify_file(existing[0], row)
            if ok:
                rec.update({"verified": True, "path": existing[0], "error": None, "verified_at": now_iso()})
                progress["ranks"][str(rank)] = rec
                save_progress(progress)
                done += 1
                print(f"  {rank:>2}. re-verified existing file: {detail}")
                continue
        shutil.rmtree(dest, ignore_errors=True)
        print(f"  {rank:>2}. {row['qobuz']['title']} — {row['qobuz']['performer']} ... ", end="", flush=True)
        try:
            code, out = rip_track(dest, row["qobuz"]["url"])
        except subprocess.TimeoutExpired:
            code, out = -1, "timeout"
        rec = {"attempts": rec.get("attempts", 0) + 1, "verified": False, "path": None, "last_attempt": now_iso()}
        if FATAL_RIP.search(out):
            rec["error"] = "fatal: " + FATAL_RIP.search(out).group(0)
            progress["ranks"][str(rank)] = rec
            save_progress(progress)
            print("FATAL")
            print(f"ABORT: rip reported an auth/rate-limit condition on rank {rank}; see {dest}/rip.log. Stop the run.")
            raise SystemExit(2)
        files = find_audio(dest)
        ok, detail = verify_file(files[0], row) if files else (False, f"rip exit {code}, no audio file")
        if ok:
            rec.update({"verified": True, "path": files[0], "error": None, "verified_at": now_iso()})
            consecutive = 0
            done += 1
            print(f"ok {detail}" + (f" (rip exited {code} after the download; see rip.log)" if code != 0 else ""))
        else:
            rec["error"] = detail if files else detail
            consecutive += 1
            failed += 1
            print(f"FAILED ({detail})")
        progress["ranks"][str(rank)] = rec
        save_progress(progress)
        if consecutive >= 3:
            print(f"HALT: {consecutive} consecutive failures in {year}; wait a few minutes and rerun `top-hits.py download {year}` (see {staging}/*/rip.log).")
            raise SystemExit(3)
        time.sleep(2)
    wanted = sum(1 for r in manifest["entries"] if r["status"] != "skip")
    print(f"\n{year}: {done}/{wanted} verified, {skipped} skipped, {failed} failed this run.")
    if done < wanted:
        raise SystemExit(1)


# -------------------------------------------------------------- stage: assemble
def load_font(size):
    from PIL import ImageFont
    for path in COVER_FONTS:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default(size=size)


def make_cover(year, path, size=1400):
    import colorsys
    from PIL import Image, ImageDraw
    hue = ((year - 2000) * 37 % 360) / 360
    bg = tuple(int(255 * c) for c in colorsys.hsv_to_rgb(hue, 0.55, 0.26))
    fg = tuple(int(255 * c) for c in colorsys.hsv_to_rgb(hue, 0.18, 0.97))
    img = Image.new("RGB", (size, size), bg)
    draw = ImageDraw.Draw(img)
    small, big = load_font(int(size * 0.13)), load_font(int(size * 0.50))

    def place(text, font, ink_top):
        left, top, right, bottom = draw.textbbox((0, 0), text, font=font, anchor="lt")
        draw.text(((size - (right - left)) / 2 - left, ink_top - top), text, font=font, fill=fg, anchor="lt")
        return ink_top + (bottom - top)

    label_bottom = place("TOP 50 HITS", small, size * 0.20)
    rule_y = label_bottom + size * 0.05
    draw.rectangle([size * 0.30, rule_y, size * 0.70, rule_y + 6], fill=fg)
    place(str(year), big, rule_y + size * 0.06)
    img.save(path, "JPEG", quality=92)
    with open(path, "rb") as f:
        return f.read()


NOISE_WORDS = re.compile(r"\b(?:album|main|original|version|mix|explicit|clean|final)\b", re.I)
NOISE_PHRASES = re.compile(r"\b(?:(?:us |uk )?album version|main version|original version|original mix|album mix|explicit|clean|final)\b", re.I)


def _noise_only(text):
    return not re.sub(r"[\s\-/()\[\]]+", "", NOISE_WORDS.sub("", NOISE_PHRASES.sub("", text)))


def _tidy(text):
    text = re.sub(r"\(\s*\)|\[\s*\]", "", text)
    return re.sub(r"\s+", " ", text).strip(" -/")


def display_title(row):
    """Library title for a manifest row: the chart title when Qobuz's is asterisk-censored, else the cleaned Qobuz title."""
    q = row["qobuz"]
    ctx = {"chart_title": row["chart_title"]}
    if "*" in (q.get("title") or "") and censored_match(ctx, q["title"]):
        return clean_title(row["chart_title"], q.get("version"))
    return clean_title(q["title"], q.get("version"))


def clean_title(title, version):
    """Title as shown in the library: Qobuz title minus noise parentheticals, plus any meaningful version."""
    title = (title or "").strip()
    while True:
        cleaned = re.sub(r"\s*\(([^()]*)\)", lambda m: "" if _noise_only(m.group(1)) else m.group(0), title)
        cleaned = re.sub(r"\s*\[([^\[\]]*)\]", lambda m: "" if _noise_only(m.group(1)) else m.group(0), cleaned)
        if cleaned == title:
            break
        title = cleaned
    title = _tidy(title)
    version = version or ""
    version = "" if _noise_only(version) else _tidy(NOISE_PHRASES.sub("", version))
    if version and norm(version) not in norm(title):
        return f"{title} ({version})"
    return title


def artist_override(file_artist, chart_artist):
    """The chart credit when the file's artist tag names none of the charted main artists, else None."""
    main_names, _ = split_credit(chart_artist)
    if any(name_in(n, norm(file_artist)) for n in main_names):
        return None
    return chart_artist


def tag_file(path, row, manifest, cover_bytes):
    from mutagen.mp4 import MP4, MP4Cover, MP4FreeForm
    audio = MP4(path)
    fixed = artist_override((audio.get("\xa9ART") or [""])[0], row["chart_artist"])
    if fixed:
        audio["\xa9ART"] = [fixed]
    audio["\xa9nam"] = [display_title(row)]
    audio["\xa9alb"] = [manifest["album"]]
    audio["aART"] = [manifest["album_artist"]]
    audio["cpil"] = True
    audio["\xa9day"] = [str(manifest["year"])]
    audio["trkn"] = [(row["rank"], TOP_N)]
    audio["disk"] = [(1, 1)]
    audio["\xa9gen"] = [row["genre"] or "Pop"]
    source_genre = (row["qobuz"] or {}).get("genre")
    if source_genre:
        audio["----:com.apple.iTunes:SOURCE_GENRE"] = [MP4FreeForm(source_genre.encode("utf-8"))]
    audio["covr"] = [MP4Cover(cover_bytes, imageformat=MP4Cover.FORMAT_JPEG)]
    for key in ("\xa9cmt", "cprt"):
        audio.pop(key, None)
    audio.save()
    return fixed


def safe_filename(name):
    return "".join("_" if c in '/\\:*?"<>|' else c for c in name).strip().rstrip(".") or "track"


def nas_chmod(library_dirs):
    host = None
    for candidate in NAS_HOSTS:
        if subprocess.run(["ssh", "-n", "-o", "ConnectTimeout=5", "-o", "BatchMode=yes", candidate, "true"],
                          capture_output=True).returncode == 0:
            host = candidate
            break
    if not host:
        return [f"ssh <nas> \"chmod 775 '{os.path.dirname(d)}'; chmod -R 775 '{d}'\"" for d in library_dirs]
    failed = []
    for d in library_dirs:
        nas_dir = d.replace("/Volumes/Media", "/share/Media", 1)
        esc = lambda p: p.replace("'", "'\\''")
        cmd = f"chmod 775 '{esc(os.path.dirname(nas_dir))}'; chmod -R 775 '{esc(nas_dir)}'"
        ok = False
        for _ in range(2):
            if subprocess.run(["ssh", "-n", "-o", "ConnectTimeout=10", host, cmd], capture_output=True).returncode == 0:
                ok = True
                break
            time.sleep(3)
        if not ok:
            failed.append(f"ssh {host} \"{cmd}\"")
    return failed


def score_runnability(library_dir):
    for verb in ("analyze", "write"):
        proc = subprocess.run([RUNNABILITY, verb, "--force", library_dir], capture_output=True, text=True)
        if proc.returncode != 0:
            return False, (proc.stderr or proc.stdout or "")[-400:]
    return True, ""


def cmd_assemble(args):
    year = args.year
    manifest = load_manifest(year)
    progress = load_progress(year)
    if progress.get("assembled_at") and not args.force:
        print(f"{year}: already assembled at {progress['assembled_at']} ({progress.get('library_dir')}); nothing to do (use --force to redo).")
        return
    lock = year_lock(year, "assemble")
    rows = [r for r in manifest["entries"] if r["status"] != "skip"]
    staging = os.path.join(DOWNLOADS_DIR, str(year))
    album_dir = os.path.join(staging, safe_filename(manifest["album"]))
    os.makedirs(album_dir, exist_ok=True)
    for row in rows:
        rec = progress["ranks"].get(str(row["rank"]), {})
        if rec.get("verified") and not os.path.exists(rec.get("path") or ""):
            moved = glob.glob(os.path.join(album_dir, f"{row['rank']:02d} *.m4a"))
            if moved:
                rec["path"] = moved[0]
    missing = [r["rank"] for r in rows
               if not progress["ranks"].get(str(r["rank"]), {}).get("verified")
               or not os.path.exists(progress["ranks"][str(r["rank"])]["path"])]
    if missing:
        raise SystemExit(f"ERROR: {year} is missing verified files for ranks {missing}; run `top-hits.py download {year}`.")
    if not os.path.isdir(LIBRARY_ROOT) or not os.listdir(LIBRARY_ROOT):
        raise SystemExit(f"ERROR: library root {LIBRARY_ROOT} is not mounted.")

    cover_bytes = make_cover(year, os.path.join(album_dir, "cover.jpg"))
    print(f"--> Tagging {len(rows)} tracks as '{manifest['album']}'")
    for row in rows:
        rec = progress["ranks"][str(row["rank"])]
        dst = os.path.join(album_dir, f"{row['rank']:02d} {safe_filename(display_title(row))}.m4a")
        if os.path.abspath(rec["path"]) != os.path.abspath(dst):
            shutil.move(rec["path"], dst)
            rec["path"] = dst
            save_progress(progress)
        fixed = tag_file(dst, row, manifest, cover_bytes)
        if fixed:
            print(f"    {row['rank']:>2}. artist tag replaced with chart credit: {fixed!r}")

    print("--> Organizing into the library")
    organize_manifest = os.path.join(staging, "organize-manifest.txt")
    proc = subprocess.run([ORGANIZER, "--library-root", LIBRARY_ROOT, "--on-collision", "replace",
                           "--manifest", organize_manifest, album_dir], text=True)
    if proc.returncode != 0:
        raise SystemExit(f"ERROR: music-organize exited {proc.returncode}; tagged files remain at {album_dir}.")
    with open(organize_manifest, encoding="utf-8") as f:
        library_dirs = [l.strip() for l in f if l.strip()]
    os.remove(organize_manifest)
    if not library_dirs:
        raise SystemExit("ERROR: organizer reported no destination folders.")

    print("--> Setting permissions on the NAS")
    perm_failures = nas_chmod(library_dirs)
    print("--> Scoring runnability")
    run_ok, run_err = score_runnability(library_dirs[0])

    progress.update({"assembled_at": now_iso(), "library_dir": library_dirs[0],
                     "perm_failures": perm_failures, "runnability_scored": run_ok})
    for row in rows:
        rank_dir = os.path.join(staging, f"{row['rank']:02d}")
        shutil.rmtree(rank_dir, ignore_errors=True)
    save_progress(progress)

    print(f"\n{year}: {len(rows)} tracks filed at {library_dirs[0]}")
    if perm_failures:
        print("WARNING: NAS permissions not set; once the NAS is reachable run:")
        for cmd in perm_failures:
            print(f"  {cmd}")
    if not run_ok:
        print(f"WARNING: runnability scoring failed (nightly sync will retry): {run_err.strip()}")
    if perm_failures:
        raise SystemExit(4)


# ----------------------------------------------------------------- stage: adopt
def cmd_adopt(args):
    """Mark a year assembled from the album already filed in the library (repairs a clobbered progress record)."""
    from mutagen.mp4 import MP4
    year = args.year
    manifest = load_manifest(year)
    progress = load_progress(year)
    filed = library_album_dir(manifest)
    files = find_audio(filed) if os.path.isdir(filed) else []
    wanted = {r["rank"]: r for r in manifest["entries"] if r["status"] != "skip"}
    found = {}
    for path in files:
        rank = MP4(path).get("trkn", [(0, 0)])[0][0]
        if rank in wanted:
            found[rank] = path
    missing = sorted(set(wanted) - set(found))
    if missing:
        raise SystemExit(f"ERROR: {filed} lacks ranks {missing}; cannot adopt.")
    for rank, path in found.items():
        rec = progress["ranks"].setdefault(str(rank), {"attempts": 0})
        rec.update({"verified": True, "path": path, "error": None, "verified_at": rec.get("verified_at") or now_iso()})
    progress.update({"assembled_at": progress.get("assembled_at") or now_iso(), "library_dir": filed,
                     "perm_failures": nas_chmod([filed]), "runnability_scored": score_runnability(filed)[0]})
    save_progress(progress)
    staging = os.path.join(DOWNLOADS_DIR, str(year))
    if os.path.isdir(staging):
        shutil.rmtree(staging, ignore_errors=True)
    print(f"{year}: adopted {len(found)} tracks at {filed}; staging cleared" + (f"; perm fixes pending: {progress['perm_failures']}" if progress["perm_failures"] else ""))


# ------------------------------------------------------------------ stage: redo
def cmd_redo(args):
    """Replace specific ranks of an already-filed year with the manifest's current pick."""
    from mutagen.mp4 import MP4
    year = args.year
    manifest = load_manifest(year)
    progress = load_progress(year)
    library_dir = progress.get("library_dir")
    if not progress.get("assembled_at") or not library_dir or not os.path.isdir(library_dir):
        raise SystemExit(f"ERROR: {year} is not assembled; use download/assemble instead.")
    lock = year_lock(year, "redo")
    by_rank = {r["rank"]: r for r in manifest["entries"]}
    existing = {}
    for path in find_audio(library_dir):
        existing.setdefault(MP4(path).get("trkn", [(0, 0)])[0][0], []).append(path)
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        cover_bytes = make_cover(year, os.path.join(tmp, "cover.jpg"))
    staging = os.path.join(DOWNLOADS_DIR, str(year))
    failures = []
    for rank in args.rank:
        row = by_rank.get(rank)
        if not row or not row["qobuz"]:
            failures.append((rank, "no pick in manifest"))
            continue
        dest = os.path.join(staging, f"{rank:02d}")
        shutil.rmtree(dest, ignore_errors=True)
        print(f"  {rank:>2}. {row['qobuz']['title']} — {row['qobuz']['performer']} ... ", end="", flush=True)
        try:
            code, out = rip_track(dest, row["qobuz"]["url"])
        except subprocess.TimeoutExpired:
            code, out = -1, "timeout"
        files = find_audio(dest)
        ok, detail = verify_file(files[0], row) if files else (False, f"rip exit {code}, no audio file")
        if not ok:
            failures.append((rank, detail))
            print(f"FAILED ({detail})")
            continue
        title = display_title(row)
        target = os.path.join(library_dir, f"{rank:02d} {safe_filename(title)}.m4a")
        tag_file(files[0], row, manifest, cover_bytes)
        for old in existing.get(rank, []):
            if os.path.abspath(old) != os.path.abspath(target):
                os.remove(old)
        shutil.move(files[0], target)
        shutil.rmtree(dest, ignore_errors=True)
        progress["ranks"][str(rank)] = {"attempts": 1, "verified": True, "path": target, "error": None,
                                        "verified_at": now_iso(), "redone_at": now_iso()}
        save_progress(progress)
        print(f"ok -> {os.path.basename(target)}")
    perm_failures = nas_chmod([library_dir])
    run_ok, run_err = score_runnability(library_dir)
    progress["perm_failures"] = perm_failures
    save_progress(progress)
    if perm_failures:
        print("WARNING: NAS permissions not set:\n  " + "\n  ".join(perm_failures))
    if not run_ok:
        print(f"WARNING: runnability scoring failed: {run_err.strip()}")
    if failures:
        print("FAILED ranks: " + ", ".join(f"{r} ({d})" for r, d in failures))
        raise SystemExit(1)


# ----------------------------------------------------------------- stage: retag
def cmd_retag(args):
    """Re-apply the compilation tags (and cleaned titles) to an already-filed year, renaming files to match."""
    import tempfile
    from mutagen.mp4 import MP4
    year = args.year
    manifest = load_manifest(year)
    progress = load_progress(year)
    library_dir = progress.get("library_dir")
    if not progress.get("assembled_at") or not library_dir or not os.path.isdir(library_dir):
        raise SystemExit(f"ERROR: {year} is not assembled (or {library_dir} is missing).")
    by_rank = {r["rank"]: r for r in manifest["entries"]}
    with tempfile.TemporaryDirectory() as tmp:
        cover_bytes = make_cover(year, os.path.join(tmp, "cover.jpg"))
        shutil.copy2(os.path.join(tmp, "cover.jpg"), os.path.join(library_dir, "cover.jpg"))
    renamed = 0
    for path in find_audio(library_dir):
        rank = MP4(path).get("trkn", [(0, 0)])[0][0]
        row = by_rank.get(rank)
        if not row or not row["qobuz"]:
            print(f"  skipping {os.path.basename(path)}: no manifest row for track {rank}")
            continue
        tag_file(path, row, manifest, cover_bytes)
        title = display_title(row)
        dest = os.path.join(library_dir, f"{rank:02d} {safe_filename(title)}.m4a")
        if dest != path:
            os.rename(path, dest)
            renamed += 1
        print(f"  {rank:>2}. {title}")
    print(f"\n{year}: retagged {len(by_rank)} tracks, renamed {renamed} files in {library_dir}")


# ------------------------------------------------------------------- stage: run
def _run_stage(args_list, log):
    cmd = [os.path.abspath(__file__), *args_list]
    log.write(f"\n[{now_iso()}] $ top-hits.py {' '.join(args_list)}\n")
    log.flush()
    proc = subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT, text=True)
    log.write(f"[{now_iso()}] exit {proc.returncode}\n")
    log.flush()
    return proc.returncode


def write_run_report(lines):
    path = state_path("run-report.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"# Top 50 Hits run report\n\nUpdated {now_iso()}\n\n" + "\n".join(lines) + "\n")
    return path


def cmd_run(args):
    years = parse_years(args.years)
    report = []
    log = open(state_path("run.log"), "a", encoding="utf-8")
    log.write(f"\n===== run {args.years} started {now_iso()} pid {os.getpid()} =====\n")
    stop_reason = None
    for year in years:
        info = year_status(year)
        if info["assembled"]:
            report.append(f"- {year}: assembled (already) — {info['library_dir']}")
            write_run_report(report + ([f"\nSTOPPED: {stop_reason}"] if stop_reason else []))
            continue
        if not info["resolved"]:
            report.append(f"- {year}: BLOCKED, rows still need a decision ({info['counts']})")
            write_run_report(report)
            continue
        code = None
        for attempt in range(1, args.max_attempts + 1):
            code = _run_stage(["download", str(year)], log)
            if code == 0:
                break
            if code == 2:
                stop_reason = f"{year} download reported ABORT (auth/rate limit) on attempt {attempt}"
                break
            log.write(f"[{now_iso()}] {year}: download exit {code} on attempt {attempt}; waiting {args.retry_wait}s\n")
            log.flush()
            time.sleep(args.retry_wait)
        if stop_reason:
            report.append(f"- {year}: STOPPED during download — {stop_reason}; resume: `top-hits.py run --years {year}-{years[-1]}`")
            write_run_report(report + [f"\nSTOPPED: {stop_reason}"])
            break
        if code != 0:
            info = year_status(year)
            report.append(f"- {year}: INCOMPLETE after {args.max_attempts} download attempts ({info['verified']}/{info['wanted']} files); moving on; resume: `top-hits.py run --years {year}`")
            write_run_report(report)
            time.sleep(args.cooldown)
            continue
        code = _run_stage(["assemble", str(year)], log)
        info = year_status(year)
        if code == 0:
            report.append(f"- {year}: assembled {info['verified']}/{info['wanted']} — {info['library_dir']}")
        elif code == 4:
            report.append(f"- {year}: assembled {info['verified']}/{info['wanted']} but NAS permissions failed; see run.log for the chmod commands")
        else:
            report.append(f"- {year}: ASSEMBLE FAILED (exit {code}); see run.log; resume: `top-hits.py run --years {year}-{years[-1]}`")
            write_run_report(report)
            stop_reason = f"{year} assemble exit {code}"
            break
        write_run_report(report)
        time.sleep(args.cooldown)
    path = write_run_report(report + ([f"\nSTOPPED: {stop_reason}"] if stop_reason else ["\nRun finished."]))
    log.write(f"===== run ended {now_iso()} {'STOPPED: ' + stop_reason if stop_reason else 'finished'} =====\n")
    log.close()
    print(f"report: {path}")
    if stop_reason:
        raise SystemExit(2)


# ---------------------------------------------------------------- stage: status
def parse_years(spec):
    years = []
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-", 1)
            years.extend(range(int(a), int(b) + 1))
        elif part:
            years.append(int(part))
    return years


def year_status(year):
    manifest = read_json(state_path("manifests", f"{year}.json"))
    progress = read_json(state_path("progress", f"{year}.json"), default={"ranks": {}})
    info = {"year": year, "chart": os.path.exists(state_path("charts", f"{year}.json")),
            "resolved": False, "counts": {}, "wanted": 0, "verified": 0,
            "assembled": bool(progress.get("assembled_at")), "library_dir": progress.get("library_dir")}
    if manifest:
        for r in manifest["entries"]:
            info["counts"][r["status"]] = info["counts"].get(r["status"], 0) + 1
        info["resolved"] = not not_ready_rows(manifest)
        info["wanted"] = sum(1 for r in manifest["entries"] if r["status"] != "skip")
        info["verified"] = sum(1 for r in manifest["entries"] if r["status"] != "skip"
                               and progress["ranks"].get(str(r["rank"]), {}).get("verified"))
    info["downloaded"] = info["resolved"] and info["wanted"] > 0 and info["verified"] == info["wanted"]
    return info


def cmd_status(args):
    all_ok = True
    for year in parse_years(args.years):
        info = year_status(year)
        ok = info[args.require] if args.require else True
        all_ok &= bool(ok)
        counts = " ".join(f"{k}={v}" for k, v in sorted(info["counts"].items())) or "-"
        stage = "assembled" if info["assembled"] else "downloaded" if info["downloaded"] else "resolved" if info["resolved"] else "manifest" if info["counts"] else "chart" if info["chart"] else "none"
        print(f"{year}: {stage:<10} {info['verified']:>2}/{info['wanted']:<2} files  [{counts}]"
              + (f"  {info['library_dir']}" if info["library_dir"] else ""))
    if not all_ok:
        raise SystemExit(1)


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
    p = sub.add_parser("approve", help="Accept the current pick for review-status rows.")
    p.add_argument("year", type=int)
    p.add_argument("--rank", type=int, nargs="*")
    p.set_defaults(func=cmd_approve)
    p = sub.add_parser("download", help="Download every non-skipped rank into per-rank staging folders.")
    p.add_argument("year", type=int)
    p.set_defaults(func=cmd_download)
    p = sub.add_parser("assemble", help="Tag, cover, and file the year as one compilation.")
    p.add_argument("year", type=int)
    p.add_argument("--force", action="store_true", help="Redo an already-assembled year.")
    p.set_defaults(func=cmd_assemble)
    p = sub.add_parser("adopt", help="Reconcile progress with an album already filed in the library.")
    p.add_argument("year", type=int)
    p.set_defaults(func=cmd_adopt)
    p = sub.add_parser("redo", help="Re-download and replace given ranks of an assembled year.")
    p.add_argument("year", type=int)
    p.add_argument("--rank", type=int, nargs="+", required=True)
    p.set_defaults(func=cmd_redo)
    p = sub.add_parser("retag", help="Re-apply tags and cleaned titles to an assembled year in place.")
    p.add_argument("year", type=int)
    p.set_defaults(func=cmd_retag)
    p = sub.add_parser("run", help="Unattended driver over a year range.")
    p.add_argument("--years", required=True)
    p.add_argument("--max-attempts", type=int, default=4)
    p.add_argument("--retry-wait", type=int, default=300)
    p.add_argument("--cooldown", type=int, default=60)
    p.set_defaults(func=cmd_run)
    p = sub.add_parser("status", help="Per-year progress; exit 1 unless every year meets --require.")
    p.add_argument("--years", required=True, help="e.g. 2008-2025 or 2008,2010")
    p.add_argument("--require", choices=["resolved", "downloaded", "assembled"])
    p.set_defaults(func=cmd_status)
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
