"""Tests for normalize_release_type, including the non-str-input edge case:
a multi-value tag field can hand this function a list instead of a str,
and it must pass such values through unchanged instead of crashing on
.strip().
"""

from src.album.release_type_utils import normalize_release_type


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
