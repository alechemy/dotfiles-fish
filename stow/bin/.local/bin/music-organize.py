#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "mutagen",
# ]
# ///
"""Organize audio files into an Artist/Album library tree.

Replaces Music.app's "Automatically Add to Music" auto-organize step in the
riptag pipeline. For each file it reads the metadata tags and moves the file to:

    <library-root>/<Album Artist | Compilations>/<Album>/<[D-]NN Title.ext>

This reimplements Music.app's "Keep Media folder organized" logic: the compilation flag wins the artist folder,
multi-disc sets are flattened to `D-NN Title` filenames, and filesystem-unsafe
characters are replaced with `_`. The character/truncation behavior was
verified against the existing library, which is itself Music.app's output:
`/`, `:`, `?` all become `_` (e.g. `Speakerboxxx_The Love Below`) and there is
no component truncation.

Usage:
    music-organize.py --library-root DIR [options] SOURCE [SOURCE ...]

SOURCE is an album folder (walked recursively) or a single audio file.
"""

import argparse
import os
import re
import shutil
import sys

from mutagen.flac import FLAC
from mutagen.mp3 import MP3
from mutagen.mp4 import MP4

AUDIO_EXTS = {".m4a", ".flac", ".mp3"}
IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp")
COVER_STEMS = ("cover", "folder", "front", "album")  # preference order
ILLEGAL = set('/\\:*?"<>|')  # replaced with "_"
DIR_MODE = 0o775
FILE_MODE = 0o664


# ----------------------------------------------------------------- tag reading
def _first(value):
    if isinstance(value, (list, tuple)):
        return value[0] if value else None
    return value


def _int(value):
    """Leading integer from '5', '05', '5/12', 5, etc. 0 if none."""
    if value is None:
        return 0
    if isinstance(value, int):
        return value
    m = re.match(r"\s*(\d+)", str(value))
    return int(m.group(1)) if m else 0


def _truthy(value):
    return str(value or "").strip().lower() in ("1", "true", "yes")


def read_tags(path):
    """Return {albumartist, artist, album, title, track, disc, disctotal, compilation}."""
    ext = os.path.splitext(path)[1].lower()
    t = dict(
        albumartist=None,
        artist=None,
        album=None,
        title=None,
        track=0,
        disc=0,
        disctotal=0,
        compilation=False,
    )

    if ext == ".m4a":
        a = MP4(path)
        t["albumartist"] = _first(a.get("aART"))
        t["artist"] = _first(a.get("\xa9ART"))
        t["album"] = _first(a.get("\xa9alb"))
        t["title"] = _first(a.get("\xa9nam"))
        trkn = _first(a.get("trkn")) or (0, 0)
        disk = _first(a.get("disk")) or (0, 0)
        t["track"] = trkn[0] if len(trkn) > 0 else 0
        t["disc"] = disk[0] if len(disk) > 0 else 0
        t["disctotal"] = disk[1] if len(disk) > 1 else 0
        t["compilation"] = bool(_first(a.get("cpil")) or False)

    elif ext == ".flac":
        a = FLAC(path)
        get = lambda k: _first(a.get(k))
        t["albumartist"] = get("albumartist")
        t["artist"] = get("artist")
        t["album"] = get("album")
        t["title"] = get("title")
        t["track"] = _int(get("tracknumber"))
        t["disc"] = _int(get("discnumber"))
        t["disctotal"] = _int(get("disctotal") or get("totaldiscs"))
        t["compilation"] = _truthy(get("compilation"))

    elif ext == ".mp3":
        tags = MP3(path).tags

        def txt(frame):
            fr = tags.get(frame) if tags else None
            return str(fr.text[0]) if fr and getattr(fr, "text", None) else None

        t["albumartist"] = txt("TPE2")
        t["artist"] = txt("TPE1")
        t["album"] = txt("TALB")
        t["title"] = txt("TIT2")
        t["track"] = _int(txt("TRCK"))
        tpos = txt("TPOS") or ""
        t["disc"] = _int(tpos)
        t["disctotal"] = _int(tpos.split("/", 1)[1]) if "/" in tpos else 0
        t["compilation"] = _truthy(txt("TCMP"))

    else:
        raise ValueError(f"unsupported extension {ext}")

    for key in ("albumartist", "artist", "album", "title"):
        if t[key]:
            t[key] = str(t[key]).strip() or None
    return t


