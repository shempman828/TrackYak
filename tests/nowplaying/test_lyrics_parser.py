"""Tests for src/nowplaying/nowplaying_lyrics_parser.py::parse_lyrics.

Focus: fabricated / placeholder timing must not be treated as real sync.
"""

from src.nowplaying.nowplaying_lyrics_parser import build_lrc, format_timestamp_ms, parse_lyrics


def test_real_synced_lyrics_parse_as_synced():
    raw = "[00:08.33] one\n[00:14.01] two\n[00:19.00] three\n[00:24.39] four"

    is_synced, lines = parse_lyrics(raw)

    assert is_synced is True
    assert lines[0] == (8330, "one")
    assert lines[-1] == (24390, "four")


def test_sequential_one_second_timestamps_are_rejected_as_plain():
    raw = "[00:00.00] a\n[00:01.00] b\n[00:02.00] c\n[00:03.00] d\n[00:04.00] e"

    is_synced, lines = parse_lyrics(raw)

    assert is_synced is False
    assert [t for _, t in lines] == ["a", "b", "c", "d", "e"]
    assert all(ms == 0 for ms, _ in lines)


def test_every_line_on_the_same_timestamp_is_rejected_as_plain():
    raw = "\n".join("[00:00.00] line" for _ in range(5))

    is_synced, _ = parse_lyrics(raw)

    assert is_synced is False


def test_three_sequential_lines_is_too_few_to_call_fake():
    # Guard the >=4 threshold: a genuine intro could open on round seconds.
    raw = "[00:00.00] a\n[00:01.00] b\n[00:02.00] c"

    is_synced, _ = parse_lyrics(raw)

    assert is_synced is True


def test_plain_lyrics_unchanged():
    raw = "just some\nplain lyrics\nno timing"

    is_synced, lines = parse_lyrics(raw)

    assert is_synced is False
    assert [t for _, t in lines] == ["just some", "plain lyrics", "no timing"]


def test_format_timestamp_ms_renders_mm_ss_centiseconds():
    assert format_timestamp_ms(0) == "00:00.00"
    assert format_timestamp_ms(8330) == "00:08.33"
    assert format_timestamp_ms(65_005) == "01:05.00"


def test_format_timestamp_ms_clamps_negative_to_zero():
    assert format_timestamp_ms(-500) == "00:00.00"


def test_build_lrc_is_the_inverse_of_parse_lyrics():
    lines = [(8330, "one"), (14010, "two"), (19000, "three"), (24390, "four")]

    lrc = build_lrc(lines)
    is_synced, parsed = parse_lyrics(lrc)

    assert is_synced is True
    assert parsed == lines
