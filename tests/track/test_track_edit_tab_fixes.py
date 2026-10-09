"""Regression tests for the 2026-10-09 src/track finalize fixes in the track edit tabs."""

from types import SimpleNamespace

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.common.widgets.entity_completer_edit import invalidate_entity_cache
from src.db.db_helpers.get import GetFromDB
from src.db.db_tables.base import Base
from src.db.db_tables.track import Track
from src.track.edit.track_edit_advanced import AdvancedTab
from src.track.edit.track_edit_album import AlbumsTab
from src.track.edit.track_edit_genres import GenresTab
from src.track.edit.track_edit_lyrics import LyricsTab
from src.track.edit.track_edit_roles import RolesTab
from src.track.edit.track_edit_samples import SamplesTab, _AddSampleBar
from src.track.edit.track_edit_usedin import UsedInTab


@pytest.fixture(autouse=True)
def _clean_cache(qapp):
    invalidate_entity_cache()
    yield
    qapp.processEvents()
    invalidate_entity_cache()


class _Get:
    """Enough of GetFromDB for the search widgets: empty tables."""

    def __init__(self):
        self.session = SimpleNamespace(expire=lambda *a, **k: None)

    def count_entities(self, model_name):
        return 0

    def get_all_entities(self, model_name, **kwargs):
        return []

    def get_entity_object(self, model_name, **kwargs):
        return None


class _Delete:
    def __init__(self, result=True):
        self.result = result
        self.calls = []

    def delete_entity(self, model_name, **kwargs):
        self.calls.append((model_name, kwargs))
        return self.result


def _controller(**parts):
    return SimpleNamespace(get=parts.get("get", _Get()), delete=parts.get("delete", _Delete()), add=parts.get("add"))


# ── Roles ────────────────────────────────────────────────────────────────────


def test_roles_remove_all_uses_one_filtered_delete(qapp):
    controller = _controller()
    tab = RolesTab([SimpleNamespace(track_id=1), SimpleNamespace(track_id=2)], controller)
    tab.load = lambda tracks: None

    tab._remove_all_roles_for_artist(7)

    assert controller.delete.calls == [("TrackArtistRole", {"track_id": [1, 2], "artist_id": 7})]


def test_roles_reload_requested_during_load_runs_afterwards(qapp):
    tab = RolesTab([SimpleNamespace(track_id=1)], _controller())
    reloads = []
    tab.load = lambda tracks: reloads.append(tracks)

    tab._reload_pending = True
    tab._run_pending_reload()
    assert len(reloads) == 1

    tab.cleanup()
    tab._reload_pending = True
    tab._run_pending_reload()
    assert len(reloads) == 1  # no reload after cleanup


def test_roles_remove_failure_warns(qapp, monkeypatch):
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warnings.append(a))
    tab = RolesTab([SimpleNamespace(track_id=1)], _controller(delete=_Delete(result=False)))
    tab.load = lambda tracks: None
    tab._remove_role(7, 3)
    assert warnings


# ── Samples ──────────────────────────────────────────────────────────────────


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine, expire_on_commit=False)()
    yield s
    s.close()


def test_sample_search_first_result_can_be_picked(qapp, session):
    t = Track(track_name="Amen Brother")
    session.add(t)
    session.commit()
    bar = _AddSampleBar(SimpleNamespace(get=GetFromDB(session)), on_add=lambda **k: None)

    bar.name_search.setText("Amen")
    assert bar.name_combo.count() == 1
    bar.name_combo.activated.emit(0)  # the user picks the (already current) first row

    assert bar._matched_id == t.track_id


def test_sample_add_uses_exact_name_match_without_a_pick(qapp, session):
    t = Track(track_name="Amen Brother")
    session.add(t)
    session.commit()
    added = []
    bar = _AddSampleBar(SimpleNamespace(get=GetFromDB(session)), on_add=lambda **k: added.append(k))

    bar.name_search.setText("Amen Brother")
    bar._handle_add()

    assert added == [{"direction": "uses", "matched_track_id": t.track_id}]


