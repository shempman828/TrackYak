"""Tests for src/lyrics/lyrics_format.py::format_lyrics_for_storage.

Guards the fix for fabricated per-line lyric timestamps: lyriq's Lyrics
object always carries a populated ``lyrics`` dict, and for plain-only LRCLib
entries those keys are line-index placeholders ("00.00", "01.00", ...).
Storing that dict as ``[00.00] line`` baked fake timing into the field.
"""

from lyriq import Lyrics

from src.lyrics.lyrics_format import format_lyrics_for_storage


def _lyrics(synced: str, plain: str) -> Lyrics:
    return Lyrics.from_dict(
        {
            "id": 1,
            "trackName": "T",
            "artistName": "A",
            "syncedLyrics": synced,
            "plainLyrics": plain,
            "instrumental": False,
        }
    )


def test_plain_only_result_is_stored_without_fabricated_timestamps():
    obj = _lyrics("", "First line\nSecond line\n\nFourth line\nFifth line")

    out = format_lyrics_for_storage(obj)

    assert out == "First line\nSecond line\n\nFourth line\nFifth line"
    assert "[00.00]" not in out and "[01.00]" not in out


def test_real_synced_result_is_stored_verbatim_as_lrc():
    synced = "[00:08.33] Real first line\n[00:14.01] Real second line\n[00:24.39] Real fourth line"
    obj = _lyrics(synced, "Real first line\nReal second line\nReal fourth line")

    assert format_lyrics_for_storage(obj) == synced


def test_plain_string_passthrough():
    assert format_lyrics_for_storage("already plain\ntext") == "already plain\ntext"


def test_bare_dict_with_fabricated_keys_drops_the_placeholders():
    fabricated = {f"{i:02d}.00": f"line {i}" for i in range(5)}

    out = format_lyrics_for_storage(fabricated)

    assert out == "line 0\nline 1\nline 2\nline 3\nline 4"


def test_bare_dict_with_real_timestamps_is_rendered_as_lrc():
    real = {"00:08.33": "one", "00:14.01": "two", "00:24.39": "three"}

    out = format_lyrics_for_storage(real)

    assert out == "[00:08.33] one\n[00:14.01] two\n[00:24.39] three"
