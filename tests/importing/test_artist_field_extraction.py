"""Tests for normalize_artists_data's delimiter handling and type safety.
"/" and "|" commonly appear inside one artist name (e.g. "AC/DC"), so only
";" and "," split; an unsupported metadata type yields no artists.
"""

import pytest

from src.importing.artist_field_extraction import normalize_artists_data


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("AC/DC", ["AC/DC"]),
        ("Artist|Name", ["Artist|Name"]),
        ("Artist A; Artist B", ["Artist A", "Artist B"]),
        ("Artist A, Artist B", ["Artist A", "Artist B"]),
        ({"unexpected": "dict"}, []),
    ],
)
def test_normalize_artists_data(raw, expected):
    assert normalize_artists_data(raw) == expected
