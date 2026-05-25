#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "mutagen",
# ]
# ///
"""Import an externally-acquired album folder into the Navidrome library.

Bridges "raw folder of audio files (possibly untagged, possibly with extras
like cue/log/cover-in-subfolder)" to a clean handoff to music-organize.py:

  1. Parse track number + title (+ optional artist/disc) from filenames when
     existing tags are missing or unreliable.
  2. Apply per-track tags via mutagen (artist, albumartist, album, title,
     trkn, disk, genre, year, cpil). Strip stray comment/copyright tags.
     Mirrors tagger.py's smart Soundtrack cpil auto-detect.
  3. Promote cover art from common subfolders (Album Artwork/, Artwork/,
     Scans/, ...) to <source>/cover.jpg so music-organize.py picks it up.
  4. (Optional, --preserve-extras) Move non-audio leftovers (cue/log/rtf
     etc.) to <library-root>/../Music-Imports/<date>/<artist>/<album>/
     before music-organize rmtrees the source.
  5. Invoke music-organize.py to file the album under
     <library-root>/<artist|Compilations>/<album>/. Supports --replaces for
     the guarded re-download path.

Usage:
    import-album.py [options] <source_folder>
"""

import argparse
import os
import pathlib
import re
import shutil
import subprocess
import sys
from datetime import datetime

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from _music_tags import compilation_signal, norm_artist  # noqa: E402

from mutagen.flac import FLAC  # noqa: E402
from mutagen.id3 import COMM, TALB, TCMP, TCON, TCOP, TDRC, TIT2, TPE1, TPE2, TPOS, TRCK  # noqa: E402
from mutagen.mp3 import MP3  # noqa: E402
from mutagen.mp4 import MP4  # noqa: E402

AUDIO_EXTS = {".m4a", ".flac", ".mp3"}
IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp")
TOP_COVER_STEMS = ("cover", "folder", "front", "album")

# From stow/fish/.config/fish/functions/riptag.fish; keep in sync.
ALLOWED_GENRES = (
    "Ambient", "Bluegrass", "Classical", "Country", "Electronic",
    "Experimental", "Folk", "Hip-Hop", "Jazz", "Lo-Fi", "Mashup",
    "Pop", "R&B", "Reggae", "Rock", "Soundtrack", "Unknown",
)

# Tried in order; first pattern that matches every file wins.
FILENAME_PATTERNS = (
    # "01 Artist - Title.ext"
    r"^(?P<track>\d+)\s+(?P<artist>.+?)\s+-\s+(?P<title>.+)\.[^.]+$",
    # "1-01 Title.ext"  (multi-disc, music-organize's own output shape)
    r"^(?P<disc>\d+)-(?P<track>\d+)\s+(?P<title>.+)\.[^.]+$",
    # "01. Title.ext"
    r"^(?P<track>\d+)\.\s+(?P<title>.+)\.[^.]+$",
    # "01 - Title.ext"
    r"^(?P<track>\d+)\s+-\s+(?P<title>.+)\.[^.]+$",
    # "01 Title.ext"
    r"^(?P<track>\d+)\s+(?P<title>.+)\.[^.]+$",
)

# ----------------------------------------------------------------- disc-from-folder
# Matches "Disc 1", "Disc1", "Disc-1", "CD 2", "CD2", "Disk-3", "Disc One" etc.
_DISC_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}
_DISC_FOLDER_RE = re.compile(
    r"^(?:disc|disk|cd)\s*[-_]?\s*"
    r"(\d+|one|two|three|four|five|six|seven|eight|nine|ten)$",
    re.I,
)


def disc_from_path(file_path, source_root):
    """Return disc number derived from a Disc/CD/Disk parent folder, or None."""
    rel = os.path.relpath(file_path, source_root)
    for part in rel.split(os.sep)[:-1]:
        m = _DISC_FOLDER_RE.match(part.strip())
        if m:
            val = m.group(1).lower()
            return int(val) if val.isdigit() else _DISC_WORDS.get(val)
    return None