# ----------------------------------------------------------------- path building
def sanitize(component, fallback):
    """Make a tag value safe for one path component (Music.app-compatible)."""
    s = "".join("_" if c in ILLEGAL else c for c in str(component or ""))
    s = "".join(c for c in s if ord(c) >= 32)  # drop control characters
    s = s.strip().rstrip(".").strip()  # no trailing dot/space
    return s or fallback


def artist_folder(tags):
    if tags["compilation"]:
        return "Compilations"
    return sanitize(tags["albumartist"] or tags["artist"], "Unknown Artist")


def album_folder(tags):
    return sanitize(tags["album"], "Unknown Album")


def dest_filename(tags, ext):
    title = sanitize(tags["title"], "Untitled")
    track, disc, disctotal = tags["track"], tags["disc"], tags["disctotal"]
    if not track:
        return f"{title}{ext}"
    if (disctotal and disctotal > 1) or (disc and disc > 1):
        return f"{disc or 1}-{track:02d} {title}{ext}"
    return f"{track:02d} {title}{ext}"


# ----------------------------------------------------------------- filesystem
def iter_audio(folder):
    for root, dirs, files in os.walk(folder):
        dirs.sort()
        for fn in sorted(files):
            if fn.startswith("._"):  # macOS AppleDouble sidecar
                continue
            if os.path.splitext(fn)[1].lower() in AUDIO_EXTS:
                yield os.path.join(root, fn)


def find_cover(folder):
    """Best cover image at the top level of an album folder, or None."""
    best = None
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return None
    for fn in names:
        if fn.startswith("._"):
            continue
        stem, ext = os.path.splitext(fn)
        full = os.path.join(folder, fn)
        if ext.lower() not in IMAGE_EXTS or not os.path.isfile(full):
            continue
        rank = (
            COVER_STEMS.index(stem.lower())
            if stem.lower() in COVER_STEMS
            else len(COVER_STEMS)
        )
        if best is None or rank < best[0]:
            best = (rank, full)
    return best[1] if best else None


def unique_path(path):
    """Append ' 1', ' 2', ... before the extension until the path is free."""
    if not os.path.exists(path):
        return path
    stem, ext = os.path.splitext(path)
    n = 1
    while os.path.exists(f"{stem} {n}{ext}"):
        n += 1
    return f"{stem} {n}{ext}"


def chmod_quiet(path, mode):
    try:
        os.chmod(path, mode)
    except OSError:
        pass