def test_samples_multi_placeholder_is_not_selectable(qapp):
    tracks = [SimpleNamespace(track_id=1, track_name="A"), SimpleNamespace(track_id=2, track_name="B")]
    tab = SamplesTab(tracks, _controller())
    tab.load(tracks)
    assert tab._used_list.item(0).flags() == Qt.NoItemFlags


# ── Advanced ─────────────────────────────────────────────────────────────────


class _Signal:
    def __init__(self):
        self.disconnected = []

    def disconnect(self, slot):
        self.disconnected.append(slot)


class _Scheduler:
    def __init__(self):
        self.signals = SimpleNamespace(track_done=_Signal(), batch_done=_Signal(), all_done=_Signal(), error=_Signal())
        self.is_running = True
        self.stopped = False

    def stop(self):
        self.stopped = True


def test_advanced_cleanup_stops_running_analysis(qapp):
    tab = AdvancedTab.__new__(AdvancedTab)
    scheduler = _Scheduler()
    tab._scheduler = scheduler

    AdvancedTab.cleanup(tab)

    assert scheduler.stopped
    assert all(sig.disconnected for sig in vars(scheduler.signals).values())
    assert tab._scheduler is None


# ── Genres / Moods ───────────────────────────────────────────────────────────


def test_tag_tab_remove_button_removes_selected(qapp):
    tracks = [SimpleNamespace(track_id=1, genres=[SimpleNamespace(genre_id=5, genre_name="Jazz")])]
    controller = _controller()
    tab = GenresTab(tracks, controller)
    tab.load(tracks)
    assert not tab._remove_btn.isEnabled()

    tab._list.item(0).setSelected(True)
    assert tab._remove_btn.isEnabled()
    tab._remove_btn.click()

    assert controller.delete.calls == [("TrackGenre", {"track_id": [1], "genre_id": 5})]


# ── Lyrics ───────────────────────────────────────────────────────────────────


def test_lyrics_multi_mixed_explicit_shows_partial_state(qapp):
    tracks = [SimpleNamespace(track_id=1, is_explicit=True, lyrics=None), SimpleNamespace(track_id=2, is_explicit=False, lyrics=None)]
    tab = LyricsTab(tracks, _controller())
    tab.load(tracks)
    assert tab._explicit_widget.checkState() == Qt.PartiallyChecked
    tab.cleanup()


def test_lyrics_search_uses_live_title(qapp):
    track = SimpleNamespace(track_id=1, track_name="Saved", album_name=None, duration=200, primary_artist_names="A", artist_roles=[], is_explicit=None, lyrics=None)
    tab = LyricsTab([track], _controller(), dialog=SimpleNamespace(get_live_track_name=lambda: "Typed"))
    queries = []
    tab._lyric_thread.search = queries.append

    tab._search_lyrics()

    assert queries[0].song == "Typed"
    tab.cleanup()


# ── Album ────────────────────────────────────────────────────────────────────


def test_album_create_from_mb_failure_does_not_crash(qapp, monkeypatch):
    warnings = []
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warnings.append(a))
    tab = AlbumsTab.__new__(AlbumsTab)
    tab.controller = SimpleNamespace(add=SimpleNamespace(add_entity=lambda *a, **k: None))

    assert tab._create_album_from_mb({"album_name": "X", "release_group_mbid": "rg"}, []) is None
    assert warnings


# ── Used In ──────────────────────────────────────────────────────────────────


def _usage(uid, description):
    return SimpleNamespace(usage_id=uid, usage_type="Film", title="X", year=2000, description=description, wikipedia_link=None)


def test_used_in_multi_remove_matches_the_whole_entry(qapp):
    tracks = [SimpleNamespace(track_id=1, usages=[_usage(1, "a"), _usage(3, "b")]), SimpleNamespace(track_id=2, usages=[_usage(2, "a")])]
    controller = _controller()
    tab = UsedInTab(tracks, controller)
    tab.load(tracks)
    assert tab._table.rowCount() == 1

    tab._remove_row(0)

    assert controller.delete.calls == [("TrackUsage", {"entity_ids": [1, 2]})]
