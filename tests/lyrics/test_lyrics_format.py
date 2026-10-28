"""Tests for src/lyrics/lyrics_format.py::format_lyrics_for_storage.

Guards the fix for fabricated per-line lyric timestamps: lyriq's Lyrics
object always carries a populated ``lyrics`` dict, and for plain-only LRCLib
entries those keys are line-index placeholders ("00.00", "01.00", ...).
Storing that dict as ``[00.00] line`` baked fake timing into the field.
"""

from lyriq import Lyrics
import pytest

from src.lyrics.lyrics_format import format_lyrics_for_storage


def _lyrics(synced: str, plain: str) -> Lyrics:
    return Lyrics.from_dict({"id": 1, "trackName": "T", "artistName": "A", "syncedLyrics": synced, "plainLyrics": plain, "instrumental": False})


def test_plain_only_result_is_stored_without_fabricated_timestamps():
    obj = _lyrics("", "First line\nSecond line\n\nFourth line\nFifth line")

    out = format_lyrics_for_storage(obj)

    assert out == "First line\nSecond line\n\nFourth line\nFifth line"
    assert "[00.00]" not in out and "[01.00]" not in out


def test_real_synced_result_is_stored_verbatim_as_lrc():
    synced = "[00:08.33] Real first line\n[00:14.01] Real second line\n[00:24.39] Real fourth line"
    obj = _lyrics(synced, "Real first line\nReal second line\nReal fourth line")

    assert format_lyrics_for_storage(obj) == synced


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, ""),
        ("already plain\ntext", "already plain\ntext"),
        ({f"{i:02d}.00": f"line {i}" for i in range(5)}, "line 0\nline 1\nline 2\nline 3\nline 4"),  # fabricated keys dropped
        ({"00:08.33": "one", "00:14.01": "two", "00:24.39": "three"}, "[00:08.33] one\n[00:14.01] two\n[00:24.39] three"),
        ({(f"0{i}" if i < 10 else str(i)) + ".00": f"line {i}" for i in range(120)}, "\n".join(f"line {i}" for i in range(120))),
        ({"00.00": "only"}, "only"),
        ({"00.00": "a", "01.00": "b"}, "a\nb"),
        ({"00.00": "a", "05.00": "b"}, "[00.00] a\n[05.00] b"),  # gap in index run: not fabricated
    ],
    ids=["none", "plain-string", "fabricated-dict", "real-timestamps", "over-100-fabricated", "one-fabricated", "two-fabricated", "index-gap"],
)
def test_bare_values_are_formatted_for_storage(value, expected):
    assert format_lyrics_for_storage(value) == expected
