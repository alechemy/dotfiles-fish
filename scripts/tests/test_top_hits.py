#!/usr/bin/env python3
"""Unit tests for the top-hits chart parser and Qobuz candidate scorer."""

from __future__ import annotations

import importlib.util
import os
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).parents[2] / "stow" / "bin" / ".local" / "bin" / "top-hits.py"

_state = tempfile.TemporaryDirectory()
os.environ["TOP_HITS_STATE"] = _state.name
_spec = importlib.util.spec_from_file_location("top_hits", SCRIPT)
th = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(th)


WIKITEXT = """
Intro text.
{| class="wikitable sortable" style="text-align: center"
|-
! scope="col" | No.
! scope="col" | Title
! scope="col" | Artist(s)
|-
|1 || "[[Glass Harbor (song)|Glass Harbor]]" || [[Marlow Vane]] featuring [[Tessa Quill]]<ref>cite</ref>
|-
|2 || "[[Neon Orchard]]" || rowspan="2"| [[The Lantern Club]]
|-
|3 || "[[Paper Comet]]"
|-
|4 || "{{nowrap|Salt & Static}}" || {{sortname|Iris|Halloway}} and [[Dorian Feld]]
|-
|5
| "[[Wire Garden (Pt. 2)|Wire Garden (Part 2)]]"
| [[Otto Brann]]
|-
|6 || "[[Beat Drop 2]]" / "[[Beat Drop 3]]" || [[Otto Brann]] featuring [[Ivo Rask]] / [[Otto Brann]] featuring [[Tessa Quill]]
|}
Outro text.
"""


class ChartParserTests(unittest.TestCase):
    def test_parses_links_rowspan_templates_and_refs(self):
        entries = th.parse_chart(WIKITEXT, top_n=50)
        self.assertEqual([e["rank"] for e in entries], [1, 2, 3, 4, 5, 6])
        self.assertEqual(entries[0], {"rank": 1, "title": "Glass Harbor", "artist": "Marlow Vane featuring Tessa Quill"})
        self.assertEqual(entries[1]["artist"], "The Lantern Club")
        self.assertEqual(entries[2], {"rank": 3, "title": "Paper Comet", "artist": "The Lantern Club"})
        self.assertEqual(entries[3], {"rank": 4, "title": "Salt & Static", "artist": "Iris Halloway and Dorian Feld"})
        self.assertEqual(entries[4], {"rank": 5, "title": "Wire Garden (Part 2)", "artist": "Otto Brann"})
        self.assertEqual(entries[5], {"rank": 6, "title": "Beat Drop 2", "artist": "Otto Brann featuring Ivo Rask"})

    def test_top_n_truncates(self):
        self.assertEqual(len(th.parse_chart(WIKITEXT, top_n=2)), 2)

    def test_missing_table_raises(self):
        with self.assertRaises(ValueError):
            th.parse_chart("no table here")


class CreditTests(unittest.TestCase):
    def test_split_credit(self):
        self.assertEqual(th.split_credit("Marlow Vane featuring Tessa Quill"), (["marlow vane"], ["tessa quill"]))
        self.assertEqual(th.split_credit("Iris Halloway and Dorian Feld featuring A & B"),
                         (["iris halloway", "dorian feld"], ["a", "b"]))
        self.assertEqual(th.split_credit("Solo Act"), (["solo act"], []))
        self.assertEqual(th.split_credit("Lil Nas X featuring Billy Ray Cyrus"), (["lil nas x"], ["billy ray cyrus"]))

    def test_name_in_aliases_and_word_prefixes(self):
        self.assertTrue(th.name_in("machine gun kelly", "mgk , blackbear"))
        self.assertTrue(th.name_in("soulja boy tell em", "soulja boy"))
        self.assertFalse(th.name_in("soulja boy tell em", "soulja"))

    def test_norm_handles_stylized_spellings(self):
        self.assertEqual(th.norm("P!nk"), "pink")
        self.assertEqual(th.norm("Ke$ha"), "kesha")
        self.assertEqual(th.norm("Wire Garden Pt. 2"), "wire garden part 2")
        self.assertEqual(th.norm("Beyoncé"), "beyonce")
        self.assertEqual(th.norm("G.D.F.R."), "gdfr")
        self.assertEqual(th.norm("T.I."), "ti")

    def test_base_title_strips_nested_parentheticals(self):
        self.assertEqual(th.base_title("Glass Harbor (Album Version (Explicit) FINAL)"), "glass harbor")
        self.assertEqual(th.base_title("Glass Harbor (feat. Tessa Quill) [Remastered]"), "glass harbor")
        self.assertEqual(th.base_title("Glass Harbor - Radio Edit"), "glass harbor")
        self.assertEqual(th.base_title("Glass Harbor (unclosed"), "glass harbor")

    def test_credited_artists_ignores_writers(self):
        performers = "Cover Kid, MainArtist - Marlow Vane, Composer, Lyricist - Guest Star, FeaturedArtist"
        self.assertEqual(th.credited_artists(performers), ["Cover Kid", "Guest Star"])

    def test_build_queries_prefers_first_main_artist(self):
        queries = th.build_queries({"title": "Glass Harbor (Radio Edit)", "artist": "Marlow Vane and Ivo Rask featuring Tessa Quill"})
        self.assertEqual(queries, ["Marlow Vane Glass Harbor", "Marlow Vane and Ivo Rask Glass Harbor", "Glass Harbor"])


