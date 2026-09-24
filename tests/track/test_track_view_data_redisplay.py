"""Reloading the track list (delete, edit, Refresh) keeps the active search.

Regression: deleting a track in the track view reset the table to the full
library while the search text stayed in the search bar.
"""

import pytest

from src.track.view.track_view_data import TrackViewDataMixin
import src.track.view.track_view_search as search_mod
from src.track.view.track_view_search import TrackViewSearchMixin


class _Track:
    def __init__(self, track_id, name):
        self.track_id = track_id
        self.track_name = name


class _SearchBar:
    def __init__(self, text=""):
        self._text = text

    def text(self):
        return self._text


class _Model:
    def __init__(self):
        self.rows = []

    def setRowCount(self, n):
        self.rows = self.rows[:n]


class _Signal:
    def __init__(self):
        self._slots = []

    def connect(self, slot):
        self._slots.append(slot)

    def emit(self, *args):
        for slot in self._slots:
            slot(*args)


class _SyncFilterWorker:
    """Runs the filter inline instead of on a QThread."""

    def __init__(self, tracks, text, *_args):
        self._tracks = tracks
        self._text = text
        self.finished = _Signal()

    def isRunning(self):
        return False

    def start(self):
        self.finished.emit([t for t in self._tracks if self._text in t.track_name.lower()])


class _Host(TrackViewDataMixin, TrackViewSearchMixin):
    def __init__(self, search_text):
        self.search_bar = _SearchBar(search_text)
        self.model = _Model()
        self._filter_worker = None
        self._search_field_name = None
        self._loaded_count = 0

    def _build_lookup_caches(self):
        pass

    def _append_next_batch(self, source_list):
        self.model.rows = list(source_list)
        self._loaded_count = len(source_list)

    def _update_status(self):
        pass

    def _get_artist_name(self, *_args):
        return ""

    def _format_value(self, *_args):
        return ""

    def _field_value(self, *_args):
        return ""


@pytest.fixture(autouse=True)
def _sync_filter(monkeypatch):
    monkeypatch.setattr(search_mod, "FilterWorker", _SyncFilterWorker)


def test_load_data_after_delete_keeps_search_results():
    host = _Host("blue")
    remaining = [_Track(1, "Blue Monday"), _Track(3, "Red Rain"), _Track(4, "Kind of Blue")]

    host.load_data(remaining)

    assert host._filter_active is True
    assert [t.track_id for t in host.model.rows] == [1, 4]


def test_load_data_without_search_shows_all_tracks():
    host = _Host("")
    tracks = [_Track(1, "Blue Monday"), _Track(3, "Red Rain")]

    host.load_data(tracks)

    assert host._filter_active is False
    assert [t.track_id for t in host.model.rows] == [1, 3]
