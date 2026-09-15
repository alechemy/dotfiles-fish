import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

SCRIPTS = Path(__file__).resolve().parents[2] / "stow/agents/.agents/skills/playlist-import/scripts"


def load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


spotify = load("spotify_playlist")
qobuz = load("qobuz_playlist")
album = load("verify_album")
URL = "https://open.spotify.com/playlist/" + "A" * 22
URI = "spotify:playlist:" + "A" * 22


def row(title="Song"):
    return {"uri": "spotify:track:" + "B" * 22, "title": title,
            "subtitle": "Artist", "duration": 123000, "isExplicit": False,
            "audioPreview": {"private": "DO_NOT_PROJECT"}}


def page(rows=None, uri=URI):
    entity = {"title": "Synthetic playlist", "uri": uri,
              "trackList": rows if rows is not None else [row()]}
    state = {"props": {"pageProps": {"state": {"data": {"entity": entity},
             "settings": {"session": {"accessToken": "DO_NOT_PROJECT"}}}}}}
    return '<script id="__NEXT_DATA__" type="application/json">' + json.dumps(state) + '</script>'


def manifest():
    return {"album": "Synthetic album", "track_count": 2, "tracks": [
        {"position": i, "title": "Song", "artist": "Artist",
         "qobuz": {"id": str(i), "reviewed": True, "reason": "Exact recording."}}
        for i in (1, 2)]}