# ----------------------------------------------------------------- tag reading
def _first(v):
    if isinstance(v, (list, tuple)):
        return v[0] if v else None
    return v


def _int(v):
    if v is None:
        return 0
    if isinstance(v, int):
        return v
    m = re.match(r"\s*(\d+)", str(v))
    return int(m.group(1)) if m else 0


def read_existing_tags(path):
    """Return a normalized dict of identity tags for any supported audio file."""
    ext = os.path.splitext(path)[1].lower()
    out = dict(
        albumartist=None, artist=None, album=None, title=None,
        track=0, disc=0, year=None,
    )
    if ext == ".m4a":
        a = MP4(path)
        out["albumartist"] = _first(a.get("aART"))
        out["artist"] = _first(a.get("\xa9ART"))
        out["album"] = _first(a.get("\xa9alb"))
        out["title"] = _first(a.get("\xa9nam"))
        trkn = _first(a.get("trkn")) or (0, 0)
        disk = _first(a.get("disk")) or (0, 0)
        out["track"] = trkn[0] if trkn else 0
        out["disc"] = disk[0] if disk else 0
        out["year"] = _first(a.get("\xa9day"))
    elif ext == ".flac":
        a = FLAC(path)
        g = lambda k: _first(a.get(k))
        out["albumartist"] = g("albumartist")
        out["artist"] = g("artist")
        out["album"] = g("album")
        out["title"] = g("title")
        out["track"] = _int(g("tracknumber"))
        out["disc"] = _int(g("discnumber"))
        out["year"] = g("date")
    elif ext == ".mp3":
        tags = MP3(path).tags

        def txt(frame):
            fr = tags.get(frame) if tags else None
            return str(fr.text[0]) if fr and getattr(fr, "text", None) else None

        out["albumartist"] = txt("TPE2")
        out["artist"] = txt("TPE1")
        out["album"] = txt("TALB")
        out["title"] = txt("TIT2")
        out["track"] = _int(txt("TRCK"))
        out["disc"] = _int(txt("TPOS"))
        out["year"] = txt("TDRC")
    for k in ("albumartist", "artist", "album", "title", "year"):
        if out[k] is not None:
            out[k] = str(out[k]).strip() or None
    return out


# ----------------------------------------------------------------- filename parsing
def parse_filename(name, pattern):
    m = re.match(pattern, name)
    if not m:
        return None
    g = m.groupdict()
    parsed = {}
    if "track" in g and g["track"]:
        parsed["track"] = int(g["track"])
    if "disc" in g and g["disc"]:
        parsed["disc"] = int(g["disc"])
    if "title" in g and g["title"]:
        parsed["title"] = g["title"].strip()
    if "artist" in g and g["artist"]:
        parsed["artist"] = g["artist"].strip()
    return parsed


def autodetect_pattern(filenames):
    """Return the first FILENAME_PATTERNS entry that matches every filename."""
    for pat in FILENAME_PATTERNS:
        if all(re.match(pat, fn) for fn in filenames):
            return pat
    return None


