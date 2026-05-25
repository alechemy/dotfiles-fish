#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "mutagen",
# ]
# ///

import argparse
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from _music_tags import is_compilation_album, norm_artist  # noqa: E402

from mutagen.mp4 import MP4, MP4Cover  # noqa: E402


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


def _read_artists(filepath):
    """Return (album_artist_norm, track_artist_norm) for one m4a, either may be None."""
    try:
        a = MP4(filepath)
    except Exception:
        return (None, None)
    aa = a.get("aART"); ta = a.get("\xa9ART")
    return (norm_artist(aa[0]) if aa else None,
            norm_artist(ta[0]) if ta else None)


def auto_compilation(paths):
    """True if the album looks like a compilation; see _music_tags.compilation_signal."""
    aart, tart = set(), set()
    for fp in iter_m4as(paths):
        aa, ta = _read_artists(fp)
        if aa: aart.add(aa)
        if ta: tart.add(ta)
    return is_compilation_album(aart, tart)


def process_file(
    filepath,
    genre=None,
    is_compilation=None,
    album_artist=None,
    artist=None,
    album=None,
    year=None,
    cover=None,
):
    if not filepath.lower().endswith(".m4a"):
        return

    try:
        audio = MP4(filepath)
        actions = []

        if genre is not None:
            audio["\xa9gen"] = genre
            actions.append(f"genre={genre}")

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

        if artist:
            audio["\xa9ART"] = artist
            actions.append(f"artist={artist}")

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
parser.add_argument(
    "--genre",
    help="Genre to set on every track (e.g. for a fresh tag pass). Omit to "
         "leave the genre tag untouched (e.g. for an incremental retag that "
         "only touches artist or album fields).",
)
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
parser.add_argument("--artist",
                    help="Override per-track artist (©ART) on every track. "
                         "Useful for single-artist albums where the album "
                         "artist and per-track artist should match.")
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

parts = []
if args.genre:
    parts.append(f"genre='{args.genre}'")
if final_compilation is True:
    parts.append("compilation=true")
elif final_compilation is False:
    parts.append("compilation=false")
if args.album_artist:
    parts.append(f"albumartist='{args.album_artist}'")
if args.artist:
    parts.append(f"artist='{args.artist}'")
if args.album:
    parts.append(f"album='{args.album}'")
if args.year:
    parts.append(f"year='{args.year}'")
if unified_cover is not None:
    parts.append("unified cover")
if not parts:
    print("ERROR: nothing to do — pass at least one of --genre, "
          "--album-artist, --artist, --album, --year, or --unify-cover.",
          file=sys.stderr)
    sys.exit(1)
print(f"Setting {', '.join(parts)} and clearing comment/copyright tags...")

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
        artist=args.artist,
        album=args.album,
        year=args.year,
        cover=unified_cover,
    )

print("Done.")
