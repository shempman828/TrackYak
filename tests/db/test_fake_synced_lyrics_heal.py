"""Tests for scripts/heal_fake_synced_lyrics.py detection + heal helpers.

The one-time repair that strips lyriq's fabricated per-line ``[NN.NN] ``
placeholders from Track.lyrics rows written by the old _format_lyrics.
"""

from scripts.heal_fake_synced_lyrics import _heal, _is_fake_timed

FAKE = "\n".join(f"[{i:02d}.00] line {i}" for i in range(6))
FAKE_WITH_GAP = "[00.00] a\n[01.00] b\n\n[03.00] d\n[04.00] e\n[05.00] f"
REAL = "[00:08.33] a\n[00:14.01] b\n[00:19.00] c\n[00:24.39] d"


def test_detects_fabricated_placeholder_block():
    assert _is_fake_timed(FAKE) is True


def test_detects_fabricated_block_with_blank_gap_lines():
    assert _is_fake_timed(FAKE_WITH_GAP) is True


def test_ignores_real_synced_lyrics():
    assert _is_fake_timed(REAL) is False


def test_ignores_block_that_also_contains_a_real_lrc_line():
    mixed = "[00.00] a\n[01.00] b\n[02.00] c\n[00:30.00] real"
    assert _is_fake_timed(mixed) is False


def test_ignores_plain_lyrics():
    assert _is_fake_timed("just plain\nlyrics here\nnothing bracketed") is False


def test_needs_at_least_three_placeholder_lines():
    assert _is_fake_timed("[00.00] a\n[01.00] b") is False


def test_heal_strips_every_prefix_and_preserves_text_and_blank_lines():
    assert _heal(FAKE_WITH_GAP) == "a\nb\n\nd\ne\nf"


def test_heal_is_idempotent():
    once = _heal(FAKE)
    assert _heal(once) == once
    assert once == "\n".join(f"line {i}" for i in range(6))


def test_heal_restores_line_order_scrambled_by_string_sorted_keys():
    # lyriq keys "10.00" < "100.00" < "11.00" string-sort out of order for
    # 100+-line tracks; the prefix int is the real index, so heal re-sorts.
    scrambled = "[09.00] i9\n[10.00] i10\n[100.00] i100\n[101.00] i101\n[11.00] i11\n[12.00] i12"

    assert _heal(scrambled) == "i9\ni10\ni11\ni12\ni100\ni101"


def test_is_fake_timed_matches_scrambled_100plus_line_track():
    scrambled = "\n".join(f"[{i}.00] line {i}" for i in [0, 1, 10, 100, 101, 11, 2, 3])
    assert _is_fake_timed(scrambled) is True