class SpotifyTests(unittest.TestCase):
    def test_url_validation(self):
        self.assertEqual(spotify.playlist_id(URL + "?si=ignored"), "A" * 22)
        for url in [URL.replace("https", "http"), URL.replace("open.spotify.com", "open.spotify.com.evil.test"),
                    URL.replace("open.spotify.com", "user@open.spotify.com"), URL + "/extra", URL[:-1]]:
            with self.subTest(url=url), self.assertRaises(spotify.ImportError):
                spotify.playlist_id(url)

    def test_projection_order_duplicates(self):
        result = spotify.extract(page([row("One"), row("Two"), row("One")]), URL, 3)
        self.assertEqual([t["title"] for t in result["tracks"]], ["One", "Two", "One"])
        self.assertEqual([t["position"] for t in result["tracks"]], [1, 2, 3])
        self.assertNotIn("DO_NOT_PROJECT", json.dumps(result))
        self.assertEqual(set(result["tracks"][0]), {"title", "artist", "position", "spotify_uri", "duration_ms", "explicit"})

    def test_completeness_and_identity(self):
        for html, count in [(page(), 2), (page([]), 1), (page(uri="wrong"), 1), (page(), True), ("", 1), (page() + page(), 1)]:
            with self.subTest(count=count), self.assertRaises(spotify.ImportError):
                spotify.extract(html, URL, count)

    def test_invalid_rows(self):
        for field, value in [("uri", "spotify:episode:" + "B" * 22), ("title", ""), ("subtitle", "bad\ntext"),
                             ("duration", True), ("duration", 0), ("isExplicit", 1)]:
            item = row()
            item[field] = value
            with self.subTest(field=field), self.assertRaises(spotify.ImportError):
                spotify.extract(page([item]), URL, 1)

    def test_fetch_bounds_and_identity(self):
        response = Mock()
        response.url = URL
        response.read.return_value = b"x" * (spotify.MAX_BYTES + 1)
        opener = Mock()
        opener.open.return_value.__enter__ = Mock(return_value=response)
        opener.open.return_value.__exit__ = Mock(return_value=False)
        with patch.object(spotify, "build_opener", return_value=opener), self.assertRaises(spotify.ImportError):
            spotify.fetch(URL)
        response.read.assert_called_once_with(spotify.MAX_BYTES + 1)
        response.url = URL.replace("open.spotify.com", "evil.test")
        response.read.reset_mock()
        with patch.object(spotify, "build_opener", return_value=opener), self.assertRaises(spotify.ImportError):
            spotify.fetch(URL)
        response.read.assert_not_called()

    def test_save_exclusive_and_private(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "data.json"
            spotify.save_new(path, {"ok": True})
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(FileExistsError):
                spotify.save_new(path, {"overwrite": True})
            self.assertEqual(json.loads(path.read_text()), {"ok": True})
            self.assertEqual(list(Path(directory).iterdir()), [path])

    def test_failed_write_cleans_temporary(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "data.json"
            with self.assertRaises(TypeError):
                spotify.save_new(path, object())
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_cli_does_not_fetch_when_output_exists(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "data.json"
            path.write_text("unchanged")
            with patch.object(spotify, "fetch") as fetch, self.assertRaises(SystemExit):
                spotify.main([URL, "--expected-count", "1", "--output", str(path)])
            fetch.assert_not_called()


class SelectionTests(unittest.TestCase):
    def test_review_and_missing_gates(self):
        data = manifest()
        self.assertEqual(qobuz.selection(data)[1], ["1", "2"])
        data["tracks"][1]["qobuz"] = None
        data["tracks"][1]["missing_reason"] = "Not found."
        with self.assertRaises(spotify.ImportError):
            qobuz.selection(data)
        self.assertEqual(qobuz.selection(data, True)[2], [2])
        data["tracks"][0]["qobuz"]["reviewed"] = False
        with self.assertRaises(spotify.ImportError):
            qobuz.selection(data, True)

    def test_ids_positions_counts(self):
        for mutate in [lambda d: d.update(track_count=3),
                       lambda d: d["tracks"][0].update(position=2),
                       lambda d: d["tracks"][0]["qobuz"].update(id="1,2")]:
            data = manifest()
            mutate(data)
            with self.assertRaises(spotify.ImportError):
                qobuz.selection(data)

    def test_duplicate_ids_preserved(self):
        data = manifest()
        data["tracks"][1]["qobuz"]["id"] = "1"
        self.assertEqual(qobuz.selection(data)[1], ["1", "1"])

    def test_summary_projects_only_catalog_fields(self):
        result = qobuz.track_summary({"id": 1, "user": {"secret": "DO_NOT_PROJECT"}})
        self.assertNotIn("DO_NOT_PROJECT", json.dumps(result))


class CreateTests(unittest.IsolatedAsyncioTestCase):
    def client(self):
        client = AsyncMock()
        async def metadata(ident, kind):
            if kind == "track":
                return {"id": ident, "streamable": True}
            return {"id": "42", "name": "Synthetic album", "is_public": False,
                    "is_collaborative": False, "tracks": {"total": 2, "items": [{"id": 1}, {"id": 2}]}}
        client.get_metadata.side_effect = metadata
        response = AsyncMock()
        response.status = 200
        response.json.return_value = {"id": 42}
        context = AsyncMock()
        context.__aenter__.return_value = response
        client.session.post = Mock(return_value=context)
        return client

    async def test_create_verify_and_idempotent_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "receipt.json"
            client = self.client()
            first = await qobuz.create(client, manifest(), path)
            second = await qobuz.create(client, manifest(), path)
            self.assertEqual(first, second)
            self.assertEqual(first["status"], "verified")
            client.session.post.assert_called_once()
            self.assertEqual(client.session.post.call_args.kwargs["data"]["track_ids"], "1,2")
            self.assertEqual(client.session.post.call_args.kwargs["data"]["is_public"], "0")

    async def test_unknown_outcome_never_retries_write(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "receipt.json"
            client = self.client()
            client.session.post.side_effect = TimeoutError()
            with self.assertRaises(TimeoutError):
                await qobuz.create(client, manifest(), path)
            self.assertEqual(json.loads(path.read_text())["status"], "creating")
            with self.assertRaises(spotify.ImportError):
                await qobuz.create(client, manifest(), path)
            client.session.post.assert_called_once()

    async def test_verification_rejects_wrong_order(self):
        client = AsyncMock()
        client.get_metadata.return_value = {"name": "Synthetic album", "tracks": {
            "total": 2, "items": [{"id": 2}, {"id": 1}]}}
        with self.assertRaises(spotify.ImportError):
            await qobuz.verify(client, "42", "Synthetic album", ["1", "2"])

    async def test_verification_requires_explicit_privacy(self):
        client = AsyncMock()
        client.get_metadata.return_value = {"name": "Synthetic album", "tracks": {
            "total": 2, "items": [{"id": 1}, {"id": 2}]}}
        with self.assertRaises(spotify.ImportError):
            await qobuz.verify(client, "42", "Synthetic album", ["1", "2"])

    async def test_no_receipt_or_remote_write_when_unavailable(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "receipt.json"
            client = self.client()
            client.get_metadata.side_effect = None
            client.get_metadata.return_value = {"id": "1", "streamable": False}
            with self.assertRaises(spotify.ImportError):
                await qobuz.create(client, manifest(), path)
            self.assertFalse(path.exists())
            client.session.post.assert_not_called()


class AlbumTests(unittest.TestCase):
    def data(self):
        data = manifest()
        data.update(genre="Soundtrack", year="2018")
        for row in data["tracks"]:
            row["duration_ms"] = 123000
            row["qobuz"].update(title="Song", artist="Artist")
        return data

    def audio(self, number):
        class Audio(dict):
            pass
        audio = Audio({"©nam": ["Song"], "©ART": ["Artist"], "©alb": ["Synthetic album"],
                       "aART": ["Various Artists"], "©gen": ["Soundtrack"], "©day": ["2018"],
                       "cpil": True, "trkn": [(number, 0)], "disk": [(1, 0)], "covr": [b"image"]})
        audio.info = SimpleNamespace(codec="alac", length=123.0)
        audio.save = Mock()
        return audio

    def test_audit_rejects_wrong_recording_and_accepts_artist_recase(self):
        data = self.data()
        audio = self.audio(1)
        audio["©ART"] = ["ARTIST"]
        album.audit(audio, data["tracks"][0], data, 1)
        audio["©nam"] = ["Wrong recording"]
        with self.assertRaises(spotify.ImportError):
            album.audit(audio, data["tracks"][0], data, 1)
        audio["©nam"] = ["Song"]
        audio.info.length = 150
        with self.assertRaises(spotify.ImportError):
            album.audit(audio, data["tracks"][0], data, 1)

    def test_version_title_matches_streamrip_rendering(self):
        data = self.data()
        data["tracks"][0]["qobuz"].update(version="Live", work="Suite")
        audio = self.audio(1)
        audio["©nam"] = ["Suite: Song (Live)"]
        album.audit(audio, data["tracks"][0], data, 1)
        data["tracks"][0]["qobuz"]["title"] = "Suite: Song (Live)"
        album.audit(audio, data["tracks"][0], data, 1)

    def test_preflight_before_any_save_and_totals(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            audios = {}
            for number in (1, 2):
                path = folder / f"{number}.m4a"
                path.write_bytes(b"synthetic")
                audios[path] = self.audio(number)
            module = SimpleNamespace(MP4=lambda path: audios[path], MP4Cover=Mock())
            with patch.dict(sys.modules, {"mutagen": SimpleNamespace(), "mutagen.mp4": module}):
                audios[folder / "2.m4a"]["©nam"] = ["Wrong"]
                with self.assertRaises(spotify.ImportError):
                    album.check(self.data(), folder, apply=True)
                for audio in audios.values():
                    audio.save.assert_not_called()
                audios[folder / "2.m4a"]["©nam"] = ["Song"]
                result = album.check(self.data(), folder, apply=True)
                self.assertEqual(result["verified_tracks"], 2)
                for number in (1, 2):
                    audio = audios[folder / f"{number}.m4a"]
                    self.assertEqual(audio["trkn"], [(number, 2)])
                    self.assertEqual(audio["disk"], [(1, 1)])
                    audio.save.assert_called_once()


if __name__ == "__main__":
    unittest.main()
