import argparse
import hashlib
import json
from pathlib import Path

from qobuz_playlist import selection
from spotify_playlist import ImportError


def audit(audio, row, manifest, number):
    chosen = row["qobuz"]
    title = chosen["title"].strip()
    version, work = chosen.get("version"), chosen.get("work")
    if version is not None and version not in title:
        title = f"{title} ({version})"
    if work is not None and work not in title:
        title = f"{work}: {title}"
    expected = {"\xa9nam": title, "\xa9ART": chosen["artist"],
                "\xa9alb": manifest["album"], "aART": "Various Artists",
                "\xa9gen": manifest["genre"], "\xa9day": str(manifest["year"])}
    for key, value in expected.items():
        actual = audio.get(key, [None])[0]
        if key == "\xa9ART" and isinstance(actual, str) and isinstance(value, str):
            actual, value = actual.casefold(), value.casefold()
        if actual != value:
            raise ImportError(f"Track {number} has unexpected {key} metadata.")
    if audio.get("cpil") is not True or audio.get("trkn", [(0, 0)])[0][0] != number:
        raise ImportError(f"Track {number} has incorrect compilation or track numbering.")
    if abs(audio.info.length * 1000 - row["duration_ms"]) > 2000:
        raise ImportError(f"Track {number} differs from the source duration by more than two seconds.")
    if audio.info.codec != "alac" or not audio.get("covr"):
        raise ImportError(f"Track {number} is not ALAC or lacks cover art.")


def check(manifest, folder, allow_missing=False, cover=None, apply=False):
    from mutagen.mp4 import MP4, MP4Cover
    _, ids, _ = selection(manifest, allow_missing)
    folder = Path(folder)
    if folder.is_symlink() or not folder.is_dir():
        raise ImportError("Expected a real album directory.")
    audio_suffixes = {".m4a", ".mp3", ".flac", ".ogg", ".opus", ".wav", ".aiff", ".aif", ".ape", ".wv"}
    paths = [p for p in folder.rglob("*") if p.suffix.lower() in audio_suffixes]
    if len(paths) != len(ids) or any(p.is_symlink() or p.parent != folder or p.suffix.lower() != ".m4a" for p in paths):
        raise ImportError("Album file count or directory layout differs from the manifest.")
    tracks = [(path, MP4(path)) for path in paths]
    tracks.sort(key=lambda item: item[1].get("trkn", [(0, 0)])[0][0])
    rows = [row for row in manifest["tracks"] if row.get("qobuz")]
    for number, ((_, audio), row) in enumerate(zip(tracks, rows), 1):
        audit(audio, row, manifest, number)
    image = None
    if cover:
        data = Path(cover).read_bytes()
        if not 0 < len(data) <= 10 * 1024 * 1024:
            raise ImportError("Cover must be at most 10 MiB.")
        if data.startswith(b"\xff\xd8\xff"):
            image = MP4Cover(data, imageformat=MP4Cover.FORMAT_JPEG)
        elif data.startswith(b"\x89PNG\r\n\x1a\n"):
            image = MP4Cover(data, imageformat=MP4Cover.FORMAT_PNG)
        else:
            raise ImportError("Cover must be a JPEG or PNG, not renamed WebP.")
        if not apply:
            raise ImportError("Embedding cover art requires --apply.")
    if apply:
        for number, (path, audio) in enumerate(tracks, 1):
            audio["trkn"] = [(number, len(tracks))]
            audio["disk"] = [(1, 1)]
            if image is not None:
                audio["covr"] = [image]
            audio.save()
        tracks = [(path, MP4(path)) for path, _ in tracks]
    covers = set()
    for number, ((_, audio), row) in enumerate(zip(tracks, rows), 1):
        audit(audio, row, manifest, number)
        if apply and (audio["trkn"] != [(number, len(tracks))] or audio["disk"] != [(1, 1)]):
            raise ImportError("Track or disc totals did not persist.")
        digest = hashlib.sha256(bytes(audio["covr"][0])).hexdigest()
        covers.add(digest)
        if image is not None and bytes(audio["covr"][0]) != bytes(image):
            raise ImportError("Cover did not persist.")
    if len(covers) != 1:
        raise ImportError("Album tracks have different cover art.")
    return {"verified_tracks": len(tracks), "source_tracks": manifest["track_count"],
            "unified_cover_sha256": covers.pop(), "album": manifest["album"]}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Verify a riptag compilation against its reviewed manifest. Optionally set track/disc totals and cover art after preflight.")
    parser.add_argument("manifest", type=Path)
    parser.add_argument("folder", type=Path)
    parser.add_argument("--allow-missing", action="store_true")
    parser.add_argument("--cover", type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = check(json.loads(args.manifest.read_text()), args.folder,
                       args.allow_missing, args.cover, args.apply)
    except ImportError as error:
        parser.exit(1, f"{error}\n")
    except Exception:
        parser.exit(1, "Album verification failed. Files may be partially retagged if --apply was used; rerun verification before removing an existing album.\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
