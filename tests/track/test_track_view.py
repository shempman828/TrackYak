"""Main TrackView: shared actions, right-click submenus, per-session
shuffle, filing-name artist sort, search kept on reload, and lookup-cache
worker session release.
"""

import threading
import time
from types import SimpleNamespace

from PySide6.QtCore import QItemSelectionModel, QThread
from PySide6.QtGui import QStandardItem, QStandardItemModel
from PySide6.QtWidgets import QMenu, QMessageBox, QTableView, QWidget
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import scoped_session, sessionmaker

from src.db.db_helpers.add import AddToDB
from src.db.db_helpers.get import GetFromDB
from src.db.db_tables import Album, Artist, Disc, Role, Track, TrackArtistRole
from src.db.db_tables.base import Base
from src.db.db_tables.mood import Mood, MoodTrackAssociation
from src.db.db_tables.playlist import Playlist, PlaylistTracks
from src.track.view.base_track_view import BaseTrackView
from src.track.view.track_columns import ColumnCustomizationDialog
from src.track.view.track_view import TrackView
import src.track.view.track_view_actions as track_view_actions_module
from src.track.view.track_view_actions import TrackViewActionsMixin
import src.track.view.track_view_columns as track_view_columns_module
import src.track.view.track_view_data as data_mod
from src.track.view.track_view_data import TrackLookupCacheWorker, TrackViewDataMixin, _fetch_lookup_caches, session_shuffled
from src.track.view.track_view_editing import TrackViewEditingMixin, add_tracks_to_mood, add_tracks_to_playlist, delete_tracks_with_prompt, track_names_preview
import src.track.view.track_view_search as search_mod
from src.track.view.track_view_search import TrackViewSearchMixin

# ---------------------------------------------------------------------------
# Shared track-view helpers: delete, playlist/mood add, row selection, column reset and BaseTrackView drag.
# ---------------------------------------------------------------------------


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine, expire_on_commit=False)()
    yield s
    s.close()


class _FakeAppConfig:
    def __init__(self):
        self.visible, self.order, self.widths = [], [], []

    def get_track_view_visible_columns(self):
        return self.visible

    def set_track_view_visible_columns(self, value):
        self.visible = list(value)

    def get_track_view_column_order(self):
        return self.order

    def set_track_view_column_order(self, value):
        self.order = list(value)

    def get_track_view_column_widths(self):
        return self.widths

    def set_track_view_column_widths(self, value):
        self.widths = list(value)


@pytest.fixture(autouse=True)
def _isolated_column_config(monkeypatch):
    monkeypatch.setattr(track_view_columns_module, "app_config", _FakeAppConfig())


class _Delete:
    def __init__(self, db_ok):
        self.db_ok = db_ok
        self.files = []

    def delete_entity(self, model_name, entity_ids=None, **_):
        return self.db_ok

    def delete_file(self, file_path=None):
        self.files.append(file_path)
        return True


def test_delete_keeps_files_when_db_delete_fails(qapp, monkeypatch):
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warnings.append(a))
    controller = SimpleNamespace(delete=_Delete(db_ok=False))
    tracks = [SimpleNamespace(track_id=1, track_name="A", track_file_path="/x/a.mp3")]

    result = delete_tracks_with_prompt(QWidget(), controller, tracks, confirm=lambda *a: "db_and_file")

    assert result == []
    assert controller.delete.files == []
    assert warnings


def test_delete_removes_files_after_db_delete(qapp):
    controller = SimpleNamespace(delete=_Delete(db_ok=True))
    tracks = [SimpleNamespace(track_id=1, track_name="A", track_file_path="/x/a.mp3")]
    assert delete_tracks_with_prompt(QWidget(), controller, tracks, confirm=lambda *a: "db_and_file") == [1]
    assert controller.delete.files == ["/x/a.mp3"]


def test_delete_cancel_returns_none(qapp):
    controller = SimpleNamespace(delete=_Delete(db_ok=True))
    assert delete_tracks_with_prompt(QWidget(), controller, [SimpleNamespace(track_id=1, track_name="A")], confirm=lambda *a: None) is None