# ----------------------------------------------------------------- organizing
def organize_source(source, library_root, policy, dry_run, manifest, stats):
    is_dir = os.path.isdir(source)
    if is_dir:
        audio = list(iter_audio(source))
    elif os.path.splitext(source)[1].lower() in AUDIO_EXTS:
        audio = [source]
    else:
        audio = []
    if not audio:
        print(f"  -> WARNING: no audio files found in {source}", file=sys.stderr)
        stats["failed"].append(source)
        return

    plan, failures = [], []  # plan: (src, album_dir, filename)
    for f in audio:
        try:
            tags = read_tags(f)
        except Exception as e:
            print(f"  -> ERROR reading tags from {f}: {e}", file=sys.stderr)
            failures.append(f)
            continue
        album_dir = os.path.join(library_root, artist_folder(tags), album_folder(tags))
        plan.append((f, album_dir, dest_filename(tags, os.path.splitext(f)[1].lower())))

    if not plan:
        stats["failed"].append(source)
        return

    album_dirs = sorted({d for _, d, _ in plan})

    # album-level collision handling
    skipped = set()
    for d in album_dirs:
        if not os.path.exists(d):
            continue
        if policy == "replace":
            if is_dir and os.path.abspath(source).startswith(
                os.path.abspath(d) + os.sep
            ):
                print(
                    f"  -> ERROR: refusing to replace {d} (it contains the source)",
                    file=sys.stderr,
                )
                skipped.add(d)
                continue
            print(f"  -> replacing existing album: {d}")
            if not dry_run:
                shutil.rmtree(d)
        elif policy == "skip":
            print(f"  -> skipping, album already exists: {d}")
            skipped.add(d)
        # "counter": no album-level action; collisions resolved per file below

    # move files
    moved = 0
    for src_file, album_dir, filename in plan:
        if album_dir in skipped:
            failures.append(src_file)  # source not fully consumed
            continue
        dest = os.path.join(album_dir, filename)
        if dry_run:
            print(f"  -> [dry-run] {dest}")
            moved += 1
            continue
        if policy == "counter" or os.path.exists(dest):
            dest = unique_path(dest)
        try:
            os.makedirs(album_dir, exist_ok=True)
            shutil.move(src_file, dest)
        except Exception as e:
            print(f"  -> ERROR moving {src_file}: {e}", file=sys.stderr)
            failures.append(src_file)
            continue
        chmod_quiet(dest, FILE_MODE)
        print(f"  -> {dest}")
        moved += 1

    # artwork + permissions + manifest
    cover = find_cover(source) if is_dir else None
    for d in album_dirs:
        if d in skipped:
            continue
        manifest.add(d)
        if dry_run:
            if cover:
                print(
                    f"  -> [dry-run] {os.path.join(d, 'cover' + os.path.splitext(cover)[1].lower())}"
                )
            continue
        chmod_quiet(d, DIR_MODE)
        chmod_quiet(os.path.dirname(d), DIR_MODE)  # artist folder
        if cover:
            has_cover = any(
                os.path.exists(os.path.join(d, "cover" + e)) for e in IMAGE_EXTS
            )
            if policy == "replace" or not has_cover:
                cover_dest = os.path.join(
                    d, "cover" + os.path.splitext(cover)[1].lower()
                )
                try:
                    shutil.copy2(cover, cover_dest)
                    chmod_quiet(cover_dest, FILE_MODE)
                    print(f"  -> {cover_dest}")
                except Exception as e:
                    print(
                        f"  -> WARNING: could not copy cover art: {e}", file=sys.stderr
                    )

    stats["moved"] += moved

    # remove the consumed source folder (fixes the empty-husk problem)
    if is_dir and not failures and not dry_run:
        try:
            shutil.rmtree(source)
            print(f"  -> removed empty source folder: {source}")
        except Exception as e:
            print(
                f"  -> WARNING: could not remove source {source}: {e}", file=sys.stderr
            )
    elif failures:
        stats["failed"].append(source)
        print(f"  -> {len(failures)} item(s) left behind in {source}", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(
        description="Organize audio files into an Artist/Album library tree."
    )
    parser.add_argument(
        "--library-root",
        required=True,
        help="Root of the organized library (the 'Music' folder).",
    )
    parser.add_argument(
        "--on-collision",
        choices=["replace", "skip", "counter"],
        default="replace",
        help="What to do when the destination album already exists (default: replace).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned moves without touching the filesystem.",
    )
    parser.add_argument(
        "--manifest",
        help="Write the list of destination album folders here "
        "(one per line) for downstream permission fixes.",
    )
    parser.add_argument(
        "sources", nargs="+", help="Album folders or audio files to organize."
    )
    args = parser.parse_args()

    library_root = os.path.abspath(os.path.expanduser(args.library_root))
    if not os.path.isdir(library_root):
        print(
            f"ERROR: library root is not a directory: {library_root}", file=sys.stderr
        )
        sys.exit(1)

    stats = {"moved": 0, "failed": []}
    manifest = set()
    for src in args.sources:
        src = os.path.abspath(os.path.expanduser(src))
        if not os.path.exists(src):
            print(f"ERROR: source not found: {src}", file=sys.stderr)
            stats["failed"].append(src)
            continue
        print(f"Organizing: {src}")
        organize_source(
            src, library_root, args.on_collision, args.dry_run, manifest, stats
        )

    if args.manifest and not args.dry_run:
        with open(args.manifest, "w", encoding="utf-8") as f:
            for d in sorted(manifest):
                f.write(d + "\n")

    print(f"\nDone. {stats['moved']} file(s) organized into {library_root}.")
    if stats["failed"]:
        print(
            f"{len(stats['failed'])} source(s) had problems and were left in place.",
            file=sys.stderr,
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