# ----------------------------------------------------------------- tag writing
def write_tags(path, plan, compilation):
    """Write the resolved tags for one file. Raises on failure."""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".m4a":
        a = MP4(path)
        a["aART"] = [plan["albumartist"]]
        a["\xa9ART"] = [plan["artist"]]
        a["\xa9alb"] = [plan["album"]]
        a["\xa9nam"] = [plan["title"]]
        a["trkn"] = [(plan["track"], plan["track_total"])]
        a["disk"] = [(plan["disc"], plan["disc_total"])]
        a["\xa9gen"] = [plan["genre"]]
        if plan.get("year"):
            a["\xa9day"] = [str(plan["year"])]
        a["cpil"] = bool(compilation)  # bare bool — mutagen renders a list as truthy
        for stale in ("\xa9cmt", "cprt"):
            if stale in a:
                del a[stale]
        a.save()
    elif ext == ".flac":
        a = FLAC(path)
        a["albumartist"] = plan["albumartist"]
        a["artist"] = plan["artist"]
        a["album"] = plan["album"]
        a["title"] = plan["title"]
        a["tracknumber"] = str(plan["track"])
        a["totaltracks"] = str(plan["track_total"])
        a["discnumber"] = str(plan["disc"])
        a["totaldiscs"] = str(plan["disc_total"])
        a["genre"] = plan["genre"]
        if plan.get("year"):
            a["date"] = str(plan["year"])
        a["compilation"] = "1" if compilation else "0"
        for stale in ("comment", "copyright"):
            if stale in a:
                del a[stale]
        a.save()
    elif ext == ".mp3":
        m = MP3(path)
        if m.tags is None:
            m.add_tags()
        t = m.tags
        t["TPE2"] = TPE2(encoding=3, text=plan["albumartist"])
        t["TPE1"] = TPE1(encoding=3, text=plan["artist"])
        t["TALB"] = TALB(encoding=3, text=plan["album"])
        t["TIT2"] = TIT2(encoding=3, text=plan["title"])
        t["TRCK"] = TRCK(encoding=3, text=f"{plan['track']}/{plan['track_total']}")
        t["TPOS"] = TPOS(encoding=3, text=f"{plan['disc']}/{plan['disc_total']}")
        t["TCON"] = TCON(encoding=3, text=plan["genre"])
        if plan.get("year"):
            t["TDRC"] = TDRC(encoding=3, text=str(plan["year"]))
        t["TCMP"] = TCMP(encoding=3, text="1" if compilation else "0")
        t.delall("COMM")
        t.delall("TCOP")
        m.save()
    else:
        raise ValueError(f"unsupported extension {ext}")


# ----------------------------------------------------------------- cover art
def find_existing_top_cover(folder):
    """Return path to a top-level cover image music-organize would already pick up."""
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return None
    for fn in names:
        stem, ext = os.path.splitext(fn)
        if ext.lower() in IMAGE_EXTS and stem.lower() in TOP_COVER_STEMS:
            full = os.path.join(folder, fn)
            if os.path.isfile(full):
                return full
    return None


def find_cover_candidate(folder):
    """Best-guess cover image when none of the top-level stems exist.

    Ranks (lower = better):
      0: top-level file whose stem contains 'front'
      1: top-level file whose stem contains 'cover'
      2: inside Album Artwork/Artwork/Scans/Art/Covers, file matching front/cover/album
      3: inside the same subdirs, any image (alphabetical)
    """
    candidates = []  # (rank, name_for_tiebreak, path)
    cover_subdirs = ("album artwork", "artwork", "scans", "art", "covers")

    try:
        top = sorted(os.listdir(folder))
    except OSError:
        return None

    for fn in top:
        full = os.path.join(folder, fn)
        stem, ext = os.path.splitext(fn)
        if os.path.isfile(full) and ext.lower() in IMAGE_EXTS:
            low = stem.lower()
            if "front" in low:
                candidates.append((0, fn, full))
            elif "cover" in low:
                candidates.append((1, fn, full))

    for fn in top:
        full = os.path.join(folder, fn)
        if not os.path.isdir(full) or fn.lower() not in cover_subdirs:
            continue
        try:
            sub = sorted(os.listdir(full))
        except OSError:
            continue
        for sfn in sub:
            sfull = os.path.join(full, sfn)
            sstem, sext = os.path.splitext(sfn)
            if not os.path.isfile(sfull) or sext.lower() not in IMAGE_EXTS:
                continue
            low = sstem.lower()
            if any(k in low for k in ("front", "cover", "album")):
                candidates.append((2, sfn, sfull))
            else:
                candidates.append((3, sfn, sfull))

    if not candidates:
        return None
    candidates.sort(key=lambda c: (c[0], c[1].lower()))
    return candidates[0][2]


