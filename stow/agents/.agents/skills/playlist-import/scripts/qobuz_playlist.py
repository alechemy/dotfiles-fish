import argparse
import asyncio
import hashlib
import json
import logging
import os
import re
import tempfile
from pathlib import Path

from spotify_playlist import ImportError, save_new, text


def track_summary(track):
    album = track.get("album") or {}
    return {"id": str(track["id"]), "title": track.get("title"),
            "version": track.get("version"), "work": track.get("work"),
            "artist": (track.get("performer") or {}).get("name"),
            "album": album.get("title"), "album_id": album.get("id"),
            "duration": track.get("duration"), "isrc": track.get("isrc"),
            "streamable": track.get("streamable"),
            "explicit": track.get("parental_warning"),
            "bit_depth": track.get("maximum_bit_depth"),
            "sample_rate": track.get("maximum_sampling_rate")}


def selection(manifest, allow_missing=False):
    name = text(manifest["album"])
    rows = manifest["tracks"]
    if not isinstance(rows, list) or not rows or len(rows) > 500:
        raise ImportError("A manifest must contain 1 to 500 tracks.")
    if type(manifest["track_count"]) is not int or manifest["track_count"] != len(rows):
        raise ImportError("Manifest count does not match its tracks.")
    ids, missing = [], []
    for position, row in enumerate(rows, 1):
        if type(row["position"]) is not int or row["position"] != position:
            raise ImportError("Manifest positions must be consecutive and ordered.")
        text(row["title"])
        text(row["artist"])
        chosen = row.get("qobuz")
        if chosen is None:
            text(row["missing_reason"])
            missing.append(position)
            continue
        ident = chosen["id"]
        if not isinstance(ident, str) or not re.fullmatch(r"[0-9]+", ident):
            raise ImportError("Qobuz track IDs must be numeric strings.")
        if chosen.get("reviewed") is not True:
            raise ImportError("Every selected recording requires explicit review.")
        text(chosen["reason"])
        ids.append(ident)
    if missing and not allow_missing:
        raise ImportError("Unmatched tracks remain. Partial imports require --allow-missing.")
    if not ids:
        raise ImportError("No matched tracks.")
    return name, ids, missing


def replace_receipt(path, data):
    fd, temporary = tempfile.mkstemp(prefix=".receipt-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(data, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


async def verify(client, ident, name, ids):
    remote = await client.get_metadata(ident, "playlist")
    tracks = remote["tracks"]
    if (remote["name"] != name or tracks["total"] != len(ids)
            or [str(t["id"]) for t in tracks["items"]] != ids):
        raise ImportError("Qobuz playlist name, count, or track order differs from the manifest.")
    if remote.get("is_public") is not False or remote.get("is_collaborative") is not False:
        raise ImportError("Qobuz playlist privacy differs from the requested settings.")


async def create(client, manifest, receipt_path, allow_missing=False):
    name, ids, missing = selection(manifest, allow_missing)
    digest = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
    receipt_path = Path(receipt_path)
    if receipt_path.exists():
        receipt = json.loads(receipt_path.read_text())
        if receipt["manifest_sha256"] != digest:
            raise ImportError("Receipt belongs to a different manifest.")
        if not receipt.get("playlist_id"):
            raise ImportError("An earlier creation has an uncertain outcome. Inspect Qobuz before retrying; do not delete the receipt.")
    else:
        for ident in dict.fromkeys(ids):
            track = await client.get_metadata(ident, "track")
            if str(track["id"]) != ident or track.get("streamable") is not True:
                raise ImportError("A selected recording is no longer streamable.")
        receipt = {"manifest_sha256": digest, "status": "creating",
                   "matched_count": len(ids), "missing_positions": missing}
        save_new(receipt_path, receipt)
        description = "Manually matched playlist import."
        if missing:
            description += " Partial reconstruction; missing source positions: " + ", ".join(map(str, missing)) + "."
        async with client.session.post(
                "https://www.qobuz.com/api.json/0.2/playlist/create",
                data={"name": name, "description": description, "is_public": "0",
                      "is_collaborative": "0", "track_ids": ",".join(ids)}) as response:
            if response.status != 200:
                raise ImportError("Qobuz creation failed. The receipt records an uncertain outcome; inspect Qobuz before retrying.")
            result = await response.json()
        ident = str(result["id"])
        if not re.fullmatch(r"[0-9]+", ident):
            raise ImportError("Qobuz returned an invalid playlist ID.")
        receipt.update(playlist_id=ident, status="created")
        replace_receipt(receipt_path, receipt)
    await verify(client, receipt["playlist_id"], name, ids)
    receipt.update(status="verified", url=f"https://open.qobuz.com/playlist/{receipt['playlist_id']}")
    replace_receipt(receipt_path, receipt)
    return receipt


async def run(args):
    from streamrip.client.qobuz import QobuzClient
    from streamrip.config import Config, DEFAULT_CONFIG_PATH
    logging.disable(logging.CRITICAL)
    config = Config(args.config or DEFAULT_CONFIG_PATH)
    config.session.downloads.verify_ssl = True
    client = QobuzClient(config)
    try:
        await asyncio.wait_for(client.login(), timeout=60)
        if args.command == "search":
            pages = await client.search(args.kind, args.query, limit=args.limit)
            items = [t for p in pages for t in p.get(args.kind + "s", {}).get("items", [])]
            if args.kind == "track":
                return [track_summary(t) for t in items]
            return [{"id": str(t["id"]), "title": t.get("title"),
                     "artist": (t.get("artist") or {}).get("name"),
                     "streamable": t.get("streamable")} for t in items]
        if args.command == "inspect":
            data = await client.get_metadata(args.id, args.kind)
            if args.kind == "track":
                return track_summary(data)
            return {"id": str(data["id"]), "title": data.get("title") or data.get("name"),
                    "total": data["tracks"]["total"],
                    "tracks": [track_summary(t) for t in data["tracks"]["items"]]}
        manifest = json.loads(args.manifest.read_text())
        return await create(client, manifest, args.receipt, args.allow_missing)
    finally:
        if getattr(client, "session", None):
            await client.session.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Review Qobuz recordings and create a private, verified playlist using installed streamrip credentials.")
    parser.add_argument("--config", help="Existing streamrip config path. Never print its contents.")
    commands = parser.add_subparsers(dest="command", required=True)
    search = commands.add_parser("search")
    search.add_argument("kind", choices=["track", "album"])
    search.add_argument("query")
    search.add_argument("--limit", type=int, choices=range(1, 101), default=20, metavar="1..100")
    inspect = commands.add_parser("inspect")
    inspect.add_argument("kind", choices=["track", "album", "playlist"])
    inspect.add_argument("id")
    publish = commands.add_parser("create")
    publish.add_argument("manifest", type=Path)
    publish.add_argument("--receipt", type=Path, required=True)
    publish.add_argument("--allow-missing", action="store_true")
    publish.add_argument("--apply", action="store_true", required=True,
                         help="Authorize a remote write. Requires the user's current-turn approval.")
    args = parser.parse_args(argv)
    try:
        if args.command == "create":
            selection(json.loads(args.manifest.read_text()), args.allow_missing)
        result = asyncio.run(asyncio.wait_for(run(args), timeout=600))
    except ImportError as error:
        parser.exit(1, f"{error}\n")
    except Exception:
        parser.exit(1, "Qobuz operation failed; no response or credential details were printed. For creation, inspect the receipt before retrying.\n")
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