def item(id, title, performer, album, album_artist, *, version=None, tracks_count=12, duration=210,
         explicit=False, released="2008-03-01", isrc=None, performers="", streamable=True, genre="Pop"):
    return {
        "id": id, "title": title, "version": version, "duration": duration,
        "parental_warning": explicit, "isrc": isrc or f"ISRC{id}", "hires": False,
        "maximum_bit_depth": 16, "maximum_sampling_rate": 44.1, "streamable": streamable,
        "performer": {"name": performer}, "performers": performers,
        "album": {"id": f"A{id}", "title": album, "version": None, "tracks_count": tracks_count,
                  "artist": {"name": album_artist}, "release_date_original": released,
                  "genre": {"name": genre}},
    }


def resolve(title, artist, items, year=2008):
    return th.resolve_entry({"rank": 1, "title": title, "artist": artist}, year, items)


class ScoringTests(unittest.TestCase):
    def test_album_cut_beats_radio_edit_single_and_compilation(self):
        items = [
            item(1, "Glass Harbor", "Marlow Vane", "Glass Harbor", "Marlow Vane", version="Radio Edit", tracks_count=1, duration=190),
            item(2, "Glass Harbor", "Marlow Vane", "Big Chart Hits 2008", "Various Artists", tracks_count=40),
            item(3, "Glass Harbor", "Marlow Vane", "Harbor Lights", "Marlow Vane"),
        ]
        row = resolve("Glass Harbor", "Marlow Vane", items)
        self.assertEqual(row["qobuz"]["id"], "3")
        self.assertEqual(row["status"], "auto")

    def test_album_version_label_is_not_a_remix(self):
        items = [
            item(1, "Glass Harbor", "Marlow Vane", "Harbor Lights", "Marlow Vane", version="Album Version (Explicit)", explicit=True),
            item(2, "Glass Harbor", "Marlow Vane", "Street Tape", "DJ Somebody", explicit=True,
                 performers="DJ Somebody, MainArtist - Marlow Vane, MainArtist"),
        ]
        row = resolve("Glass Harbor", "Marlow Vane", items)
        self.assertEqual(row["qobuz"]["id"], "1")
        self.assertNotIn("remix", [n for n, _ in row["penalties"]])

    def test_explicit_preferred_over_clean_sibling(self):
        items = [
            item(1, "Glass Harbor", "Marlow Vane", "Harbor Lights", "Marlow Vane", explicit=False),
            item(2, "Glass Harbor", "Marlow Vane", "Harbor Lights (Explicit)", "Marlow Vane", explicit=True),
        ]
        self.assertEqual(resolve("Glass Harbor", "Marlow Vane", items)["qobuz"]["id"], "2")

    def test_live_album_explicit_does_not_force_clean_penalty(self):
        items = [
            item(1, "Glass Harbor", "Marlow Vane", "Harbor Lights", "Marlow Vane", explicit=False),
            item(2, "Glass Harbor", "Marlow Vane", "Live and Loud 2009", "Marlow Vane", explicit=True, released="2009-09-01"),
        ]
        row = resolve("Glass Harbor", "Marlow Vane", items)
        self.assertEqual(row["qobuz"]["id"], "1")
        self.assertEqual(row["status"], "auto")

    def test_cover_by_writer_credit_is_rejected(self):
        items = [
            item(1, "Glass Harbor", "Cover Kid", "Cover Kid", "Cover Kid", tracks_count=1, released="2025-01-01",
                 performers="Cover Kid, MainArtist - Marlow Vane, Composer, Lyricist"),
            item(2, "Glass Harbor", "Kidz Choir Kids", "Kidz Bop 14", "Kidz Choir Kids",
                 performers="Kidz Choir Kids, MainArtist - Marlow Vane, FeaturedArtist"),
        ]
        row = resolve("Glass Harbor", "Marlow Vane", items)
        self.assertIsNone(row["qobuz"])
        self.assertEqual(row["status"], "unresolved")
        self.assertEqual(row["rejected"], {"artist_mismatch": 1, "junk": 1})

    def test_charted_feature_selects_remix(self):
        items = [
            item(1, "Salt & Static", "Iris Halloway", "Tidelines", "Iris Halloway"),
            item(2, "Salt & Static (feat. Dorian Feld)", "Iris Halloway", "Salt & Static (Remix)", "Iris Halloway",
                 version="Remix", tracks_count=1),
        ]
        row = resolve("Salt & Static", "Iris Halloway featuring Dorian Feld", items)
        self.assertEqual(row["qobuz"]["id"], "2")

    def test_remix_word_in_title_with_charted_feature(self):
        items = [
            item(1, "Glass Harbor", "Marlow Vane", "Harbor Lights", "Marlow Vane", explicit=True),
            item(2, "Glass Harbor Remix (feat. Tessa Quill)", "Marlow Vane", "Harbor Lights (Deluxe)", "Marlow Vane", explicit=True),
        ]
        row = resolve("Glass Harbor", "Marlow Vane featuring Tessa Quill", items)
        self.assertEqual(row["qobuz"]["id"], "2")
        self.assertEqual(row["status"], "auto")

    def test_longer_title_sharing_a_prefix_is_not_a_match(self):
        items = [item(1, "Glass Harbor To The Edge", "Marlow Vane", "Harbor Lights", "Marlow Vane")]
        self.assertEqual(resolve("Glass Harbor", "Marlow Vane", items)["status"], "unresolved")

    def test_censored_title_matches(self):
        items = [item(1, "Gl**s Harbor", "Marlow Vane", "Harbor Lights", "Marlow Vane")]
        self.assertEqual(resolve("Glass Harbor", "Marlow Vane", items)["status"], "auto")
        items = [item(1, "Gl**s Harbor Nights", "Marlow Vane", "Harbor Lights", "Marlow Vane")]
        self.assertEqual(resolve("Glass Harbor", "Marlow Vane", items)["status"], "unresolved")

    def test_stylized_artist_and_part_title_match(self):
        items = [item(1, "Wire Garden Pt. 2 (feat. Otto Brann)", "P!nk", "Funhouse", "P!nk")]
        row = resolve("Wire Garden (Part 2)", "Pink featuring Otto Brann", items)
        self.assertEqual(row["qobuz"]["id"], "1")
        self.assertEqual(row["status"], "auto")

    def test_title_in_song_name_is_not_a_live_marker(self):
        items = [item(1, "Live Your Dream", "Marlow Vane", "Harbor Lights", "Marlow Vane")]
        row = resolve("Live Your Dream", "Marlow Vane", items)
        self.assertEqual(row["status"], "auto")

    def test_close_call_ignores_same_recording(self):
        items = [
            item(1, "Glass Harbor", "Marlow Vane", "Harbor Lights", "Marlow Vane", isrc="X1"),
            item(2, "Glass Harbor", "Marlow Vane", "Harbor Lights", "Marlow Vane", isrc="X1"),
            item(3, "Glass Harbor", "Marlow Vane", "Harbor Lights (Deluxe)", "Marlow Vane", isrc="X2", duration=211),
        ]
        self.assertNotIn("close_call", resolve("Glass Harbor", "Marlow Vane", items)["flags"])

    def test_named_alternate_version_loses_to_plain_cut(self):
        items = [
            item(1, "Glass Harbor", "Marlow Vane", "Harbor Lights (Deluxe)", "Marlow Vane", version="Pop Version", tracks_count=30, duration=178),
            item(2, "Glass Harbor", "Marlow Vane", "Harbor Lights", "Marlow Vane", duration=215),
        ]
        row = resolve("Glass Harbor", "Marlow Vane", items)
        self.assertEqual(row["qobuz"]["id"], "2")
        self.assertEqual(row["status"], "auto")
        self.assertIn("alt_version", resolve("Glass Harbor", "Marlow Vane", items[:1])["flags"])

    def test_unknown_version_string_is_flagged(self):
        items = [item(1, "Glass Harbor", "Marlow Vane", "Harbor Lights: The Remixes", "Marlow Vane", version="Somebody & Someone")]
        row = resolve("Glass Harbor", "Marlow Vane", items)
        self.assertEqual(row["status"], "review")
        self.assertIn("unknown_version", row["flags"])
        self.assertIn("remix_album", row["flags"])