def test_names_preview_handles_missing_names():
    tracks = [SimpleNamespace(track_id=i, track_name=None if i == 2 else f"T{i}") for i in range(1, 6)]
    assert track_names_preview(tracks) == "T1, ID 2, T3 … and 2 more"


def test_add_to_playlist_appends_and_skips_members(session):
    controller = SimpleNamespace(get=GetFromDB(session), add=AddToDB(session))
    pl = Playlist(playlist_name="P")
    t1, t2, t3 = Track(track_name="1"), Track(track_name="2"), Track(track_name="3")
    session.add_all([pl, t1, t2, t3])
    session.commit()
    session.add(PlaylistTracks(playlist_id=pl.playlist_id, track_id=t1.track_id, position=1))
    session.commit()

    added, already, failed = add_tracks_to_playlist(controller, pl.playlist_id, [str(t1.track_id), str(t2.track_id), str(t3.track_id)])

    assert (added, already, failed) == (2, 1, 0)
    positions = {r.track_id: r.position for r in session.query(PlaylistTracks).all()}
    assert positions == {t1.track_id: 1, t2.track_id: 2, t3.track_id: 3}


def test_add_to_mood_skips_members(session):
    controller = SimpleNamespace(get=GetFromDB(session), add=AddToDB(session))
    mood = Mood(mood_name="Calm")
    t1, t2 = Track(track_name="1"), Track(track_name="2")
    session.add_all([mood, t1, t2])
    session.commit()
    session.add(MoodTrackAssociation(mood_id=mood.mood_id, track_id=t1.track_id))
    session.commit()

    assert add_tracks_to_mood(controller, mood.mood_id, [t1.track_id, t2.track_id]) == (1, 1, 0)
    assert session.query(MoodTrackAssociation).count() == 2


class _Host(QWidget, TrackViewActionsMixin):
    def __init__(self, tracks):
        super().__init__()
        self._all_tracks = tracks
        self._filtered_tracks = []
        self._filter_active = False
        self.controller = SimpleNamespace(get=None)  # any DB access would raise
        self.model = QStandardItemModel(len(tracks), 1)
        for row, t in enumerate(tracks):
            self.model.setItem(row, 0, QStandardItem(t.track_name))
        self.table = QTableView()
        self.table.setModel(self.model)

    def _visible_source(self):
        return self._filtered_tracks if self._filter_active else self._all_tracks


def test_selected_track_objects_come_from_visible_list(qapp):
    tracks = [SimpleNamespace(track_id=i, track_name=f"T{i}") for i in range(5)]
    host = _Host(tracks)
    sel = host.table.selectionModel()
    for row in (3, 1):
        sel.select(host.model.index(row, 0), QItemSelectionModel.Select | QItemSelectionModel.Rows)
    assert [t.track_id for t in host._selected_tracks()] == [1, 3]


def _view(session, **kwargs):
    t = Track(track_name="T")
    session.add(t)
    session.commit()
    return BaseTrackView(SimpleNamespace(get=GetFromDB(session)), [t], **kwargs)


def test_column_reset_restores_view_defaults(qapp, session):
    view = _view(session)
    dialog = ColumnCustomizationDialog(view, view)
    dialog.reset_to_default()
    state = dialog.get_selected_state()

    assert state["order"] == list(view.columns)
    assert set(state["visible"]) == set(view.columns) - view.default_hidden_columns()
    assert "track_name" in state["visible"]


def test_column_dialog_refuses_to_hide_every_column(qapp, session, monkeypatch):
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warnings.append(a))
    view = _view(session)
    dialog = ColumnCustomizationDialog(view, view)
    dialog.deselect_all()
    dialog.apply_changes()

    assert warnings
    assert any(not view.table.isColumnHidden(i) for i in range(len(view.columns)))


def test_load_column_state_applies_order_and_visibility_and_skips_unknown_keys(qapp, session):
    view = _view(session)
    keys = list(view.columns)
    wanted_order = [keys[2], "no_such_column", keys[0], keys[1], *keys[3:]]
    config = track_view_columns_module.app_config
    config.order = wanted_order
    config.visible = [keys[0], keys[2], "no_such_column"]

    view.load_column_state()
    state = view.get_column_state()

    assert state["order"] == [k for k in wanted_order if k in view.columns]
    assert state["visible"] == [keys[0], keys[2]]