def promote_cover(folder, dry_run):
    """Ensure music-organize.py will find a cover at the source root.

    Returns the promoted path, or None if nothing usable was found.
    Does nothing if a top-level cover/folder/front/album image already exists.
    """
    existing = find_existing_top_cover(folder)
    if existing:
        return existing
    cand = find_cover_candidate(folder)
    if not cand:
        return None
    ext = os.path.splitext(cand)[1].lower()
    dest = os.path.join(folder, f"cover{ext}")
    if os.path.abspath(cand) == os.path.abspath(dest):
        return dest
    print(f"  -> promoting cover: {cand} -> {dest}")
    if dry_run:
        return dest
    shutil.copy2(cand, dest)
    return dest


# ----------------------------------------------------------------- extras
def preserve_extras(folder, archive_dir, artist, album, dry_run):
    """Move non-audio, non-cover leftovers somewhere safe before music-organize
    rmtrees the source. Walks recursively so per-disc cue/log/inserts inside
    Disc 1/, Disc 2/, etc. are also captured. Returns the archive path used,
    or None if nothing moved."""
    keep_top_names = {"cover.jpg", "cover.jpeg", "cover.png", "cover.webp"}
    moved = []
    for root, _dirs, files in os.walk(folder):
        for fn in sorted(files):
            if fn == ".DS_Store" or fn.startswith("._"):
                continue
            full = os.path.join(root, fn)
            ext = os.path.splitext(fn)[1].lower()
            if ext in AUDIO_EXTS:
                continue
            rel = os.path.relpath(full, folder)
            # Top-level cover.* is left for music-organize to pick up.
            if os.sep not in rel and fn.lower() in keep_top_names:
                continue
            moved.append((rel, full))
    if not moved:
        return None

    dest_root = os.path.join(
        archive_dir, datetime.now().strftime("%Y-%m-%d"), artist, album
    )
    print(f"  -> preserving {len(moved)} extra(s) -> {dest_root}")
    if dry_run:
        for rel, _ in moved:
            print(f"     [dry-run] {rel}")
        return dest_root
    for rel, full in moved:
        dest = os.path.join(dest_root, rel)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.move(full, dest)
    return dest_root


# ----------------------------------------------------------------- planning
def collect_audio(folder):
    """Return absolute paths of audio files, walked recursively, sorted."""
    out = []
    for root, dirs, files in os.walk(folder):
        dirs.sort()
        for fn in sorted(files):
            if fn.startswith("._"):
                continue
            if os.path.splitext(fn)[1].lower() in AUDIO_EXTS:
                out.append(os.path.join(root, fn))
    return out


def build_plan(audio_files, existing_tags, pattern, args, source):
    """Resolve per-track tag plans. Returns (plans_list, errors_list).

    Each plan is a dict: {file, albumartist, artist, album, title, track,
    track_total, disc, disc_total, genre, year}.
    """
    parsed = {}
    errors = []
    if pattern:
        for f in audio_files:
            p = parse_filename(os.path.basename(f), pattern)
            if p is None:
                errors.append(f"filename did not match pattern: {os.path.basename(f)}")
            else:
                parsed[f] = p
    else:
        parsed = {f: {} for f in audio_files}

    if errors:
        return [], errors

    # Resolve album-level defaults (CLI > shared existing tag value).
    def shared(key, default=None):
        vals = [existing_tags[f].get(key) for f in audio_files]
        vals = [v for v in vals if v]
        return vals[0] if vals else default

    album = args.album or shared("album")
    if not album:
        errors.append("--album is required (no existing 'album' tag to infer)")
    year = args.year or shared("year")

    # Determine per-track artist for each file, plus inferred album-level artist.
    track_artists = []
    for f in audio_files:
        per = parsed.get(f, {}).get("artist") or args.artist or existing_tags[f].get("artist")
        track_artists.append(per)
    inferred_album_artist = args.artist or shared("albumartist") or shared("artist")
    if not inferred_album_artist and any(track_artists):
        inferred_album_artist = next(a for a in track_artists if a)
    if not inferred_album_artist:
        errors.append("--artist is required (no existing artist tag to infer)")

    albumartist = args.albumartist or inferred_album_artist

    if errors:
        return [], errors

    # Disc derivation precedence: filename group > parent folder name > existing tag > 1.
    file_disc = {}
    for f in audio_files:
        p = parsed.get(f, {})
        file_disc[f] = (
            p.get("disc")
            or disc_from_path(f, source)
            or existing_tags[f].get("disc")
            or 1
        )
    disc_max = max(file_disc.values()) if file_disc else 1
    per_disc_count = {}
    for f in audio_files:
        d = file_disc[f]
        per_disc_count[d] = per_disc_count.get(d, 0) + 1

    plans = []
    for i, f in enumerate(audio_files):
        p = parsed.get(f, {})
        ex = existing_tags[f]
        title = p.get("title") or ex.get("title")
        if not title:
            errors.append(f"no title for {os.path.basename(f)} (no filename match, no existing tag)")
            continue
        disc = file_disc[f]
        track = p.get("track") or ex.get("track") or (i + 1)
        per_track_artist = track_artists[i] or albumartist
        plans.append(dict(
            file=f,
            albumartist=albumartist,
            artist=per_track_artist,
            album=album,
            title=title,
            track=track,
            track_total=per_disc_count.get(disc, len(audio_files)),
            disc=disc,
            disc_total=disc_max,
            genre=args.genre,
            year=year,
        ))
    return plans, errors


