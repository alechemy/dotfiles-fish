#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "mutagen",
# ]
# ///

import argparse
import os
import re
import sys

from mutagen.mp4 import MP4, MP4Cover


def iter_m4as(paths):
    """Yield every .m4a path under the given file/dir paths, sorted within each dir."""
    for path in paths:
        if os.path.isdir(path):
            for root, _dirs, files in os.walk(path):
                for fn in sorted(files):
                    if fn.lower().endswith(".m4a"):
                        yield os.path.join(root, fn)
        elif os.path.isfile(path) and path.lower().endswith(".m4a"):
            yield path


def first_embedded_cover(paths):
    """Return the first MP4Cover found in any .m4a under paths, or None."""
    for fp in iter_m4as(paths):
        try:
            covers = MP4(fp).get("covr")
        except Exception:
            continue
        if covers:
            return covers[0]
    return None


_FEAT_RE = re.compile(r"\s+(?:feat\.?|ft\.?|featuring|with)\s+", re.I)


def _norm_artist(s):
    """Strip 'feat./featuring/with' suffixes; lowercase; collapse whitespace."""
    return _FEAT_RE.split(str(s or ""), maxsplit=1)[0].strip().lower()


def _read_artists(filepath):
    """Return (album_artist_norm, track_artist_norm) for one m4a, either may be None."""
    try:
        a = MP4(filepath)
    except Exception:
        return (None, None)
    aa = a.get("aART"); ta = a.get("\xa9ART")
    return (_norm_artist(aa[0]) if aa else None,
            _norm_artist(ta[0]) if ta else None)


def auto_compilation(paths):
    """True if the album looks like a compilation.

    Two signals, in order:
      1. If the album-level artist (aART) is "Various Artists", it's a compilation
         — Qobuz uses this label even when every track's per-track artist is the
         same composer (e.g. Rob Simonsen's *Spectacular Now* OST has aART
         "Various Artists" but \\xa9ART "Rob Simonsen" on every track).
      2. Otherwise, count distinct per-track artists. Multi-artist → compilation;
         single-artist → not (e.g. Bo Burnham's INSIDE)."""
    aart, tart = set(), set()
    for fp in iter_m4as(paths):
        aa, ta = _read_artists(fp)
        if aa: aart.add(aa)
        if ta: tart.add(ta)
    if any(a == "various artists" for a in aart):
        return True
    return len(tart) > 1


def process_file(
    filepath,
    genre,
    is_compilation=None,
    album_artist=None,
    album=None,
    year=None,
    cover=None,
):
    if not filepath.lower().endswith(".m4a"):
        return

    try:
        audio = MP4(filepath)
        actions = []

        audio["\xa9gen"] = genre
        actions.append("genre")

        if is_compilation is True:
            audio["cpil"] = True  # bare bool: mutagen renders a list as truthy
            actions.append("compilation=true")
        elif is_compilation is False:
            audio["cpil"] = False
            actions.append("compilation=false")
        # is_compilation None: leave cpil at whatever the source set it to

        if album_artist:
            audio["aART"] = album_artist
            actions.append(f"albumartist={album_artist}")

        if album:
            audio["\xa9alb"] = album
            actions.append(f"album={album}")

        if year:
            audio["\xa9day"] = str(year)
            actions.append(f"year={year}")

        if cover is not None:
            audio["covr"] = [cover]
            actions.append("unified cover")

        if "\xa9cmt" in audio:
            del audio["\xa9cmt"]
            actions.append("cleared comment")

        if "cprt" in audio:
            del audio["cprt"]
            actions.append("cleared copyright")

        audio.save()
        print(f"  -> Updated ({' & '.join(actions)}): {filepath}")

    except Exception as e:
        print(f"  -> ERROR: Could not process {filepath}: {e}", file=sys.stderr)


parser = argparse.ArgumentParser(description="A simple M4A genre and tag editor.")
parser.add_argument("--genre", required=True, help="The genre to set for the files.")
comp_group = parser.add_mutually_exclusive_group()
comp_group.add_argument(
    "--compilation", dest="compilation", action="store_const", const=True, default=None,
    help="Force compilation flag on (cpil=True).",
)
comp_group.add_argument(
    "--no-compilation", dest="compilation", action="store_const", const=False,
    help="Force compilation flag off (cpil=False).",
)
parser.add_argument("--album-artist", dest="album_artist",
                    help="Override album artist (aART) on every track.")
parser.add_argument("--album", help="Override album name (©alb) on every track.")
parser.add_argument("--year", help="Override year (©day) on every track.")
parser.add_argument(
    "--unify-cover",
    action="store_true",
    help="Embed the first track's cover art into every track.",
)
parser.add_argument(
    "paths", nargs="+", help="A list of .m4a files or directories to process."
)

args = parser.parse_args()

unified_cover = None
if args.unify_cover:
    unified_cover = first_embedded_cover(args.paths)
    if unified_cover is None:
        print("WARNING: --unify-cover requested but no embedded cover found.",
              file=sys.stderr)
    else:
        fmt = "JPEG" if unified_cover.imageformat == MP4Cover.FORMAT_JPEG else "PNG"
        print(f"Unifying cover from first track ({len(bytes(unified_cover))} bytes, {fmt}).")

# Auto-detect compilation for Soundtrack genre when not explicitly set.
# Single-artist soundtracks (e.g. Bo Burnham's INSIDE) are not compilations;
# multi-artist soundtracks (e.g. Lost in Translation OST) are.
final_compilation = args.compilation
if args.compilation is None and args.genre == "Soundtrack":
    final_compilation = auto_compilation(args.paths)
    print(f"Auto-detected Soundtrack compilation = {final_compilation} "
          f"(from per-track artist tags)")

extras = []
if final_compilation is True:
    extras.append("compilation=true")
elif final_compilation is False:
    extras.append("compilation=false")
if args.album_artist:
    extras.append(f"albumartist='{args.album_artist}'")
if args.album:
    extras.append(f"album='{args.album}'")
if args.year:
    extras.append(f"year='{args.year}'")
if unified_cover is not None:
    extras.append("unified cover")
suffix = (" + " + ", ".join(extras)) if extras else ""
print(f"Setting genre='{args.genre}'{suffix} and clearing comment/copyright tags...")

for path in args.paths:
    if os.path.isdir(path):
        print(f"Processing directory (recursively): {path}")
    elif not os.path.isfile(path):
        print(f"  -> WARNING: Path not found, skipping: {path}", file=sys.stderr)
        continue

for fp in iter_m4as(args.paths):
    process_file(
        fp,
        args.genre,
        is_compilation=final_compilation,
        album_artist=args.album_artist,
        album=args.album,
        year=args.year,
        cover=unified_cover,
    )

print("Done.")