class AssembleTests(unittest.TestCase):
    def test_artist_override_only_when_main_artist_missing(self):
        self.assertIsNone(th.artist_override("Marlow Vane", "Marlow Vane featuring Tessa Quill"))
        self.assertIsNone(th.artist_override("Marlow Vane feat. Tessa Quill", "Marlow Vane featuring Tessa Quill"))
        self.assertEqual(th.artist_override("Tessa Quill", "Marlow Vane featuring Tessa Quill"), "Marlow Vane featuring Tessa Quill")
        self.assertIsNone(th.artist_override("P!nk", "Pink"))


class TitleCleanupTests(unittest.TestCase):
    def test_noise_versions_are_dropped(self):
        self.assertEqual(th.clean_title("Bleeding Love", "Album Version"), "Bleeding Love")
        self.assertEqual(th.clean_title("No Air", "Main Version"), "No Air")
        self.assertEqual(th.clean_title("Lollipop", "Album Version (Explicit)"), "Lollipop")
        self.assertEqual(th.clean_title("Sexual Eruption", "Album Version (Explicit) FINAL"), "Sexual Eruption")
        self.assertEqual(th.clean_title("Like You'll Never See Me Again", "Main"), "Like You'll Never See Me Again")
        self.assertEqual(th.clean_title("Right Round", "US Album Version"), "Right Round")
        self.assertEqual(th.clean_title("Bust It Baby, Pt. 2 (feat. Ne-Yo) (Explicit Album Version)", None), "Bust It Baby, Pt. 2 (feat. Ne-Yo)")

    def test_meaningful_versions_are_kept(self):
        self.assertEqual(th.clean_title("Teardrops On My Guitar", "Pop Version"), "Teardrops On My Guitar (Pop Version)")
        self.assertEqual(th.clean_title("Old Town Road", "Remix"), "Old Town Road (Remix)")
        self.assertEqual(th.clean_title("Carry Out", "Featuring Justin Timberlake"), "Carry Out (Featuring Justin Timberlake)")
        self.assertEqual(th.clean_title("Low (feat. T-Pain)", "Feat T-Pain   Album Version"), "Low (feat. T-Pain)")
        self.assertEqual(th.clean_title("Is It Over Now? (Taylor's Version) (From The Vault)", None), "Is It Over Now? (Taylor's Version) (From The Vault)")


class GenreTests(unittest.TestCase):
    def test_mapping(self):
        self.assertEqual(th.map_genre("Hip-Hop/Rap"), ("Hip-Hop", True))
        self.assertEqual(th.map_genre("Pop/Rock"), ("Pop", True))
        self.assertEqual(th.map_genre("Soul/Funk/R&B"), ("R&B", True))
        self.assertEqual(th.map_genre("Alternative & Indie"), ("Rock", True))
        self.assertEqual(th.map_genre("Dance"), ("Electronic", True))
        self.assertEqual(th.map_genre("Humour"), ("Pop", False))
        self.assertEqual(th.map_genre(None), ("Pop", False))

    def test_every_target_is_an_allowed_genre(self):
        for _, target in th.GENRE_MAP:
            self.assertIn(target, th.ALLOWED_GENRES)


if __name__ == "__main__":
    unittest.main()
