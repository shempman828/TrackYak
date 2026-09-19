"""Tests for normalize_artists_data's delimiter handling and type safety.

Regression coverage for two fixes:
  - "/" and "|" are no longer treated as multi-artist delimiters, since
    they commonly appear inside a single artist name (e.g. "AC/DC").
  - An unsupported metadata type (e.g. a dict) is now rejected instead of
    being blindly stringified into a garbage artist name.
"""

import pytest

from src.importing.artist_field_extraction import normalize_artists_data


def test_slash_in_artist_name_is_not_split():
    assert normalize_artists_data("AC/DC") == ["AC/DC"]


def test_pipe_in_artist_name_is_not_split():
    assert normalize_artists_data("Artist|Name") == ["Artist|Name"]


def test_semicolon_still_splits_multiple_artists():
    assert normalize_artists_data("Artist A; Artist B") == ["Artist A", "Artist B"]


def test_comma_still_splits_multiple_artists():
    assert normalize_artists_data("Artist A, Artist B") == ["Artist A", "Artist B"]


def test_unsupported_type_returns_empty_list():
    assert normalize_artists_data({"unexpected": "dict"}) == []


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