def test_base_view_drag_starts_on_table_with_selected_tracks(qapp, session, monkeypatch):
    dragged = []
    monkeypatch.setattr(track_view_actions_module, "start_track_drag", lambda source, tracks: dragged.append((source, [t.track_id for t in tracks])))
    view = _view(session, enable_drag=True)
    view.table.selectRow(0)

    view.table.startDrag(None)

    assert dragged == [(view.table, [view._all_tracks[0].track_id])]


# ---------------------------------------------------------------------------
# Regression tests for the main library Tracks tab right-click submenus.
# ---------------------------------------------------------------------------


@pytest.fixture
def session_menu():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


class _Controller:
    def __init__(self, session_menu):
        self.get = GetFromDB(session_menu)
        self.add = AddToDB(session_menu)


class _HostMenu(QWidget, TrackViewEditingMixin):
    def __init__(self, controller):
        super().__init__()
        self.controller = controller


def _texts(menu):
    return [a.text() for a in menu.actions() if not a.isSeparator()]


def test_playlist_submenu_now_exists_and_is_hierarchical(qapp, session_menu):
    rock = Playlist(playlist_name="Rock")
    session_menu.add_all([rock, Playlist(playlist_name="Jazz")])
    session_menu.flush()
    session_menu.add(Playlist(playlist_name="Punk", parent_id=rock.playlist_id))
    track = Track(track_name="T")
    session_menu.add(track)
    session_menu.commit()

    host = _HostMenu(_Controller(session_menu))
    menu = QMenu()
    host._populate_playlist_submenu(menu, [track], [str(track.track_id)])

    assert _texts(menu) == ["Jazz", "Rock"]
    rock_action = next(a for a in menu.actions() if a.text() == "Rock")
    assert rock_action.menu() is not None
    assert _texts(rock_action.menu()) == ["Punk", "Add to 'Rock'"]


def test_mood_submenu_nests_off_real_parent_id(qapp, session_menu):
    calm = Mood(mood_name="Calm")
    session_menu.add(calm)
    session_menu.flush()
    session_menu.add(Mood(mood_name="Serene", parent_id=calm.mood_id))
    track = Track(track_name="T")
    session_menu.add(track)
    session_menu.commit()

    host = _HostMenu(_Controller(session_menu))
    menu = QMenu()
    host._populate_mood_submenu(menu, [track], [str(track.track_id)])

    calm_action = next(a for a in menu.actions() if a.text() == "Calm")
    assert calm_action.menu() is not None
    assert _texts(calm_action.menu()) == ["Serene", "Add to 'Calm'"]


def test_checkmarks_reflect_multi_track_membership(qapp, session_menu):
    both = Mood(mood_name="Both")
    one = Mood(mood_name="One")
    session_menu.add_all([both, one])
    t1 = Track(track_name="T1")
    t2 = Track(track_name="T2")
    session_menu.add_all([t1, t2])
    session_menu.flush()
    session_menu.add_all(
        [
            MoodTrackAssociation(mood_id=both.mood_id, track_id=t1.track_id),
            MoodTrackAssociation(mood_id=both.mood_id, track_id=t2.track_id),
            MoodTrackAssociation(mood_id=one.mood_id, track_id=t1.track_id),
        ]
    )
    session_menu.commit()

    host = _HostMenu(_Controller(session_menu))
    menu = QMenu()
    host._populate_mood_submenu(menu, [t1, t2], [str(t1.track_id), str(t2.track_id)])

    by_text = {a.text(): a for a in menu.actions() if not a.isSeparator()}
    assert by_text["Both"].isChecked()
    assert by_text["One (partial)"].isCheckable()
    assert not by_text["One (partial)"].isChecked()


