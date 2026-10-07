"""The main TrackView opens in a per-session random order, not by track id.

Feature: with 50k+ tracks, the id order showed the same first rows at every
start. TrackView now shuffles its default (unsearched, unsorted) order with
one seed per program run; BaseTrackView popups keep the order they are given.
"""

import time

import pytest

from src.track.view.base_track_view import BaseTrackView
from src.track.view.track_view import TrackView
import src.track.view.track_view_data as data_mod
from src.track.view.track_view_data import TrackViewDataMixin, session_shuffled
import src.track.view.track_view_search as search_mod
from src.track.view.track_view_search import TrackViewSearchMixin


class _Track:
    def __init__(self, track_id, name=""):
        self.track_id = track_id
        self.track_name = name


class _SearchBar:
    def __init__(self, text=""):
        self._text = text

    def text(self):
        return self._text


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


class _Model:
    def setRowCount(self, _n):
        pass


class _Label:
    def setText(self, _text):
        pass


class _Host(TrackViewDataMixin, TrackViewSearchMixin):
    def __init__(self, shuffle):
        self._shuffle_default_order = shuffle
        self.search_bar = _SearchBar()
        self.model = _Model()
        self.status_label = _Label()
        self.rows = []
        self._filter_worker = None
        self._search_field_name = None
        self._loaded_count = 0

    def _build_lookup_caches(self):
        pass

    def _append_next_batch(self, source_list):
        self.rows = list(source_list)
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


def _ids(tracks):
    return [t.track_id for t in tracks]


def test_ac1_different_session_seeds_give_different_orders():
    tracks = [_Track(i) for i in range(1, 1001)]

    order_a = _ids(session_shuffled(tracks, seed=1))
    order_b = _ids(session_shuffled(tracks, seed=2))

    assert sorted(order_a) == _ids(tracks)
    assert order_a != _ids(tracks)
    assert order_a[:20] != order_b[:20]


def test_ac1_same_seed_gives_same_order_on_reload():
    """A Refresh re-fetches in id order; the session order must not change."""
    tracks = [_Track(i) for i in range(1, 501)]

    first = _ids(session_shuffled(tracks, seed=7))
    reloaded = _ids(session_shuffled(list(reversed(tracks)), seed=7))

    assert first == reloaded


def test_ac2_clearing_search_restores_session_order(monkeypatch):
    monkeypatch.setattr(data_mod, "_SESSION_SHUFFLE_SEED", 42)
    host = _Host(shuffle=True)
    tracks = [_Track(i, "blue" if i % 2 else "red") for i in range(1, 201)]

    host.load_data(tracks)
    session_order = _ids(host.rows)
    assert session_order == _ids(session_shuffled(tracks, seed=42))

    host.search_bar = _SearchBar("blue")
    host._apply_search_filter()
    assert _ids(host.rows) == [i for i in session_order if i % 2]

    host.search_bar = _SearchBar("")
    host._apply_search_filter()
    assert _ids(host.rows) == session_order


def test_ac3_column_sort_replaces_shuffled_order(monkeypatch):
    monkeypatch.setattr(data_mod, "_SESSION_SHUFFLE_SEED", 42)
    host = _Host(shuffle=True)
    host.table = type("T", (), {"setEnabled": lambda self, _v: None})()
    tracks = [_Track(i) for i in range(1, 101)]
    host.load_data(tracks)

    host._on_sort_done(sorted(host._all_tracks, key=lambda t: t.track_id))

    assert _ids(host.rows) == list(range(1, 101))


def test_ac4_only_main_track_view_shuffles():
    assert TrackView._shuffle_default_order is True
    assert BaseTrackView._shuffle_default_order is False

    host = _Host(shuffle=False)
    tracks = [_Track(i) for i in (5, 3, 9, 1)]
    host.load_data(tracks)

    assert _ids(host.rows) == [5, 3, 9, 1]


def test_ac5_shuffle_of_large_library_is_fast():
    tracks = [_Track(i) for i in range(1, 60_001)]

    start = time.perf_counter()
    session_shuffled(tracks, seed=123)
    elapsed = time.perf_counter() - start

    assert elapsed < 0.25
