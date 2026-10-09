"""Tests for src/nowplaying/lyrics/nowplaying_lyrics_parser.py::parse_lyrics.

Focus: fabricated / placeholder timing must not be treated as real sync.
"""

from src.nowplaying.lyrics.nowplaying_lyrics_parser import build_lrc, format_timestamp_ms, parse_lyrics


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


def test_untimed_lines_in_synced_lyrics_keep_their_place():
    # A blank stanza break with no stamp must not jump to the top.
    raw = "[00:05.00] one\n[00:10.00] two\n\n[00:20.00] three\n[00:25.00] four"

    is_synced, lines = parse_lyrics(raw)

    assert is_synced is True
    assert [t for _, t in lines] == ["one", "two", "", "three", "four"]
    assert lines[2] == (10_000, "")


def test_lrc_id_tags_are_not_lyric_lines():
    raw = "[ar:Some Artist]\n[ti:Some Title]\n[offset:+500]\n[length: 3:20]\n[00:05.00] one\n[00:10.00] two"

    is_synced, lines = parse_lyrics(raw)

    assert is_synced is True
    assert lines == [(5_000, "one"), (10_000, "two")]


def test_section_headers_in_plain_lyrics_are_kept():
    raw = "[Chorus: Someone]\nla la\n[Verse 1]"

    is_synced, lines = parse_lyrics(raw)

    assert is_synced is False
    assert [t for _, t in lines] == ["[Chorus: Someone]", "la la", "[Verse 1]"]


def test_line_with_several_timestamps_repeats_at_each():
    raw = "[00:05.00] verse\n[00:10.00][00:30.00] chorus\n[00:20.00] bridge"

    _, lines = parse_lyrics(raw)

    assert lines == [(5_000, "verse"), (10_000, "chorus"), (20_000, "bridge"), (30_000, "chorus")]


def test_timestamps_of_100_minutes_or_more_parse():
    raw = "[99:59.00] a\n[100:00.50] b\n[101:00.00] c\n[102:00.00] d"

    is_synced, lines = parse_lyrics(raw)

    assert is_synced is True
    assert lines[1] == (6_000_500, "b")


def test_active_index_bisects_sorted_lines():
    from src.nowplaying.lyrics.nowplaying_lyrics_parser import active_index

    lines = [(1_000, "a"), (2_000, "b"), (3_000, "c")]
    assert active_index(lines, 0) == 0  # before the first line
    assert active_index(lines, 2_000) == 1
    assert active_index(lines, 2_999) == 1
    assert active_index(lines, 99_000) == 2
    assert active_index([], 5) == 0