def test_add_to_playlist_handler_adds_links_for_whole_selection(qapp, session_menu):
    pl = Playlist(playlist_name="Target")
    session_menu.add(pl)
    t1 = Track(track_name="T1")
    t2 = Track(track_name="T2")
    session_menu.add_all([t1, t2])
    session_menu.commit()

    host = _HostMenu(_Controller(session_menu))
    menu = QMenu()
    host._populate_playlist_submenu(menu, [t1, t2], [str(t1.track_id), str(t2.track_id)])
    menu.actions()[0].trigger()  # "Target"

    links = session_menu.query(PlaylistTracks).filter_by(playlist_id=pl.playlist_id).all()
    assert {lnk.track_id for lnk in links} == {t1.track_id, t2.track_id}
    assert sorted(lnk.position for lnk in links) == [1, 2]


# ---------------------------------------------------------------------------
# The main TrackView opens in a per-session random order, not by track id.
# ---------------------------------------------------------------------------


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


class _HostShuffle(TrackViewDataMixin, TrackViewSearchMixin):
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
    host = _HostShuffle(shuffle=True)
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
    host = _HostShuffle(shuffle=True)
    host.table = type("T", (), {"setEnabled": lambda self, _v: None})()
    tracks = [_Track(i) for i in range(1, 101)]
    host.load_data(tracks)

    host._on_sort_done(sorted(host._all_tracks, key=lambda t: t.track_id))

    assert _ids(host.rows) == list(range(1, 101))


def test_ac4_only_main_track_view_shuffles():
    assert TrackView._shuffle_default_order is True
    assert BaseTrackView._shuffle_default_order is False

    host = _HostShuffle(shuffle=False)
    tracks = [_Track(i) for i in (5, 3, 9, 1)]
    host.load_data(tracks)

    assert _ids(host.rows) == [5, 3, 9, 1]


def test_ac5_shuffle_of_large_library_is_fast():
    tracks = [_Track(i) for i in range(1, 60_001)]

    start = time.perf_counter()
    session_shuffled(tracks, seed=123)
    elapsed = time.perf_counter() - start

    assert elapsed < 0.25


# ---------------------------------------------------------------------------
# AC7 -- the Track view's Artist column sorts by filing name
# ---------------------------------------------------------------------------


@pytest.fixture
def session_sort():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    sess = sessionmaker(bind=engine)()
    yield sess
    sess.close()


def _seed(session_sort):
    primary = Role(role_id=1, role_name="Primary Artist")
    session_sort.add(primary)
    session_sort.add_all([Track(track_id=tid, track_name=f"t{tid}") for tid in (10, 20, 30)])
    # track 10 -> "The Beatles" (files under B), track 20 -> "ABBA" (no
    # sort_name -> falls back to "abba"), track 30 -> "Tom Waits" (files
    # under W).
    session_sort.add_all(
        [
            Artist(artist_id=1, artist_name="The Beatles", sort_name="Beatles, The"),
            Artist(artist_id=2, artist_name="ABBA", sort_name=None),
            Artist(artist_id=3, artist_name="Tom Waits", sort_name="Waits, Tom"),
        ]
    )
    session_sort.add_all(
        [
            TrackArtistRole(track_id=10, artist_id=1, role_id=1),
            TrackArtistRole(track_id=20, artist_id=2, role_id=1),
            TrackArtistRole(track_id=30, artist_id=3, role_id=1),
        ]
    )
    session_sort.commit()


def test_fetch_builds_display_and_sort_caches(session_sort):
    _seed(session_sort)
    _album, _disc, name_cache, sort_cache = _fetch_lookup_caches(session_sort)

    # Display cache: plain names, unchanged behaviour.
    assert name_cache == {10: "The Beatles", 20: "ABBA", 30: "Tom Waits"}
    # Sort cache: filing names, with fallback to display name when sort_name is null.
    assert sort_cache == {10: "Beatles, The", 20: "ABBA", 30: "Waits, Tom"}


class _TrackSort:
    def __init__(self, track_id):
        self.track_id = track_id


