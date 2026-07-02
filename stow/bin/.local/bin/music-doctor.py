#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "mutagen",
# ]
# ///
"""Library health checks for a Music.app-shaped library tree.

Walks a library root (default /Volumes/Media/Music), reads tags via mutagen,
runs a battery of checks for corruption, inconsistent metadata, duplicates,
empty folders, misplaced files, and quality issues, and writes findings to a
SQLite store at ~/.local/share/music-doctor/db.sqlite3.

The store backs three workflows:

  scan       run all checks against the library, record results in a new run
  report     show findings from the last (or specified) run, honoring dismissals
  stats      aggregate library statistics only (no per-file checks)
  dismiss    mark a finding hash as known-acceptable (subsequent runs hide it)
  undismiss  un-mark a previously dismissed finding
  fix        apply safe corrective actions (dry-run unless --apply)
  history    list past runs

Findings have a stable hash derived from (kind, sorted target paths,
salient value). Dismissing a hash suppresses it from future reports while
still recording every occurrence in run history.

Default invocation:

    music-doctor.py scan --json   # for programmatic / skill consumers
    music-doctor.py scan          # human-readable summary
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import unicodedata
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Iterable, Optional

# Reuse compilation detection from the shared tagging helpers.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from _music_tags import compilation_signal, norm_artist  # noqa: E402

from mutagen.flac import FLAC  # noqa: E402
from mutagen.mp3 import MP3  # noqa: E402
from mutagen.mp4 import MP4  # noqa: E402

# ----------------------------------------------------------------- constants
DEFAULT_LIBRARY_ROOT = "/Volumes/Media/Music"
DEFAULT_DB_PATH = os.path.expanduser("~/.local/share/music-doctor/db.sqlite3")

AUDIO_EXTS = {".m4a", ".flac", ".mp3"}
IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp")
COVER_STEMS = ("cover", "folder", "front", "album")
ILLEGAL_PATH_CHARS = set('/\\:*?"<>|')

# Mirror import-album.py / riptag.fish.
ALLOWED_GENRES = (
    "Ambient", "Bluegrass", "Classical", "Country", "Electronic",
    "Experimental", "Folk", "Hip-Hop", "Jazz", "Lo-Fi", "Mashup",
    "Pop", "R&B", "Reggae", "Rock", "Soundtrack", "Unknown",
)

# Quality bands. <320kbps lossy is flagged as "low"; =320kbps lossy is an
# "upgrade candidate" (lossless replacement target).
QUALITY_LOW_KBPS = 320
QUALITY_UPGRADE_KBPS = 320

# Filenames produced by music-organize.py: `NN Title.ext` or `D-NN Title.ext`.
# Track number is zero-padded to 2 digits but not truncated, so 100-track
# playlists like Disney100 naturally produce 3-digit prefixes; allow ≥2 digits.
CANONICAL_FILENAME_RE = re.compile(r"^(?:\d+-)?\d{2,}\s.+\.(?:m4a|flac|mp3)$", re.I)

# Folders we don't recurse into when looking for stray non-audio files.
KNOWN_EXTRA_NAMES = {".DS_Store"}

# Top-level entries that aren't artists and should be ignored entirely.
# Navidrome writes its smart playlists into `Smart Playlists/` next to artist
# folders; it's a sibling of the music tree, not a misplaced empty artist.
SKIP_TOP_LEVEL = {"Smart Playlists"}

# Hidden / sidecar prefixes Music.app and SMB sprinkle around.
HIDDEN_PREFIXES = (".", "._")

ERROR, WARNING, INFO = "error", "warning", "info"
SEVERITY_ORDER = (ERROR, WARNING, INFO)


# ----------------------------------------------------------------- data classes
@dataclass
class FileInfo:
    path: str
    size: int
    ext: str
    tags: dict[str, Any]
    duration: float
    bitrate: int
    sample_rate: int
    codec: str
    lossless: bool
    has_embedded_cover: bool = False
    read_error: Optional[str] = None


@dataclass
class AlbumInfo:
    path: str
    artist_folder: str
    album_folder: str
    audio: list[str] = field(default_factory=list)
    covers: list[str] = field(default_factory=list)
    extras: list[str] = field(default_factory=list)  # non-audio, non-cover, non-hidden
    subdirs: list[str] = field(default_factory=list)  # nested folders inside album


@dataclass
class Finding:
    kind: str
    severity: str
    target_kind: str  # file | album | artist | folder | library | pair
    targets: list[str]
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    def hash(self) -> str:
        salient = self.details.get("salient", "")
        sorted_targets = sorted(self.targets)
        payload = f"{self.kind}::{','.join(sorted_targets)}::{salient}"
        return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["hash"] = self.hash()
        return d


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


def _truthy(v):
    return str(v or "").strip().lower() in ("1", "true", "yes")


def read_one(path: str) -> FileInfo:
    """Read tags + technical info for one audio file.

    Never raises — on failure, the returned FileInfo has read_error set so
    the unreadable check can pick it up downstream.
    """
    ext = os.path.splitext(path)[1].lower()
    try:
        size = os.path.getsize(path)
    except OSError as e:
        return FileInfo(path, 0, ext, {}, 0.0, 0, 0, "", False, read_error=f"stat: {e}")

    tags: dict[str, Any] = {}
    duration = 0.0
    bitrate = 0
    sample_rate = 0
    codec = ext.lstrip(".")
    lossless = False
    has_cover = False

    try:
        if ext == ".m4a":
            a = MP4(path)
            tags["albumartist"] = _first(a.get("aART"))
            tags["artist"] = _first(a.get("\xa9ART"))
            tags["album"] = _first(a.get("\xa9alb"))
            tags["title"] = _first(a.get("\xa9nam"))
            tags["genre"] = _first(a.get("\xa9gen"))
            tags["year"] = _first(a.get("\xa9day"))
            tags["comment"] = _first(a.get("\xa9cmt"))
            tags["copyright"] = _first(a.get("cprt"))
            trkn = _first(a.get("trkn")) or (0, 0)
            disk = _first(a.get("disk")) or (0, 0)
            tags["track"] = trkn[0] if len(trkn) > 0 else 0
            tags["track_total"] = trkn[1] if len(trkn) > 1 else 0
            tags["disc"] = disk[0] if len(disk) > 0 else 0
            tags["disc_total"] = disk[1] if len(disk) > 1 else 0
            tags["compilation"] = bool(_first(a.get("cpil")) or False)
            i = a.info
            duration = float(getattr(i, "length", 0) or 0)
            bitrate = int(getattr(i, "bitrate", 0) or 0) // 1000
            sample_rate = int(getattr(i, "sample_rate", 0) or 0)
            codec = (getattr(i, "codec", "") or "").lower() or "aac"
            lossless = codec == "alac"
            has_cover = bool(a.get("covr"))

        elif ext == ".flac":
            a = FLAC(path)
            g = lambda k: _first(a.get(k))
            tags["albumartist"] = g("albumartist")
            tags["artist"] = g("artist")
            tags["album"] = g("album")
            tags["title"] = g("title")
            tags["genre"] = g("genre")
            tags["year"] = g("date") or g("year")
            tags["comment"] = g("comment")
            tags["copyright"] = g("copyright")
            tags["track"] = _int(g("tracknumber"))
            tags["track_total"] = _int(g("totaltracks") or g("tracktotal"))
            tags["disc"] = _int(g("discnumber"))
            tags["disc_total"] = _int(g("totaldiscs") or g("disctotal"))
            tags["compilation"] = _truthy(g("compilation"))
            i = a.info
            duration = float(getattr(i, "length", 0) or 0)
            bitrate = int(getattr(i, "bitrate", 0) or 0) // 1000
            sample_rate = int(getattr(i, "sample_rate", 0) or 0)
            codec = "flac"
            lossless = True
            has_cover = bool(getattr(a, "pictures", []))

        elif ext == ".mp3":
            m = MP3(path)
            t = m.tags

            def txt(frame):
                fr = t.get(frame) if t else None
                return str(fr.text[0]) if fr and getattr(fr, "text", None) else None

            tags["albumartist"] = txt("TPE2")
            tags["artist"] = txt("TPE1")
            tags["album"] = txt("TALB")
            tags["title"] = txt("TIT2")
            tags["genre"] = txt("TCON")
            tags["year"] = txt("TDRC") or txt("TYER")
            tags["comment"] = None
            if t:
                comms = t.getall("COMM")
                if comms:
                    tags["comment"] = str(comms[0].text[0]) if comms[0].text else None
            tags["copyright"] = txt("TCOP")
            trk = txt("TRCK") or ""
            tags["track"] = _int(trk.split("/", 1)[0]) if trk else 0
            tags["track_total"] = _int(trk.split("/", 1)[1]) if "/" in trk else 0
            tpos = txt("TPOS") or ""
            tags["disc"] = _int(tpos.split("/", 1)[0]) if tpos else 0
            tags["disc_total"] = _int(tpos.split("/", 1)[1]) if "/" in tpos else 0
            tags["compilation"] = _truthy(txt("TCMP"))
            i = m.info
            duration = float(getattr(i, "length", 0) or 0)
            bitrate = int(getattr(i, "bitrate", 0) or 0) // 1000
            sample_rate = int(getattr(i, "sample_rate", 0) or 0)
            codec = "mp3"
            lossless = False
            has_cover = bool(t.getall("APIC")) if t else False
        else:
            return FileInfo(path, size, ext, {}, 0.0, 0, 0, "", False,
                            read_error=f"unsupported extension {ext}")

        # Normalize whitespace on string-valued tags.
        for k in ("albumartist", "artist", "album", "title", "genre", "year",
                  "comment", "copyright"):
            v = tags.get(k)
            if isinstance(v, str):
                tags[k] = v.strip() or None
        return FileInfo(path, size, ext, tags, duration, bitrate, sample_rate,
                        codec, lossless, has_embedded_cover=has_cover)
    except Exception as e:  # noqa: BLE001 -- mutagen raises many shapes
        return FileInfo(path, size, ext, {}, 0.0, 0, 0, codec, lossless,
                        read_error=f"{type(e).__name__}: {e}")


def decode_check(path: str) -> Optional[str]:
    """Return error message if ffmpeg can't fully decode the audio, else None."""
    try:
        proc = subprocess.run(
            ["ffmpeg", "-v", "error", "-xerror", "-i", path, "-f", "null", "-"],
            capture_output=True, text=True, timeout=120,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        return f"ffmpeg failed: {e}"
    # ffmpeg exits 0 on some decode errors, so treat any stderr as failure too.
    stderr = proc.stderr.strip()
    if proc.returncode != 0 or stderr:
        msg = stderr.splitlines()[-1] if stderr else "non-zero exit"
        return f"decode: {msg}"
    return None


def decode_check_parallel(paths: list[str], workers: int) -> dict[str, str]:
    """Full-decode many files in parallel; returns path -> error for failures."""
    errors: dict[str, str] = {}
    if not paths:
        return errors
    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        futures = {ex.submit(decode_check, p): p for p in paths}
        for fut in as_completed(futures):
            p = futures[fut]
            try:
                err = fut.result()
            except Exception as e:  # noqa: BLE001
                err = f"worker: {e}"
            if err:
                errors[p] = err
    return errors


# ----------------------------------------------------------------- walking
def is_hidden(name: str) -> bool:
    return name.startswith(HIDDEN_PREFIXES)


def walk_library(root: str) -> tuple[list[AlbumInfo], list[str], list[str]]:
    """Return (albums, empty_artists, stray_files).

    empty_artists are artist folders with no album subfolders or no audio.
    stray_files are top-level non-hidden files at the library root (unusual).
    """
    albums: list[AlbumInfo] = []
    empty_artists: list[str] = []
    stray_files: list[str] = []

    try:
        top_entries = sorted(os.listdir(root))
    except OSError as e:
        print(f"ERROR: cannot list library root {root}: {e}", file=sys.stderr)
        sys.exit(2)

    for name in top_entries:
        if is_hidden(name):
            continue
        if name in SKIP_TOP_LEVEL:
            continue
        full = os.path.join(root, name)
        if os.path.isfile(full):
            stray_files.append(full)
            continue
        if not os.path.isdir(full):
            continue

        try:
            sub_entries = sorted(os.listdir(full))
        except OSError:
            continue

        artist_had_album = False
        for sub in sub_entries:
            if is_hidden(sub):
                continue
            album_path = os.path.join(full, sub)
            if not os.path.isdir(album_path):
                continue
            artist_had_album = True
            albums.append(scan_album_dir(album_path, name, sub))
        if not artist_had_album:
            empty_artists.append(full)

    return albums, empty_artists, stray_files


def scan_album_dir(path: str, artist_folder: str, album_folder: str) -> AlbumInfo:
    audio: list[str] = []
    covers: list[str] = []
    extras: list[str] = []
    try:
        entries = sorted(os.listdir(path))
    except OSError:
        return AlbumInfo(path, artist_folder, album_folder)

    subdirs: list[str] = []
    for name in entries:
        if is_hidden(name) or name in KNOWN_EXTRA_NAMES:
            continue
        full = os.path.join(path, name)
        if os.path.isdir(full):
            # The canonical library shape has no nested folders inside an album
            # (organize.py flattens multi-disc sets to D-NN filenames). Record
            # the subfolder for a structural finding, and still collect any
            # audio inside so duplicate/quality checks see it.
            subdirs.append(full)
            for inner_root, dirs, files in os.walk(full):
                dirs[:] = [d for d in dirs if not is_hidden(d)]
                for f in files:
                    if is_hidden(f) or f in KNOWN_EXTRA_NAMES:
                        continue
                    inner = os.path.join(inner_root, f)
                    ext = os.path.splitext(f)[1].lower()
                    if ext in AUDIO_EXTS:
                        audio.append(inner)
                    else:
                        extras.append(inner)
            continue
        ext = os.path.splitext(name)[1].lower()
        if ext in AUDIO_EXTS:
            audio.append(full)
        elif ext in IMAGE_EXTS:
            stem = os.path.splitext(name)[0].lower()
            # Match music-organize.py's recognition pattern: exact stem only.
            if stem in COVER_STEMS:
                covers.append(full)
            else:
                extras.append(full)
        else:
            extras.append(full)

    return AlbumInfo(path, artist_folder, album_folder, audio, covers, extras,
                     subdirs)


def read_files_parallel(paths: Iterable[str], workers: int) -> dict[str, FileInfo]:
    """Read tags for many files in parallel; preserves a path -> FileInfo map."""
    out: dict[str, FileInfo] = {}
    paths = list(paths)
    if not paths:
        return out
    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        futures = {ex.submit(read_one, p): p for p in paths}
        for fut in as_completed(futures):
            p = futures[fut]
            try:
                out[p] = fut.result()
            except Exception as e:  # noqa: BLE001
                out[p] = FileInfo(p, 0, os.path.splitext(p)[1].lower(),
                                  {}, 0.0, 0, 0, "", False, f"worker: {e}")
    return out


# ----------------------------------------------------------------- checks
def add(findings: list[Finding], **kw) -> None:
    findings.append(Finding(**kw))


def sanitize_for_path(s: str) -> str:
    """Mirror music-organize.py's sanitize(): replace illegal chars with '_',
    drop control characters, strip leading/trailing whitespace AND trailing
    periods (then whitespace again — '.txt ' would become '.txt' after one
    strip and '.txt' after the second strip is a no-op, but 'foo. ' becomes
    'foo' through the chain)."""
    s = "".join("_" if c in ILLEGAL_PATH_CHARS else c for c in str(s or ""))
    s = "".join(c for c in s if ord(c) >= 32)
    return s.strip().rstrip(".").strip()


def nfc(s: str) -> str:
    """Normalize to NFC. APFS/SMB report names in NFD; tags are typically NFC.

    Without this normalization, visually identical strings like 'Beyoncé' (NFC,
    U+00E9) and 'Beyoncé' (NFD, U+0065 U+0301) compare unequal and produce
    false-positive path_tag_mismatch findings.
    """
    return unicodedata.normalize("NFC", s or "")


def check_files(albums: list[AlbumInfo], files: dict[str, FileInfo],
                deep: bool, workers: int = 8) -> list[Finding]:
    findings: list[Finding] = []

    decode_errors: dict[str, str] = {}
    if deep:
        decode_errors = decode_check_parallel(
            [fi.path for fi in files.values() if not fi.read_error and fi.size > 0],
            workers,
        )

    # Build an album-grouped view for downstream album-level checks.
    by_album: dict[str, list[FileInfo]] = defaultdict(list)
    for alb in albums:
        for p in alb.audio:
            fi = files.get(p)
            if fi:
                by_album[alb.path].append(fi)

    for fi in files.values():
        # --- unreadable / zero-byte / silent ---
        if fi.read_error:
            add(findings, kind="unreadable", severity=ERROR, target_kind="file",
                targets=[fi.path], message=f"can't read tags: {fi.read_error}",
                details={"salient": "unreadable", "error": fi.read_error})
            continue
        if fi.size == 0:
            add(findings, kind="zero_byte_file", severity=ERROR, target_kind="file",
                targets=[fi.path], message="file is zero bytes",
                details={"salient": "zero_byte"})
            continue
        if fi.duration <= 0:
            add(findings, kind="zero_duration", severity=ERROR, target_kind="file",
                targets=[fi.path], message="file reports zero-length audio",
                details={"salient": "zero_duration",
                         "size": fi.size, "bitrate": fi.bitrate})

        # --- empty required tag fields ---
        for field_name in ("title", "album"):
            if not fi.tags.get(field_name):
                add(findings, kind="empty_field", severity=WARNING,
                    target_kind="file", targets=[fi.path],
                    message=f"missing {field_name} tag",
                    details={"salient": field_name})
        if not fi.tags.get("artist") and not fi.tags.get("albumartist"):
            add(findings, kind="empty_field", severity=WARNING, target_kind="file",
                targets=[fi.path], message="missing both artist and albumartist",
                details={"salient": "artist_and_albumartist"})
        if not fi.tags.get("albumartist"):
            add(findings, kind="empty_field", severity=WARNING, target_kind="file",
                targets=[fi.path], message="missing albumartist tag",
                details={"salient": "albumartist"})

        # --- stale comment / copyright (pipeline should have cleared these) ---
        for stale in ("comment", "copyright"):
            v = fi.tags.get(stale)
            if v:
                add(findings, kind="stale_tag", severity=WARNING,
                    target_kind="file", targets=[fi.path],
                    message=f"stale {stale} tag: {str(v)[:60]!r}",
                    details={"salient": stale, "value": str(v)[:200]})

        # --- disallowed genre ---
        g = fi.tags.get("genre")
        if g and g not in ALLOWED_GENRES:
            add(findings, kind="disallowed_genre", severity=WARNING,
                target_kind="file", targets=[fi.path],
                message=f"genre {g!r} not in allowed list",
                details={"salient": g, "genre": g})

        # --- missing year ---
        if not fi.tags.get("year"):
            add(findings, kind="missing_year", severity=INFO, target_kind="file",
                targets=[fi.path], message="missing year tag",
                details={"salient": "year"})
        else:
            yr = _int(fi.tags.get("year"))
            if yr and (yr < 1900 or yr > CURRENT_YEAR + 1):
                add(findings, kind="unusual_year", severity=INFO,
                    target_kind="file", targets=[fi.path],
                    message=f"year {yr} outside [1900, {CURRENT_YEAR + 1}]",
                    details={"salient": str(yr), "year": yr})

        # --- non-canonical filename ---
        fname = os.path.basename(fi.path)
        if not CANONICAL_FILENAME_RE.match(fname):
            add(findings, kind="wrong_filename", severity=WARNING,
                target_kind="file", targets=[fi.path],
                message=f"filename doesn't match [D-]NN Title.ext: {fname!r}",
                details={"salient": "wrong_filename"})

        # --- quality buckets (lossy only) ---
        if not fi.lossless:
            if fi.bitrate and fi.bitrate < QUALITY_LOW_KBPS:
                add(findings, kind="quality_low", severity=INFO, target_kind="file",
                    targets=[fi.path],
                    message=f"{fi.bitrate}kbps {fi.codec} (<{QUALITY_LOW_KBPS}kbps)",
                    details={"salient": f"{fi.bitrate}kbps_{fi.codec}",
                             "bitrate": fi.bitrate, "codec": fi.codec})
            elif fi.bitrate and fi.bitrate >= QUALITY_UPGRADE_KBPS:
                add(findings, kind="quality_upgrade_candidate", severity=INFO,
                    target_kind="file", targets=[fi.path],
                    message=f"{fi.bitrate}kbps {fi.codec} (lossless upgrade candidate)",
                    details={"salient": f"{fi.bitrate}kbps_{fi.codec}",
                             "bitrate": fi.bitrate, "codec": fi.codec})

        # --- unusual sample rate ---
        if fi.sample_rate and fi.sample_rate < 44100:
            add(findings, kind="unusual_sample_rate", severity=INFO,
                target_kind="file", targets=[fi.path],
                message=f"sample rate {fi.sample_rate}Hz (below 44.1kHz)",
                details={"salient": str(fi.sample_rate),
                         "sample_rate": fi.sample_rate})

        # --- deep full-decode check ---
        if deep:
            err = decode_errors.get(fi.path)
            if err:
                add(findings, kind="decode_error", severity=ERROR,
                    target_kind="file", targets=[fi.path],
                    message=err, details={"salient": "decode_error"})

    # --- per-album checks ---
    for alb in albums:
        infos = [f for f in by_album.get(alb.path, []) if not f.read_error]
        if not infos:
            continue

        # Path/tag mismatch: folder names should track albumartist + album per
        # music-organize.py's sanitized output. Compilations live under a
        # 'Compilations' artist folder.
        album_artist_tags = {fi.tags.get("albumartist") for fi in infos
                             if fi.tags.get("albumartist")}
        album_tag = {fi.tags.get("album") for fi in infos if fi.tags.get("album")}
        artist_tags = {fi.tags.get("artist") for fi in infos if fi.tags.get("artist")}
        cpil_set = {bool(fi.tags.get("compilation")) for fi in infos}
        is_cpil = any(cpil_set) if cpil_set else False

        expected_artist_folder = (
            "Compilations" if is_cpil
            else (sanitize_for_path(next(iter(album_artist_tags))) if len(album_artist_tags) == 1
                  else sanitize_for_path(next(iter(artist_tags))) if len(artist_tags) == 1
                  else None)
        )
        if expected_artist_folder and nfc(expected_artist_folder) != nfc(alb.artist_folder):
            add(findings, kind="path_tag_mismatch", severity=WARNING,
                target_kind="album", targets=[alb.path],
                message=f"artist folder {alb.artist_folder!r} != expected {expected_artist_folder!r}",
                details={"salient": "artist_folder",
                         "actual": alb.artist_folder,
                         "expected": expected_artist_folder})

        if len(album_tag) == 1:
            expected_album_folder = sanitize_for_path(next(iter(album_tag)))
            if expected_album_folder and nfc(expected_album_folder) != nfc(alb.album_folder):
                add(findings, kind="path_tag_mismatch", severity=WARNING,
                    target_kind="album", targets=[alb.path],
                    message=f"album folder {alb.album_folder!r} != expected {expected_album_folder!r}",
                    details={"salient": "album_folder",
                             "actual": alb.album_folder,
                             "expected": expected_album_folder})
        elif len(album_tag) > 1:
            add(findings, kind="inconsistent_album_tag", severity=WARNING,
                target_kind="album", targets=[alb.path],
                message=f"tracks claim {len(album_tag)} different albums: "
                        f"{sorted(album_tag)!r}",
                details={"salient": "multiple_albums",
                         "values": sorted(album_tag)})

        if len(album_artist_tags) > 1:
            add(findings, kind="inconsistent_albumartist", severity=WARNING,
                target_kind="album", targets=[alb.path],
                message=f"tracks claim {len(album_artist_tags)} different "
                        f"albumartists: {sorted(album_artist_tags)!r}",
                details={"salient": "multiple_albumartists",
                         "values": sorted(album_artist_tags)})

        # Compilation flag agreement: every track in an album should share cpil.
        # Tagger.py auto-flags Various Artists / multi-distinct artists.
        if len(cpil_set) > 1:
            add(findings, kind="inconsistent_compilation", severity=WARNING,
                target_kind="album", targets=[alb.path],
                message=f"compilation flag inconsistent across tracks: {cpil_set!r}",
                details={"salient": "inconsistent_cpil"})
        else:
            actual_cpil = next(iter(cpil_set)) if cpil_set else False
            aarts = [norm_artist(fi.tags.get("albumartist") or "") for fi in infos]
            tarts = [norm_artist(fi.tags.get("artist") or "") for fi in infos]
            signal = compilation_signal(aarts, tarts)
            should_be_cpil = signal is not None
            if should_be_cpil and not actual_cpil:
                add(findings, kind="compilation_mismatch", severity=WARNING,
                    target_kind="album", targets=[alb.path],
                    message=f"looks like a compilation ({signal}) but cpil=false",
                    details={"salient": "should_be_true", "signal": signal})
            elif actual_cpil and not should_be_cpil:
                # Single-artist albums marked as compilations show up under
                # Compilations/, which is almost always wrong.
                add(findings, kind="compilation_mismatch", severity=WARNING,
                    target_kind="album", targets=[alb.path],
                    message="cpil=true but artist signal says single-artist",
                    details={"salient": "should_be_false"})

        # Track-number gap detection, per disc.
        per_disc = defaultdict(list)
        for fi in infos:
            per_disc[fi.tags.get("disc") or 1].append(fi)
        for disc_num, group in per_disc.items():
            nums = sorted(fi.tags.get("track") or 0 for fi in group)
            if not nums or 0 in nums:
                continue  # missing track numbers handled by empty_field
            expected = list(range(1, max(nums) + 1))
            missing = [n for n in expected if n not in nums]
            duplicates = [n for n in nums if nums.count(n) > 1]
            if missing:
                add(findings, kind="track_gap", severity=WARNING, target_kind="album",
                    targets=[alb.path],
                    message=f"disc {disc_num}: track numbers missing {missing}",
                    details={"salient": f"disc{disc_num}_missing_{missing}"})
            if duplicates:
                # Set up: list contains a duplicated value; we want it once.
                dupe_unique = sorted(set(duplicates))
                add(findings, kind="duplicate_track_number", severity=WARNING,
                    target_kind="album", targets=[alb.path],
                    message=f"disc {disc_num}: duplicate track numbers {dupe_unique}",
                    details={"salient": f"disc{disc_num}_dup_{dupe_unique}"})

        # Duplicate track titles within the same album.
        title_to_paths = defaultdict(list)
        for fi in infos:
            t = (fi.tags.get("title") or "").strip().lower()
            if t:
                title_to_paths[t].append(fi.path)
        for title, paths in title_to_paths.items():
            if len(paths) > 1:
                add(findings, kind="duplicate_track", severity=WARNING,
                    target_kind="album", targets=sorted(paths),
                    message=f"{len(paths)} tracks share title {title!r} in this album",
                    details={"salient": title})

        # Quality-mix detection.
        loss_set = {fi.lossless for fi in infos}
        if len(loss_set) > 1:
            add(findings, kind="quality_mixed", severity=INFO, target_kind="album",
                targets=[alb.path],
                message="album mixes lossless and lossy tracks",
                details={"salient": "mixed_loss"})

        # Cover art presence. Embedded-only is downgraded to INFO since
        # Music.app's older imports embed art but never wrote a separate file.
        if not alb.covers:
            any_embedded = any(fi.has_embedded_cover for fi in infos)
            if any_embedded:
                add(findings, kind="missing_cover_file", severity=INFO,
                    target_kind="album", targets=[alb.path],
                    message="cover art is embedded but no cover.* file at album root",
                    details={"salient": "embedded_only"})
            else:
                add(findings, kind="missing_cover", severity=WARNING,
                    target_kind="album", targets=[alb.path],
                    message="no cover art (neither file nor embedded)",
                    details={"salient": "missing_cover"})
        elif len(alb.covers) > 1:
            add(findings, kind="multiple_covers", severity=INFO, target_kind="album",
                targets=[alb.path],
                message=f"album folder has {len(alb.covers)} cover candidates",
                details={"salient": f"count_{len(alb.covers)}",
                         "covers": [os.path.basename(c) for c in alb.covers]})

        # Stray non-audio files mixed into album folder.
        for extra in alb.extras:
            add(findings, kind="stray_file", severity=INFO, target_kind="file",
                targets=[extra],
                message=f"non-audio file in album folder: {os.path.basename(extra)}",
                details={"salient": "stray_file"})

        # Nested folders inside an album (canonical library is flat).
        for sub in alb.subdirs:
            add(findings, kind="nested_subdir_in_album", severity=WARNING,
                target_kind="folder", targets=[sub],
                message=f"album has nested folder {os.path.basename(sub)!r} "
                        f"(should be flattened to D-NN filenames)",
                details={"salient": os.path.basename(sub)})

        # Single-track album heuristic (often misfiled).
        if len(infos) == 1:
            add(findings, kind="single_track_album", severity=INFO,
                target_kind="album", targets=[alb.path],
                message="album has only one track",
                details={"salient": "single_track"})

    return findings


def check_library(albums: list[AlbumInfo], files: dict[str, FileInfo],
                  empty_artists: list[str], stray_files: list[str]) -> list[Finding]:
    """Cross-album / library-wide checks."""
    findings: list[Finding] = []

    for f in stray_files:
        add(findings, kind="stray_file_at_root", severity=WARNING,
            target_kind="file", targets=[f],
            message="file at library root (no artist folder)",
            details={"salient": "stray_at_root"})

    for a in empty_artists:
        add(findings, kind="empty_artist_folder", severity=WARNING,
            target_kind="folder", targets=[a],
            message="artist folder has no albums",
            details={"salient": "empty_artist"})

    for alb in albums:
        if not alb.audio:
            add(findings, kind="empty_album_folder", severity=WARNING,
                target_kind="folder", targets=[alb.path],
                message="album folder has no audio files",
                details={"salient": "empty_album"})

    # Duplicate albums (same normalized artist + album in multiple paths).
    album_key_to_paths = defaultdict(list)
    for alb in albums:
        infos = [files[p] for p in alb.audio if p in files
                 and not files[p].read_error]
        if not infos:
            continue
        aart_candidates = {fi.tags.get("albumartist") for fi in infos
                           if fi.tags.get("albumartist")}
        album_candidates = {fi.tags.get("album") for fi in infos
                            if fi.tags.get("album")}
        if len(aart_candidates) != 1 or len(album_candidates) != 1:
            continue
        key = (norm_artist(nfc(next(iter(aart_candidates)))),
               nfc(next(iter(album_candidates)) or "").strip().lower())
        if not key[0] or not key[1]:
            continue
        album_key_to_paths[key].append(alb.path)
    for key, paths in album_key_to_paths.items():
        if len(paths) > 1:
            add(findings, kind="duplicate_album", severity=WARNING,
                target_kind="pair", targets=sorted(paths),
                message=f"{len(paths)} folders for the same album "
                        f"({key[0]!r} / {key[1]!r})",
                details={"salient": f"{key[0]}/{key[1]}",
                         "albumartist": key[0], "album": key[1]})

    # Artist name variants (only differ by case / whitespace / punctuation).
    artist_norm_to_folders: dict[str, set[str]] = defaultdict(set)
    for alb in albums:
        artist_norm_to_folders[norm_artist(nfc(alb.artist_folder))].add(alb.artist_folder)
    for norm, folders in artist_norm_to_folders.items():
        if len(folders) > 1 and norm:
            add(findings, kind="artist_name_variant", severity=WARNING,
                target_kind="artist", targets=sorted(folders),
                message=f"{len(folders)} artist folders normalize to {norm!r}",
                details={"salient": norm, "folders": sorted(folders)})

    return findings


# ----------------------------------------------------------------- stats
def compute_stats(albums: list[AlbumInfo], files: dict[str, FileInfo]) -> dict[str, Any]:
    total_files = len(files)
    readable = [fi for fi in files.values() if not fi.read_error]
    total_albums = len(albums)
    total_artists = len({alb.artist_folder for alb in albums})
    total_bytes = sum(fi.size for fi in files.values())
    total_seconds = sum(fi.duration for fi in readable)

    by_ext = Counter(fi.ext for fi in files.values())

    def quality_bucket(fi: FileInfo) -> str:
        if fi.read_error:
            return "unreadable"
        if fi.lossless:
            sr = fi.sample_rate // 1000 if fi.sample_rate else 0
            if sr >= 88:
                return "lossless_hi_res"
            return "lossless_cd"
        if not fi.bitrate:
            return "lossy_unknown"
        if fi.bitrate < 192:
            return "lossy_lt_192"
        if fi.bitrate < 256:
            return "lossy_192_256"
        if fi.bitrate < 320:
            return "lossy_256_320"
        if fi.bitrate == 320:
            return "lossy_320"
        return "lossy_gt_320"

    quality = Counter(quality_bucket(fi) for fi in files.values())

    genres = Counter()
    years = Counter()
    for fi in readable:
        if fi.tags.get("genre"):
            genres[fi.tags["genre"]] += 1
        y = _int(fi.tags.get("year"))
        if y:
            decade = (y // 10) * 10
            years[decade] += 1

    compilations = sum(
        1 for alb in albums
        if any(files.get(p) and files[p].tags.get("compilation") for p in alb.audio)
    )

    return {
        "albums": total_albums,
        "artists": total_artists,
        "tracks": total_files,
        "readable_tracks": len(readable),
        "total_bytes": total_bytes,
        "total_seconds": int(total_seconds),
        "by_extension": dict(by_ext.most_common()),
        "by_quality": dict(quality.most_common()),
        "by_genre": dict(genres.most_common()),
        "by_decade": dict(sorted(years.items())),
        "compilation_albums": compilations,
    }


# ----------------------------------------------------------------- storage
SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  library_root TEXT NOT NULL,
  options TEXT,
  status TEXT DEFAULT 'running'
);
CREATE TABLE IF NOT EXISTS findings (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  finding_hash TEXT NOT NULL,
  kind TEXT NOT NULL,
  severity TEXT NOT NULL,
  target_kind TEXT NOT NULL,
  targets TEXT NOT NULL,
  message TEXT NOT NULL,
  details TEXT
);
CREATE INDEX IF NOT EXISTS idx_findings_run ON findings(run_id);
CREATE INDEX IF NOT EXISTS idx_findings_hash ON findings(finding_hash);
CREATE INDEX IF NOT EXISTS idx_findings_kind ON findings(kind);
CREATE TABLE IF NOT EXISTS dismissals (
  finding_hash TEXT PRIMARY KEY,
  reason TEXT,
  dismissed_at TEXT NOT NULL,
  kind TEXT,
  example_message TEXT
);
CREATE TABLE IF NOT EXISTS stats (
  run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  key TEXT NOT NULL,
  value TEXT NOT NULL,
  PRIMARY KEY (run_id, key)
);
"""


def open_db(db_path: str) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def start_run(conn: sqlite3.Connection, library_root: str, options: dict) -> int:
    cur = conn.execute(
        "INSERT INTO runs (started_at, library_root, options) VALUES (?, ?, ?)",
        (datetime.now(timezone.utc).isoformat(timespec="seconds"), library_root,
         json.dumps(options, sort_keys=True)),
    )
    conn.commit()
    return cur.lastrowid


def finish_run(conn: sqlite3.Connection, run_id: int) -> None:
    conn.execute(
        "UPDATE runs SET finished_at = ?, status = 'done' WHERE id = ?",
        (datetime.now(timezone.utc).isoformat(timespec="seconds"), run_id),
    )
    conn.commit()


def store_findings(conn: sqlite3.Connection, run_id: int,
                   findings: list[Finding]) -> None:
    conn.executemany(
        "INSERT INTO findings (run_id, finding_hash, kind, severity, target_kind, "
        "targets, message, details) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (run_id, f.hash(), f.kind, f.severity, f.target_kind,
             json.dumps(f.targets), f.message, json.dumps(f.details))
            for f in findings
        ],
    )
    conn.commit()


def store_stats(conn: sqlite3.Connection, run_id: int, stats: dict) -> None:
    conn.executemany(
        "INSERT INTO stats (run_id, key, value) VALUES (?, ?, ?)",
        [(run_id, k, json.dumps(v)) for k, v in stats.items()],
    )
    conn.commit()


def latest_run_id(conn: sqlite3.Connection) -> Optional[int]:
    row = conn.execute("SELECT id FROM runs ORDER BY id DESC LIMIT 1").fetchone()
    return row[0] if row else None


def load_findings(conn: sqlite3.Connection, run_id: int,
                  include_dismissed: bool = False) -> list[dict]:
    rows = conn.execute(
        "SELECT id, finding_hash, kind, severity, target_kind, targets, message, "
        "details FROM findings WHERE run_id = ? ORDER BY severity, kind, id",
        (run_id,),
    ).fetchall()
    dismissed = {r[0] for r in conn.execute("SELECT finding_hash FROM dismissals")}
    out = []
    for r in rows:
        h = r[1]
        if h in dismissed and not include_dismissed:
            continue
        out.append({
            "id": r[0],
            "hash": h,
            "kind": r[2],
            "severity": r[3],
            "target_kind": r[4],
            "targets": json.loads(r[5]),
            "message": r[6],
            "details": json.loads(r[7]) if r[7] else {},
            "dismissed": h in dismissed,
        })
    return out


# ----------------------------------------------------------------- fixers
class FixContext:
    def __init__(self, conn: sqlite3.Connection, library_root: str,
                 apply: bool):
        self.conn = conn
        self.library_root = library_root
        self.apply = apply
        self.changed: list[str] = []
        self.skipped: list[str] = []


def fix_stale_tag(finding: dict, ctx: FixContext) -> None:
    """Clear stale comment/copyright tags from a file."""
    salient = finding["details"].get("salient")
    paths = finding["targets"]
    if salient not in ("comment", "copyright"):
        ctx.skipped.append(f"{finding['hash']}: salient={salient}")
        return
    for p in paths:
        if not os.path.isfile(p):
            ctx.skipped.append(f"{finding['hash']}: missing {p}")
            continue
        ext = os.path.splitext(p)[1].lower()
        try:
            if ctx.apply:
                if ext == ".m4a":
                    a = MP4(p)
                    key = "\xa9cmt" if salient == "comment" else "cprt"
                    if key in a:
                        del a[key]
                        a.save()
                elif ext == ".flac":
                    a = FLAC(p)
                    if salient in a:
                        del a[salient]
                        a.save()
                elif ext == ".mp3":
                    m = MP3(p)
                    if m.tags is not None:
                        m.tags.delall("COMM" if salient == "comment" else "TCOP")
                        m.save()
            ctx.changed.append(f"clear-{salient}: {p}")
        except Exception as e:  # noqa: BLE001
            ctx.skipped.append(f"{finding['hash']}: {p}: {e}")


def fix_compilation_mismatch(finding: dict, ctx: FixContext) -> None:
    """Flip the compilation flag on every track in an album."""
    album_path = finding["targets"][0]
    target = finding["details"].get("salient")  # should_be_true or should_be_false
    if target not in ("should_be_true", "should_be_false"):
        ctx.skipped.append(f"{finding['hash']}: unknown salient {target}")
        return
    new_value = (target == "should_be_true")
    if not os.path.isdir(album_path):
        ctx.skipped.append(f"{finding['hash']}: album missing")
        return
    for fn in sorted(os.listdir(album_path)):
        ext = os.path.splitext(fn)[1].lower()
        if ext not in AUDIO_EXTS:
            continue
        p = os.path.join(album_path, fn)
        try:
            if ctx.apply:
                if ext == ".m4a":
                    a = MP4(p)
                    a["cpil"] = bool(new_value)  # bare bool — see CLAUDE.md
                    a.save()
                elif ext == ".flac":
                    a = FLAC(p)
                    a["compilation"] = "1" if new_value else "0"
                    a.save()
                elif ext == ".mp3":
                    m = MP3(p)
                    if m.tags is None:
                        m.add_tags()
                    from mutagen.id3 import TCMP
                    m.tags["TCMP"] = TCMP(encoding=3, text="1" if new_value else "0")
                    m.save()
            ctx.changed.append(f"cpil={new_value}: {p}")
        except Exception as e:  # noqa: BLE001
            ctx.skipped.append(f"{finding['hash']}: {p}: {e}")


def fix_empty_folder(finding: dict, ctx: FixContext) -> None:
    """Remove an artist or album folder that has no audio descendants.

    The finding `empty_album_folder` fires when the recursive walk found no
    audio anywhere under the folder, so a top-level cover.* and any leftover
    AppleDouble sidecars are also dead and go with it. Re-walk here as a
    last-mile safety check in case the filesystem changed between scan and
    fix — refuse to delete if any audio extension is now present.
    """
    for p in finding["targets"]:
        if not os.path.isdir(p):
            ctx.skipped.append(f"{finding['hash']}: {p} not a directory")
            continue
        try:
            audio_present = []
            for root, dirs, files in os.walk(p):
                dirs[:] = [d for d in dirs if not is_hidden(d)]
                for fn in files:
                    if is_hidden(fn):
                        continue
                    if os.path.splitext(fn)[1].lower() in AUDIO_EXTS:
                        audio_present.append(os.path.join(root, fn))
                if audio_present:
                    break
            if audio_present:
                ctx.skipped.append(
                    f"{finding['hash']}: {p} has audio now ({audio_present[0]}); "
                    "refusing to delete"
                )
                continue
            if ctx.apply:
                shutil.rmtree(p)
            ctx.changed.append(f"rmdir: {p}")
        except Exception as e:  # noqa: BLE001
            ctx.skipped.append(f"{finding['hash']}: {p}: {e}")


FIX_HANDLERS = {
    "stale_tag": fix_stale_tag,
    "compilation_mismatch": fix_compilation_mismatch,
    "empty_artist_folder": fix_empty_folder,
    "empty_album_folder": fix_empty_folder,
}


def fixable_kinds() -> list[str]:
    return sorted(FIX_HANDLERS.keys())


# ----------------------------------------------------------------- subcommands
def cmd_scan(args, conn: sqlite3.Connection) -> int:
    t0 = time.time()
    print(f"Scanning {args.library_root} ...", file=sys.stderr)
    if not os.path.isdir(args.library_root):
        print(f"ERROR: library root not found: {args.library_root}", file=sys.stderr)
        return 2

    options = {"deep": args.deep, "workers": args.workers,
               "kinds": args.kind or None}
    run_id = start_run(conn, args.library_root, options)

    albums, empty_artists, stray = walk_library(args.library_root)
    all_audio = [p for alb in albums for p in alb.audio]
    print(f"  found {len(albums)} album folders, {len(all_audio)} audio files "
          f"({time.time() - t0:.1f}s)", file=sys.stderr)

    files = read_files_parallel(all_audio, args.workers)
    print(f"  read tags in {time.time() - t0:.1f}s", file=sys.stderr)

    findings = check_files(albums, files, deep=args.deep, workers=args.workers)
    findings.extend(check_library(albums, files, empty_artists, stray))

    if args.kind:
        kinds = set(args.kind)
        findings = [f for f in findings if f.kind in kinds]

    stats = compute_stats(albums, files)
    store_findings(conn, run_id, findings)
    store_stats(conn, run_id, stats)
    finish_run(conn, run_id)
    print(f"  recorded {len(findings)} findings as run #{run_id} "
          f"({time.time() - t0:.1f}s)", file=sys.stderr)

    if args.json:
        # JSON output for skill consumption: dismissals applied here too.
        loaded = load_findings(conn, run_id, include_dismissed=False)
        payload = {
            "run_id": run_id,
            "library_root": args.library_root,
            "stats": stats,
            "summary": summarize(loaded),
            "findings": loaded,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print_summary(conn, run_id, stats)
    return 0


def cmd_report(args, conn: sqlite3.Connection) -> int:
    run_id = args.run if args.run else latest_run_id(conn)
    if run_id is None:
        print("No runs recorded yet.", file=sys.stderr)
        return 1
    loaded = load_findings(conn, run_id, include_dismissed=args.include_dismissed)
    if args.severity:
        loaded = [f for f in loaded if f["severity"] in set(args.severity)]
    if args.kind:
        loaded = [f for f in loaded if f["kind"] in set(args.kind)]
    if args.json:
        stats = read_stats(conn, run_id)
        print(json.dumps({"run_id": run_id, "stats": stats,
                          "summary": summarize(loaded),
                          "findings": loaded},
                         ensure_ascii=False, indent=2))
    else:
        print_findings_human(loaded)
    return 0


def cmd_stats(args, conn: sqlite3.Connection) -> int:
    if args.from_db:
        run_id = args.run if args.run else latest_run_id(conn)
        if run_id is None:
            print("No runs recorded yet.", file=sys.stderr)
            return 1
        stats = read_stats(conn, run_id)
    else:
        print(f"Scanning {args.library_root} for stats only ...", file=sys.stderr)
        albums, _, _ = walk_library(args.library_root)
        all_audio = [p for alb in albums for p in alb.audio]
        files = read_files_parallel(all_audio, args.workers)
        stats = compute_stats(albums, files)

    if args.json:
        print(json.dumps(stats, ensure_ascii=False, indent=2))
    else:
        print_stats_human(stats)
    return 0


def cmd_dismiss(args, conn: sqlite3.Connection) -> int:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if args.hash:
        # Dismiss specific hashes.
        for h in args.hash:
            row = conn.execute(
                "SELECT kind, message FROM findings WHERE finding_hash = ? "
                "ORDER BY id DESC LIMIT 1", (h,),
            ).fetchone()
            kind, msg = (row[0], row[1]) if row else (None, None)
            conn.execute(
                "INSERT INTO dismissals (finding_hash, reason, dismissed_at, kind, "
                "example_message) VALUES (?, ?, ?, ?, ?) ON CONFLICT(finding_hash) "
                "DO UPDATE SET reason = excluded.reason, "
                "dismissed_at = excluded.dismissed_at",
                (h, args.reason, now, kind, msg),
            )
            print(f"dismissed {h}" + (f" ({msg})" if msg else ""))
        conn.commit()
        return 0

    if not args.kind:
        print("ERROR: must supply --hash or --kind", file=sys.stderr)
        return 1
    # Dismiss by selector (kind + optional target substring) over latest run.
    run_id = args.run if args.run else latest_run_id(conn)
    if run_id is None:
        print("No runs to dismiss against.", file=sys.stderr)
        return 1
    rows = conn.execute(
        "SELECT finding_hash, kind, targets, message FROM findings "
        "WHERE run_id = ? AND kind = ?",
        (run_id, args.kind),
    ).fetchall()
    count = 0
    for h, k, targets_json, msg in rows:
        targets = json.loads(targets_json)
        if args.target and not any(args.target in t for t in targets):
            continue
        conn.execute(
            "INSERT INTO dismissals (finding_hash, reason, dismissed_at, kind, "
            "example_message) VALUES (?, ?, ?, ?, ?) ON CONFLICT(finding_hash) "
            "DO UPDATE SET reason = excluded.reason, "
            "dismissed_at = excluded.dismissed_at",
            (h, args.reason, now, k, msg),
        )
        count += 1
    conn.commit()
    print(f"dismissed {count} finding(s) of kind={args.kind}"
          + (f" with target~={args.target!r}" if args.target else ""))
    return 0


def cmd_undismiss(args, conn: sqlite3.Connection) -> int:
    for h in args.hash:
        cur = conn.execute("DELETE FROM dismissals WHERE finding_hash = ?", (h,))
        print(f"{'undismissed' if cur.rowcount else 'not dismissed'}: {h}")
    conn.commit()
    return 0


def cmd_fix(args, conn: sqlite3.Connection) -> int:
    run_id = args.run if args.run else latest_run_id(conn)
    if run_id is None:
        print("No runs to fix against.", file=sys.stderr)
        return 1
    findings = load_findings(conn, run_id, include_dismissed=False)

    # Filter to fixable kinds, then by user's selector.
    candidates = [f for f in findings if f["kind"] in FIX_HANDLERS]
    if args.hash:
        candidates = [f for f in candidates if f["hash"] in set(args.hash)]
    elif args.kind:
        candidates = [f for f in candidates if f["kind"] in set(args.kind)]
    else:
        print(f"ERROR: pass --hash <h>... or --kind <k>. Fixable kinds: "
              f"{', '.join(fixable_kinds())}", file=sys.stderr)
        return 1
    if not candidates:
        print("No matching fixable findings.", file=sys.stderr)
        return 0

    ctx = FixContext(conn, args.library_root, apply=args.apply)
    for f in candidates:
        handler = FIX_HANDLERS[f["kind"]]
        handler(f, ctx)

    mode = "applied" if args.apply else "would apply (dry-run)"
    print(f"{mode} {len(ctx.changed)} change(s); {len(ctx.skipped)} skipped")
    for line in ctx.changed:
        print(f"  + {line}")
    for line in ctx.skipped:
        print(f"  ~ {line}")
    if not args.apply:
        print("\nRe-run with --apply to commit these changes.")
    return 0


def cmd_history(args, conn: sqlite3.Connection) -> int:
    rows = conn.execute(
        "SELECT id, started_at, finished_at, library_root, status, "
        "(SELECT COUNT(*) FROM findings f WHERE f.run_id = r.id) "
        "FROM runs r ORDER BY id DESC LIMIT ?", (args.limit,),
    ).fetchall()
    for r in rows:
        dur = ""
        if r[1] and r[2]:
            t1 = datetime.fromisoformat(r[1])
            t2 = datetime.fromisoformat(r[2])
            dur = f" ({(t2 - t1).total_seconds():.0f}s)"
        print(f"#{r[0]}  {r[1]}{dur}  {r[3]}  status={r[4]}  findings={r[5]}")
    if not rows:
        print("No runs recorded yet.")
    return 0


# ----------------------------------------------------------------- output helpers
def summarize(findings: list[dict]) -> dict[str, Any]:
    by_severity = Counter(f["severity"] for f in findings)
    by_kind = Counter(f["kind"] for f in findings)
    return {
        "total": len(findings),
        "by_severity": {s: by_severity.get(s, 0) for s in SEVERITY_ORDER},
        "by_kind": dict(by_kind.most_common()),
    }


def read_stats(conn: sqlite3.Connection, run_id: int) -> dict[str, Any]:
    rows = conn.execute("SELECT key, value FROM stats WHERE run_id = ?",
                        (run_id,)).fetchall()
    return {k: json.loads(v) for k, v in rows}


def fmt_bytes(n: int) -> str:
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PiB"


def fmt_duration(seconds: int) -> str:
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m}m"
    return f"{m}m {s}s"


def print_stats_human(stats: dict) -> None:
    print(f"\nLibrary stats")
    print(f"  Albums:       {stats['albums']:>7}")
    print(f"  Artists:      {stats['artists']:>7}")
    print(f"  Tracks:       {stats['tracks']:>7} "
          f"({stats['readable_tracks']} readable)")
    print(f"  Compilations: {stats['compilation_albums']:>7}")
    print(f"  Size:         {fmt_bytes(stats['total_bytes']):>10}")
    print(f"  Duration:     {fmt_duration(stats['total_seconds']):>10}")
    print("\n  By extension:")
    for ext, n in stats["by_extension"].items():
        print(f"    {ext:<6} {n:>6}")
    print("\n  By quality:")
    for bucket, n in stats["by_quality"].items():
        print(f"    {bucket:<20} {n:>6}")
    if stats.get("by_genre"):
        print("\n  Top genres:")
        for g, n in list(stats["by_genre"].items())[:10]:
            print(f"    {g:<14} {n:>6}")
    if stats.get("by_decade"):
        print("\n  By decade:")
        for d, n in stats["by_decade"].items():
            print(f"    {d}s         {n:>6}")
    print()


def print_summary(conn: sqlite3.Connection, run_id: int, stats: dict) -> None:
    loaded = load_findings(conn, run_id, include_dismissed=False)
    s = summarize(loaded)
    print(f"\nRun #{run_id}: {s['total']} active findings")
    for sev in SEVERITY_ORDER:
        print(f"  {sev:<8} {s['by_severity'][sev]:>6}")
    print(f"\nBy kind:")
    for kind, n in s["by_kind"].items():
        print(f"  {kind:<28} {n:>6}")
    print_stats_human(stats)


def print_findings_human(findings: list[dict]) -> None:
    if not findings:
        print("(no findings)")
        return
    grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for f in findings:
        grouped[(f["severity"], f["kind"])].append(f)
    last_sev = None
    for (sev, kind), group in sorted(grouped.items(),
                                     key=lambda kv: (SEVERITY_ORDER.index(kv[0][0]),
                                                     kv[0][1])):
        if sev != last_sev:
            print(f"\n=== {sev.upper()} ===")
            last_sev = sev
        print(f"\n  {kind} ({len(group)})")
        for f in group[:25]:
            print(f"    [{f['hash']}] {f['message']}")
            for t in f["targets"][:3]:
                print(f"      • {t}")
            if len(f["targets"]) > 3:
                print(f"      ... and {len(f['targets']) - 3} more")
        if len(group) > 25:
            print(f"    ... and {len(group) - 25} more {kind} findings")


# ----------------------------------------------------------------- main
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="music-doctor.py",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--db", default=DEFAULT_DB_PATH,
                   help=f"SQLite store path (default: {DEFAULT_DB_PATH}).")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("scan", help="Run all checks against the library.")
    s.add_argument("--library-root", default=DEFAULT_LIBRARY_ROOT)
    s.add_argument("--workers", type=int, default=8,
                   help="Parallel tag-read workers (default: 8).")
    s.add_argument("--deep", action="store_true",
                   help="Fully decode every track with ffmpeg (slow; catches truncation).")
    s.add_argument("--kind", action="append",
                   help="Only run / record these kinds of findings. "
                        "Repeat to allow multiple kinds.")
    s.add_argument("--json", action="store_true",
                   help="Emit a structured JSON report on stdout.")

    r = sub.add_parser("report", help="Show findings from a stored run.")
    r.add_argument("--run", type=int,
                   help="Run id (default: latest).")
    r.add_argument("--severity", action="append",
                   choices=(ERROR, WARNING, INFO),
                   help="Filter by severity. Repeat to allow multiple.")
    r.add_argument("--kind", action="append",
                   help="Filter by kind. Repeat to allow multiple.")
    r.add_argument("--include-dismissed", action="store_true",
                   help="Include findings whose hash is in the dismissal table.")
    r.add_argument("--json", action="store_true")

    st = sub.add_parser("stats",
                        help="Aggregate library stats (no per-file checks).")
    st.add_argument("--library-root", default=DEFAULT_LIBRARY_ROOT)
    st.add_argument("--workers", type=int, default=8)
    st.add_argument("--from-db", action="store_true",
                    help="Read stats from a previous run instead of re-scanning.")
    st.add_argument("--run", type=int)
    st.add_argument("--json", action="store_true")

    d = sub.add_parser("dismiss",
                       help="Suppress a finding (or a class of findings) "
                            "from future reports.")
    d.add_argument("--hash", action="append", default=[],
                   help="Dismiss a specific finding hash. Repeat for multiple.")
    d.add_argument("--kind",
                   help="Dismiss every finding of this kind in the chosen run "
                        "(default: latest).")
    d.add_argument("--target",
                   help="When dismissing by --kind, only those whose target "
                        "path contains this substring.")
    d.add_argument("--reason", help="Free text note attached to the dismissal.")
    d.add_argument("--run", type=int,
                   help="Run id to dismiss against (default: latest).")

    u = sub.add_parser("undismiss", help="Re-enable a previously dismissed finding.")
    u.add_argument("hash", nargs="+", help="Finding hash(es) to undismiss.")

    f = sub.add_parser("fix", help="Apply safe corrective actions.")
    f.add_argument("--library-root", default=DEFAULT_LIBRARY_ROOT)
    f.add_argument("--hash", action="append", default=[],
                   help="Fix a specific finding hash. Repeat for multiple.")
    f.add_argument("--kind", action="append",
                   help=f"Fix every finding of this kind. "
                        f"Fixable kinds: {', '.join(fixable_kinds())}.")
    f.add_argument("--run", type=int)
    f.add_argument("--apply", action="store_true",
                   help="Actually perform changes; otherwise dry-run.")

    h = sub.add_parser("history", help="List past runs.")
    h.add_argument("--limit", type=int, default=20)

    return p


CURRENT_YEAR = date.today().year


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    conn = open_db(args.db)
    try:
        dispatch = {
            "scan": cmd_scan, "report": cmd_report, "stats": cmd_stats,
            "dismiss": cmd_dismiss, "undismiss": cmd_undismiss,
            "fix": cmd_fix, "history": cmd_history,
        }
        return dispatch[args.cmd](args, conn)
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
