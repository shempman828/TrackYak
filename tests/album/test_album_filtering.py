"""
Regression tests for AlbumFilteringMixin's year-range predicate.

Motivating bug: an album with a non-numeric release_year hit the
`except (TypeError, ValueError): pass` branch and was silently treated as
passing the year filter, while an album with no release_year at all was
correctly excluded when a year filter was active -- the two "we don't know
the year" cases behaved inconsistently.
"""

from types import SimpleNamespace

import pytest

from src.album.view.album_filtering import AlbumFilteringMixin


class _FilterHost(AlbumFilteringMixin):
    pass


def _params(**overrides):
    base = {
        "text": "",
        "year_from": 0,
        "year_to": 0,
        "min_tracks": 0,
        "incomplete_mode": "Any",
        "fixed_mode": "Any",
        "type_mode": "Any",
        "media_mode": "Any",
        "art_mode": "Any",
        "art_generation": 0,
        "art_cache": None,
    }
    base.update(overrides)
    return base


@pytest.mark.parametrize(
    ("release_year", "year_filter", "expected"),
    [
        (2005, (2000, 2010), True),
        (1990, (2000, 2010), False),
        ("unknown", (2000, 2010), False),
        (None, (2000, 2010), False),
        ("unknown", (0, 0), True),
    ],
)
def test_year_range_predicate(release_year, year_filter, expected):
    params = _params(year_from=year_filter[0], year_to=year_filter[1])
    assert _FilterHost()._album_matches_filters(SimpleNamespace(release_year=release_year), params) is expected