def test_field_value_display_vs_sort(session_sort):
    _seed(session_sort)
    host = TrackViewDataMixin.__new__(TrackViewDataMixin)
    (
        host._album_cache,
        host._disc_number_cache,
        host._artist_name_cache,
        host._artist_sort_cache,
    ) = _fetch_lookup_caches(session_sort)

    tracks = [_TrackSort(10), _TrackSort(20), _TrackSort(30)]

    # Display field -> plain name join.
    assert [host._field_value(t, "primary_artist_names") for t in tracks] == [
        "The Beatles",
        "ABBA",
        "Tom Waits",
    ]

    # Sort pseudo-field -> filing names; ordering them puts "The Beatles"
    # (Beatles, The) between ABBA and Tom Waits.
    keyed = sorted(tracks, key=lambda t: host._field_value(t, "primary_artist_names__sort").lower())
    assert [t.track_id for t in keyed] == [20, 10, 30]


def test_sort_pseudo_field_falls_back_when_cache_missing(session_sort):
    host = TrackViewDataMixin.__new__(TrackViewDataMixin)
    host._artist_name_cache = {10: "Some Artist"}
    host._artist_sort_cache = {}
    assert host._field_value(_TrackSort(10), "primary_artist_names__sort") == "Some Artist"
    assert host._field_value(_TrackSort(99), "primary_artist_names__sort") == "Unknown Artist"


# ---------------------------------------------------------------------------
# Reloading the track list (delete, edit, Refresh) keeps the active search.
# ---------------------------------------------------------------------------


class _TrackRedisplay:
    def __init__(self, track_id, name):
        self.track_id = track_id
        self.track_name = name


class _SearchBarRedisplay:
    def __init__(self, text=""):
        self._text = text

    def text(self):
        return self._text


class _LabelRedisplay:
    def __init__(self):
        self.text = ""

    def setText(self, text):
        self.text = text


class _ModelRedisplay:
    def __init__(self):
        self.rows = []

    def setRowCount(self, n):
        self.rows = self.rows[:n]


class _SignalRedisplay:
    def __init__(self):
        self._slots = []

    def connect(self, slot):
        self._slots.append(slot)

    def emit(self, *args):
        for slot in self._slots:
            slot(*args)


class _SyncFilterWorkerRedisplay:
    """Runs the filter inline instead of on a QThread."""

    def __init__(self, tracks, text, *_args):
        self._tracks = tracks
        self._text = text
        self.finished = _SignalRedisplay()

    def isRunning(self):
        return False

    def start(self):
        self.finished.emit([t for t in self._tracks if self._text in t.track_name.lower()])


class _HostRedisplay(TrackViewDataMixin, TrackViewSearchMixin):
    def __init__(self, search_text):
        self.search_bar = _SearchBarRedisplay(search_text)
        self.model = _ModelRedisplay()
        self.status_label = _LabelRedisplay()
        self._filter_worker = None
        self._search_field_name = None
        self._loaded_count = 0

    def _build_lookup_caches(self):
        pass

    def _append_next_batch(self, source_list):
        self.model.rows = list(source_list)
        self._loaded_count = len(source_list)

    def _update_status(self):
        self.status_label.text = f"{len(self.model.rows)} matches"

    def _get_artist_name(self, *_args):
        return ""

    def _format_value(self, *_args):
        return ""

    def _field_value(self, *_args):
        return ""


@pytest.fixture(autouse=True)
def _sync_filter_redisplay(monkeypatch):
    monkeypatch.setattr(search_mod, "FilterWorker", _SyncFilterWorkerRedisplay)


def test_load_data_after_delete_keeps_search_results():
    host = _HostRedisplay("blue")
    remaining = [_TrackRedisplay(1, "Blue Monday"), _TrackRedisplay(3, "Red Rain"), _TrackRedisplay(4, "Kind of Blue")]

    host.load_data(remaining)

    assert host._filter_active is True
    assert [t.track_id for t in host.model.rows] == [1, 4]


def test_load_data_without_search_shows_all_tracks():
    host = _HostRedisplay("")
    tracks = [_TrackRedisplay(1, "Blue Monday"), _TrackRedisplay(3, "Red Rain")]

    host.load_data(tracks)

    assert host._filter_active is False
    assert [t.track_id for t in host.model.rows] == [1, 3]