def resolve_compilation(args, plans):
    """Match tagger.py: explicit flag wins; otherwise only Soundtrack auto-detects."""
    if args.compilation is True:
        return True, "explicit --compilation"
    if args.compilation is False:
        return False, "explicit --no-compilation"
    if args.genre != "Soundtrack":
        return False, "default (non-Soundtrack)"
    aarts = [norm_artist(p["albumartist"]) for p in plans]
    tarts = [norm_artist(p["artist"]) for p in plans]
    signal = compilation_signal(aarts, tarts)
    if signal == "various_artists":
        return True, "auto: albumartist=Various Artists"
    if signal == "multi_artist":
        distinct = {a for a in tarts if a}
        return True, f"auto: {len(distinct)} distinct per-track artists"
    return False, "auto: single artist"


# ----------------------------------------------------------------- main
def main():
    parser = argparse.ArgumentParser(
        description="File an externally-acquired album folder into the music library.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("source", help="Source folder containing the album.")
    parser.add_argument(
        "--genre", required=True, choices=ALLOWED_GENRES, metavar="GENRE",
        help=f"One of: {', '.join(ALLOWED_GENRES)}",
    )
    parser.add_argument("--artist", help="Album artist (required unless inferable from tags).")
    parser.add_argument("--album", help="Album name (required unless inferable from tags).")
    parser.add_argument("--albumartist", help="Override album artist (defaults to --artist).")
    parser.add_argument("--year", help="Year, e.g. 2010.")
    comp = parser.add_mutually_exclusive_group()
    comp.add_argument("--compilation", dest="compilation", action="store_const",
                      const=True, default=None, help="Force cpil=True.")
    comp.add_argument("--no-compilation", dest="compilation", action="store_const",
                      const=False, help="Force cpil=False.")
    parser.add_argument(
        "--filename-pattern",
        help="Regex with named groups (?P<track>), (?P<title>), optional "
             "(?P<artist>) and (?P<disc>). If omitted, a small list of common "
             "patterns is auto-tried; the first one that matches every file wins.",
    )
    parser.add_argument(
        "--replaces", metavar="PATH",
        help="Library-relative path of an existing album folder this import "
             "should replace (passed through to music-organize.py --replaces).",
    )
    parser.add_argument("--library-root", default="/Volumes/Media/Music")
    parser.add_argument(
        "--archive-dir",
        help="Where music-organize.py archives replaced folders "
             "(default: <library-root>/../Music-Replaced).",
    )
    parser.add_argument(
        "--imports-dir",
        help="Where to stash preserved extras "
             "(default: <library-root>/../Music-Imports).",
    )
    parser.add_argument(
        "--preserve-extras", action="store_true",
        help="Move cue/log/rtf/etc. to --imports-dir before music-organize "
             "rmtrees the source. Off by default.",
    )
    parser.add_argument(
        "--organizer", default=os.path.expanduser("~/.local/bin/music-organize.py"),
        help="Path to music-organize.py (default: ~/.local/bin/music-organize.py).",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    source = os.path.abspath(os.path.expanduser(args.source))
    if not os.path.isdir(source):
        print(f"ERROR: source is not a directory: {source}", file=sys.stderr)
        sys.exit(1)

    library_root = os.path.abspath(os.path.expanduser(args.library_root))
    if not os.path.isdir(library_root):
        print(f"ERROR: library root not found: {library_root}", file=sys.stderr)
        sys.exit(1)

    imports_dir = (
        os.path.abspath(os.path.expanduser(args.imports_dir))
        if args.imports_dir
        else os.path.join(os.path.dirname(library_root), "Music-Imports")
    )

    audio_files = collect_audio(source)
    if not audio_files:
        print(f"ERROR: no audio files found in {source}", file=sys.stderr)
        sys.exit(1)

    existing_tags = {}
    for f in audio_files:
        try:
            existing_tags[f] = read_existing_tags(f)
        except Exception as e:
            print(f"ERROR: cannot read tags from {f}: {e}", file=sys.stderr)
            sys.exit(1)

    pattern = args.filename_pattern
    if not pattern:
        names = [os.path.basename(f) for f in audio_files]
        needs_parse = any(
            not (existing_tags[f].get("title") and existing_tags[f].get("track"))
            for f in audio_files
        )
        if needs_parse:
            pattern = autodetect_pattern(names)
            if not pattern:
                print(
                    "ERROR: no filename pattern matched all files and existing "
                    "tags are incomplete. Pass --filename-pattern explicitly.",
                    file=sys.stderr,
                )
                sys.exit(1)
            print(f"Auto-detected filename pattern: {pattern}")

    plans, errors = build_plan(audio_files, existing_tags, pattern, args, source)
    if errors:
        for e in errors:
            print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    compilation, comp_reason = resolve_compilation(args, plans)
    print(f"Album: {plans[0]['albumartist']} / {plans[0]['album']} ({plans[0]['genre']})")
    print(f"  {len(plans)} track(s), {plans[0]['disc_total']} disc(s)")
    print(f"  compilation={compilation} ({comp_reason})")

    failures = []
    for p in plans:
        rel = os.path.relpath(p["file"], source)
        if args.dry_run:
            disc_prefix = f"{p['disc']}-" if p["disc_total"] > 1 else ""
            print(
                f"  [dry-run] {rel}: track={disc_prefix}{p['track']:02d} "
                f"title='{p['title']}' artist='{p['artist']}'"
            )
            continue
        try:
            write_tags(p["file"], p, compilation)
            print(f"  tagged: {rel}")
        except Exception as e:
            print(f"  -> ERROR tagging {rel}: {e}", file=sys.stderr)
            failures.append((p["file"], e))

    if failures:
        print(
            f"\nAborting before music-organize.py: {len(failures)} file(s) "
            "failed to tag. Source left untouched.",
            file=sys.stderr,
        )
        sys.exit(2)

    promote_cover(source, args.dry_run)

    if args.preserve_extras:
        preserve_extras(
            source, imports_dir,
            plans[0]["albumartist"], plans[0]["album"], args.dry_run,
        )

    cmd = [
        args.organizer,
        "--library-root", library_root,
        "--on-collision", "replace",
    ]
    if args.replaces:
        cmd += ["--replaces", args.replaces]
    if args.archive_dir:
        cmd += ["--archive-dir", os.path.abspath(os.path.expanduser(args.archive_dir))]
    cmd.append(source)

    if args.dry_run:
        # music-organize would read whatever's currently on disk (no tags written
        # in dry-run), so running it here would print nonsense destinations.
        print(f"\n--> [dry-run] would invoke: {' '.join(cmd)}")
        sys.exit(0)

    print(f"\n--> music-organize.py: {' '.join(cmd)}")
    sys.stdout.flush()
    result = subprocess.run(cmd)
    sys.exit(result.returncode)


if __name__ == "__main__":
    main()
