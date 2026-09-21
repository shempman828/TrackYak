"""Tests for normalize_release_type, including the non-str-input edge case:
a multi-value tag field can hand this function a list instead of a str,
and it must pass such values through unchanged instead of crashing on
.strip().
"""

from src.album.release_type_utils import is_single, normalize_release_type


def test_normalize_release_type_trims_and_canonicalizes_known_values():
    assert normalize_release_type("  album ") == "Album"
    assert normalize_release_type("EP") == "EP"


def test_normalize_release_type_passes_through_unrecognized_strings():
    assert normalize_release_type("Bonus Disc") == "Bonus Disc"


def test_normalize_release_type_returns_none_for_none_or_blank():
    assert normalize_release_type(None) is None
    assert normalize_release_type("   ") is None


def test_normalize_release_type_returns_non_str_input_unchanged():
    value = ["a", "b"]
    assert normalize_release_type(value) is value


def test_is_single_trusts_explicit_release_type_over_track_count():
    # An explicit "Single" wins even with an unusual track count.
    assert is_single("single", track_count=5, disc_count=1) is True
    # An explicit non-single type wins even with a single-shaped track count.
    assert is_single("Album", track_count=2, disc_count=1) is False


def test_is_single_falls_back_to_track_count_heuristic_when_type_unset():
    assert is_single(None, track_count=2, disc_count=1) is True
    assert is_single("", track_count=1, disc_count=0) is True
    assert is_single(None, track_count=3, disc_count=1) is False
    assert is_single(None, track_count=2, disc_count=2) is False