class _PendingFilterWorker(_SyncFilterWorkerRedisplay):
    """Starts but never finishes until the test calls finish()."""

    def start(self):
        pass

    def finish(self):
        super().start()


def test_status_shows_searching_until_results_arrive(monkeypatch):
    """Regression: a running search gave no visual sign it was in progress."""
    monkeypatch.setattr(search_mod, "FilterWorker", _PendingFilterWorker)
    host = _HostRedisplay("blue")
    host._all_tracks = [_TrackRedisplay(1, "Blue Monday"), _TrackRedisplay(3, "Red Rain")]

    host._apply_search_filter()
    assert host.status_label.text == "Searching…"

    host._filter_worker.finish()
    assert host.status_label.text == "1 matches"


def test_cancelled_filter_worker_emits_nothing():
    """A cancelled search must not deliver stale partial results."""
    from src.track.view.track_view_filter import FilterWorker

    worker = FilterWorker([_TrackRedisplay(1, "Blue Monday")], "blue", "track_name", str, lambda *_: "", lambda *_: "")
    emitted = []
    worker.finished.connect(emitted.append)
    worker.request_cancel()

    worker.run()

    assert emitted == []


# ---------------------------------------------------------------------------
# Regression: TrackLookupCacheWorker must release its background thread's
# ---------------------------------------------------------------------------


class _ControllerRelease:
    def __init__(self, session_factory):
        self.get = GetFromDB(session_factory)
        self.SessionFactory = session_factory


@pytest.fixture
def scoped_factory(tmp_path, monkeypatch):
    """A scoped_session over a real file DB (QueuePool, like production), wired
    in as src.db.db_engine.Session so the worker's `from src.db.db_engine
    import Session` in its finally block resolves to it."""
    engine = create_engine(f"sqlite:///{tmp_path / 'lib.db'}")
    Base.metadata.create_all(engine)
    factory = scoped_session(sessionmaker(bind=engine, expire_on_commit=False))

    seed = factory()
    role = Role(role_id=1, role_name="Primary Artist")
    album = Album(album_id=1, album_name="A")
    disc = Disc(disc_id=1, album_id=1, disc_number=1)
    track = Track(track_id=1, track_name="t", album_id=1, disc_id=1, needs_tag_write=False)
    artist = Artist(artist_id=1, artist_name="Somebody")
    seed.add_all([role, album, disc, track, artist])
    seed.add(TrackArtistRole(track_id=1, artist_id=1, role_id=1))
    seed.commit()
    factory.remove()

    monkeypatch.setattr("src.db.db_engine.Session", factory, raising=False)
    yield factory
    factory.remove()
    engine.dispose()


def _run_on_thread(qapp, worker):
    thread = QThread()
    worker.moveToThread(thread)
    done = {}
    thread.started.connect(worker.run)
    worker.finished.connect(lambda *a: done.setdefault("ok", True))
    worker.error.connect(lambda msg: done.setdefault("err", msg))
    worker.finished.connect(thread.quit)
    worker.error.connect(thread.quit)
    thread.start()

    deadline = 5000
    while not thread.isFinished() and deadline > 0:
        qapp.processEvents()
        thread.wait(50)
        deadline -= 50
    assert thread.isFinished(), "worker thread did not finish"
    thread.deleteLater()
    return done


def test_worker_releases_thread_local_session(qapp, scoped_factory, monkeypatch):
    real_remove = scoped_factory.remove
    calls = {}

    def spy_remove():
        calls["thread"] = threading.get_ident()
        calls["n"] = calls.get("n", 0) + 1
        return real_remove()

    monkeypatch.setattr(scoped_factory, "remove", spy_remove)

    worker = TrackLookupCacheWorker(_ControllerRelease(scoped_factory))
    done = _run_on_thread(qapp, worker)

    assert done.get("ok") and "err" not in done
    # run() must have released its scoped_session...
    assert calls.get("n", 0) >= 1, (
        "run() never called Session.remove() — connection leaks per revisit"
    )
    # ...on the worker's own thread (scoped_session keys by thread identity,
    # so releasing from any other thread would free the wrong session).
    assert calls["thread"] != threading.get_ident()
