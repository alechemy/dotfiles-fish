"""Shared helpers for music-tagging scripts.

Imported by tagger.py and import-album.py via:

    import pathlib, sys
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    from _music_tags import norm_artist, compilation_signal, is_compilation_album

Pure stdlib so it loads from any uv run --script venv without per-script
dependency declaration.
"""

import re

_FEAT_RE = re.compile(r"\s+(?:feat\.?|ft\.?|featuring|with)\s+", re.I)


def norm_artist(s):
    """Strip 'feat./ft./featuring/with' suffixes; lowercase; collapse whitespace."""
    return _FEAT_RE.split(str(s or ""), maxsplit=1)[0].strip().lower()


def compilation_signal(album_artists, track_artists):
    """Which signal (if any) marks this album as a compilation.

    Pass already-normalized artist strings (use norm_artist). Returns:
      'various_artists' — any album_artist is 'various artists'
      'multi_artist'    — 2+ distinct per-track artists
      None              — neither signal fired

    The 'various_artists' check wins so that single-composer OSTs labeled
    'Various Artists' at the album level (e.g. Qobuz's Spectacular Now) still
    get cpil=True even though every per-track artist is the composer.
    """
    if any(a == "various artists" for a in album_artists if a):
        return "various_artists"
    distinct = {a for a in track_artists if a}
    if len(distinct) > 1:
        return "multi_artist"
    return None


def is_compilation_album(album_artists, track_artists):
    """Boolean form of compilation_signal — True if any signal fires."""
    return compilation_signal(album_artists, track_artists) is not None
