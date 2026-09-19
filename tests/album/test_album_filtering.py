"""
Regression tests for AlbumFilteringMixin's year-range predicate and
filter-state persistence.

Motivating bug: an album with a non-numeric release_year hit the
`except (TypeError, ValueError): pass` branch and was silently treated as
passing the year filter, while an album with no release_year at all was
correctly excluded when a year filter was active -- the two "we don't know
the year" cases behaved inconsistently.
"""

from types import SimpleNamespace

from src.album.album_filtering import AlbumFilteringMixin


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


def test_non_numeric_release_year_excluded_when_year_filter_active():
    host = _FilterHost()
    album = SimpleNamespace(release_year="unknown")
    params = _params(year_from=2000, year_to=2010)
    assert host._album_matches_filters(album, params) is False


def test_missing_release_year_excluded_when_year_filter_active():
    host = _FilterHost()
    album = SimpleNamespace(release_year=None)
    params = _params(year_from=2000, year_to=2010)
    assert host._album_matches_filters(album, params) is False


def test_non_numeric_release_year_passes_when_no_year_filter_active():
    host = _FilterHost()
    album = SimpleNamespace(release_year="unknown")
    params = _params()
    assert host._album_matches_filters(album, params) is True


def test_numeric_release_year_still_filters_normally():
    host = _FilterHost()
    in_range = SimpleNamespace(release_year=2005)
    out_of_range = SimpleNamespace(release_year=1990)
    params = _params(year_from=2000, year_to=2010)
    assert host._album_matches_filters(in_range, params) is True
    assert host._album_matches_filters(out_of_range, params) is False


class _FakeAppConfig:
    def __init__(self, raise_on_save=False, raise_on_get=False):
        self._state = None
        self._raise_on_save = raise_on_save
        self._raise_on_get = raise_on_get

    def set_album_view_filters(self, state):
        self._state = state

    def get_album_view_filters(self):
        if self._raise_on_get:
            raise OSError("disk full")
        return self._state

    def save(self):
        if self._raise_on_save:
            raise OSError("disk full")


def test_save_filter_state_does_not_raise_on_io_error(monkeypatch):
    import src.album.album_filtering as album_filtering_module

    fake_config = _FakeAppConfig(raise_on_save=True)
    monkeypatch.setattr(album_filtering_module, "app_config", fake_config)

    host = _FilterHost()
    host._get_filter_state = lambda: {"search": "x"}
    # Should not raise despite app_config.save() failing.
    host._save_filter_state()


def test_restore_filter_state_does_not_raise_on_io_error(monkeypatch):
    import src.album.album_filtering as album_filtering_module

    fake_config = _FakeAppConfig(raise_on_get=True)
    monkeypatch.setattr(album_filtering_module, "app_config", fake_config)

    host = _FilterHost()
    # Should not raise despite app_config.get_album_view_filters() failing.
    host._restore_filter_state()
