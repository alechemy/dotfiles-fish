#!/usr/bin/env python3
import argparse
import json
import os
import re
import tempfile
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, build_opener

MAX_BYTES = 4 * 1024 * 1024


class ImportError(ValueError):
    pass


def playlist_id(url):
    parsed = urlsplit(url)
    match = re.fullmatch(r"/(?:embed/)?playlist/([A-Za-z0-9]{22})/?", parsed.path)
    if (parsed.scheme != "https" or parsed.netloc != "open.spotify.com"
            or not match):
        raise ImportError("Expected an HTTPS open.spotify.com playlist URL.")
    return match[1]


class Redirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if playlist_id(newurl) != playlist_id(req.full_url):
            raise ImportError("Unexpected Spotify redirect.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class StateParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.active = False
        self.parts = []
        self.count = 0

    def handle_starttag(self, tag, attrs):
        if tag == "script" and dict(attrs).get("id") == "__NEXT_DATA__":
            self.active = True
            self.count += 1

    def handle_endtag(self, tag):
        if tag == "script":
            self.active = False

    def handle_data(self, data):
        if self.active:
            self.parts.append(data)


def text(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 2000:
        raise ImportError("Missing or invalid Spotify text metadata.")
    if any(ord(c) < 32 for c in value):
        raise ImportError("Control characters in Spotify metadata.")
    return value.strip().replace("\u00a0", " ")


def extract(page, url, expected_count):
    ident = playlist_id(url)
    if type(expected_count) is not int or not 1 <= expected_count <= 10000:
        raise ImportError("Expected count must be between 1 and 10000.")
    if len(page.encode("utf-8")) > MAX_BYTES:
        raise ImportError("Spotify response is too large.")
    parser = StateParser()
    parser.feed(page)
    if parser.count != 1:
        raise ImportError("Spotify embed state is missing or ambiguous.")
    try:
        entity = json.loads("".join(parser.parts))["props"]["pageProps"]["state"]["data"]["entity"]
        if entity["uri"] != f"spotify:playlist:{ident}":
            raise ImportError("Spotify returned a different playlist.")
        rows = entity["trackList"]
        if not isinstance(rows, list) or len(rows) != expected_count:
            raise ImportError("Track count differs from the independently confirmed total. The embed may be truncated.")
        tracks = []
        for position, row in enumerate(rows, 1):
            uri = row["uri"]
            if not isinstance(uri, str) or not re.fullmatch(r"spotify:track:[A-Za-z0-9]{22}", uri):
                raise ImportError("Playlist contains a missing, local, or non-track item.")
            duration = row["duration"]
            explicit = row["isExplicit"]
            if type(duration) is not int or duration <= 0 or type(explicit) is not bool:
                raise ImportError("Invalid Spotify duration or explicit flag.")
            tracks.append({"position": position, "spotify_uri": uri,
                           "title": text(row["title"]), "artist": text(row["subtitle"]),
                           "duration_ms": duration, "explicit": explicit})
        return {"source_url": f"https://open.spotify.com/playlist/{ident}",
                "title": text(entity["title"]), "track_count": len(tracks), "tracks": tracks}
    except (KeyError, TypeError, json.JSONDecodeError):
        raise ImportError("Unsupported Spotify embed metadata shape.") from None


def save_new(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".playlist-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(data, stream, indent=2, ensure_ascii=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)
    finally:
        os.unlink(temporary)


def fetch(url):
    ident = playlist_id(url)
    with build_opener(Redirects()).open(
            f"https://open.spotify.com/embed/playlist/{ident}", timeout=30) as response:
        if playlist_id(response.url) != ident:
            raise ImportError("Unexpected Spotify response URL.")
        raw = response.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ImportError("Spotify response is too large.")
    return raw.decode("utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Extract public Spotify playlist metadata without browser authentication.")
    parser.add_argument("url")
    parser.add_argument("--expected-count", type=int, required=True,
                        help="Total independently confirmed from Spotify, not the embed row count.")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.output.exists() or args.output.is_symlink():
            raise ImportError("Output already exists; choose a new path.")
        result = extract(fetch(args.url), args.url, args.expected_count)
        save_new(args.output, result)
    except ImportError as error:
        parser.exit(1, f"{error}\n")
    except Exception:
        parser.exit(1, "Spotify extraction failed; no response or credential details were printed.\n")
    print(f"Saved {result['track_count']} ordered tracks to {args.output}.")


if __name__ == "__main__":
    main()
