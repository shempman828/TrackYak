"""Player dock: waveform wiring, the Ctrl+Shift+L lyrics shortcut, and mood
auto-tagging from the context menu.
"""

import json
from types import SimpleNamespace

import numpy as np
from PySide6.QtCore import Signal
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QLabel, QWidget
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.db_helpers.add import AddToDB
from src.db.db_helpers.get import GetFromDB
from src.db.db_helpers.update import UpdateDB
from src.db.db_tables.base import Base
from src.db.db_tables.mood import Mood, MoodTrackAssociation
from src.db.db_tables.track import Track
from src.foundation.status_utility import StatusManager
from src.lyrics.autotag import mood_scoring
from src.player.core.waveform_cache import N_BUCKETS
from src.player.ui.player_context_menu import PlayerContextMenuMixin
from src.player.ui.player_dock import PlayerUI
from src.player.ui.waveform_seekbar import WaveformSeekBar

# ---------------------------------------------------------------------------
# Coverage for the Player Dock's waveform wiring — see docs/specs/waveform-scrubber.md.
# ---------------------------------------------------------------------------


class _DockHarness(QWidget):
    """Real PlayerUI waveform methods on a stripped-down host."""

    _start_waveform_load = PlayerUI._start_waveform_load
    _on_waveform_ready = PlayerUI._on_waveform_ready
    _on_waveform_failed = PlayerUI._on_waveform_failed
    update_duration = PlayerUI.update_duration
    update_position = PlayerUI.update_position
    _on_seek_pressed = PlayerUI._on_seek_pressed
    _on_seek_released = PlayerUI._on_seek_released
    format_time = staticmethod(PlayerUI.format_time)

    seek_requested = Signal(int)

    def __init__(self, player):
        super().__init__()
        self.player = player
        self.controller = SimpleNamespace(mediaplayer=player)
        self._wf_generation = 0
        self.waveform = WaveformSeekBar()
        self.waveform.setGeometry(0, 0, 400, 40)
        self.position_label = QLabel()


@pytest.fixture
def _no_threadpool(monkeypatch):
    """Capture scheduled workers instead of running them."""
    started = []
    monkeypatch.setattr(
        "src.player.ui.player_dock.QThreadPool",
        SimpleNamespace(globalInstance=lambda: SimpleNamespace(start=started.append)),
    )
    return started


def test_track_change_shows_fallback_and_schedules_one_worker(qapp, tmp_path, _no_threadpool):
    src = tmp_path / "song.flac"
    player = SimpleNamespace(duration=120_000, _resolved_file_path=src, current_file=src)
    h = _DockHarness(player)
    h.waveform.set_peaks(np.zeros((N_BUCKETS, 2), dtype=np.int8))  # stale envelope

    h._start_waveform_load(src)

    assert h.waveform._peaks is None  # fallback shown immediately
    assert h._wf_generation == 1
    assert len(_no_threadpool) == 1
    assert _no_threadpool[0].generation == 1
    assert _no_threadpool[0].src == src


def test_stale_worker_result_is_ignored_current_one_applies(qapp, tmp_path, _no_threadpool):
    src = tmp_path / "song.flac"
    player = SimpleNamespace(duration=120_000, _resolved_file_path=src, current_file=src)
    h = _DockHarness(player)
    h._start_waveform_load(src)

    stale = np.zeros((N_BUCKETS, 2), dtype=np.int8)
    h._on_waveform_ready(0, src, stale)
    assert h.waveform._peaks is None

    fresh = np.zeros((N_BUCKETS, 2), dtype=np.int8)
    fresh[:, 1] = 60
    h._on_waveform_ready(h._wf_generation, src, fresh)
    assert h.waveform._peaks is not None


def test_failed_worker_leaves_fallback_in_place(qapp, tmp_path, _no_threadpool):
    src = tmp_path / "song.flac"
    player = SimpleNamespace(duration=1000, _resolved_file_path=src, current_file=src)
    h = _DockHarness(player)
    h._start_waveform_load(src)

    h._on_waveform_failed(h._wf_generation, src, "boom")  # must not raise
    assert h.waveform._peaks is None


def test_update_duration_toggles_and_ranges_the_bar(qapp):
    player = SimpleNamespace(duration=0, position=0, current_file=None)
    h = _DockHarness(player)

    h.update_duration(0)
    assert not h.waveform.isEnabled()

    h.update_duration(60_000)
    assert h.waveform.isEnabled()
    assert h.waveform._duration_ms == 60_000


def test_update_position_moves_playhead_except_while_dragging(qapp):
    player = SimpleNamespace(duration=60_000, position=0, current_file=None)
    h = _DockHarness(player)
    h.update_duration(60_000)

    h.update_position(30_000)
    assert h.waveform._position_ms == 30_000

    h.waveform._dragging = True
    h.update_position(45_000)
    assert h.waveform._position_ms == 30_000


def test_seek_released_gates_on_same_track_and_nonzero_duration(qapp, tmp_path):
    a, b = tmp_path / "a.flac", tmp_path / "b.flac"
    player = SimpleNamespace(duration=60_000, position=0, current_file=a)
    h = _DockHarness(player)
    emitted = []
    h.seek_requested.connect(emitted.append)

    h._on_seek_pressed()
    h._on_seek_released(12_345)
    assert emitted == [12_345]

    # track changed since press → drop it
    player.current_file = b
    h._on_seek_released(9_999)
    assert emitted == [12_345]

    # duration went to zero → drop it
    h._on_seek_pressed()
    player.duration = 0
    h._on_seek_released(1)
    assert emitted == [12_345]


# ---------------------------------------------------------------------------
# Tests for the player dock's Ctrl+Shift+L lyrics-search keyboard shortcut.
# ---------------------------------------------------------------------------


class _FakeLyricThread:
    def __init__(self):
        self.is_running = False
        self.searches = []

    def search(self, track):
        self.searches.append(track)


class _ShortcutHarness(PlayerContextMenuMixin, QWidget):
    """Minimal PlayerUI stand-in: real shortcut setup + real lyrics handler."""

    setup_keyboard_shortcuts = PlayerUI.setup_keyboard_shortcuts

    def __init__(self, current_track):
        super().__init__()
        mediaplayer = SimpleNamespace(
            toggle_play_pause=lambda: None,
            stop=lambda: None,
            play_next=lambda: None,
            play_previous=lambda: None,
            increase_volume=lambda: None,
            decrease_volume=lambda: None,
            seek_forward=lambda: None,
            seek_backward=lambda: None,
        )
        self.controller = SimpleNamespace(mediaplayer=mediaplayer)
        self.current_track = current_track
        self.parent_window = None
        self._lyric_search_track = None
        self._lyric_thread = _FakeLyricThread()
        self.setup_keyboard_shortcuts()

    def _adjust_rating(self, _delta):
        pass

    def _toggle_mute(self):
        pass


@pytest.fixture
def _quiet_status(monkeypatch):
    messages = []
    monkeypatch.setattr(StatusManager, 'show_message', lambda msg, duration=0: messages.append(msg))
    return messages


def test_shortcut_is_bound_to_ctrl_shift_l(qapp):
    harness = _ShortcutHarness(current_track=object())
    assert harness.lyrics_search_shortcut.key() == QKeySequence('Ctrl+Shift+L')


# AC1 -------------------------------------------------------------------------
def test_shortcut_starts_lyrics_search_for_current_track(qapp, _quiet_status):
    track = object()
    harness = _ShortcutHarness(current_track=track)

    harness.lyrics_search_shortcut.activated.emit()

    assert harness._lyric_thread.searches == [track]
    assert 'Searching for lyrics…' in _quiet_status


# AC2 -------------------------------------------------------------------------
def test_shortcut_noop_while_search_already_running(qapp, _quiet_status):
    harness = _ShortcutHarness(current_track=object())
    harness._lyric_thread.is_running = True

    harness.lyrics_search_shortcut.activated.emit()

    assert harness._lyric_thread.searches == []


# AC3 -------------------------------------------------------------------------
def test_shortcut_noop_with_no_current_track(qapp, _quiet_status):
    harness = _ShortcutHarness(current_track=None)

    harness.lyrics_search_shortcut.activated.emit()

    assert harness._lyric_thread.searches == []
    assert _quiet_status == []


# ---------------------------------------------------------------------------
# Tests for mood auto-tagging wired into the player dock's context-menu
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolated_keywords(tmp_path, monkeypatch):
    keywords_path = tmp_path / "mood_keywords.json"
    keywords_path.write_text(json.dumps({"Happy": ["happy", "sunshine", "joyful"]}))
    monkeypatch.setattr(mood_scoring, "_KEYWORDS_PATH", keywords_path)
    mood_scoring._cache["mtime"] = None
    mood_scoring._cache["keyword_patterns"] = None

    monkeypatch.setattr(mood_scoring, "_OPPOSITES_PATH", tmp_path / "no_opposites.json")
    mood_scoring._opposites_cache["mtime"] = None
    mood_scoring._opposites_cache["pairs"] = None
    yield
    mood_scoring._cache["mtime"] = None
    mood_scoring._cache["keyword_patterns"] = None
    mood_scoring._opposites_cache["mtime"] = None
    mood_scoring._opposites_cache["pairs"] = None


class StubController:
    def __init__(self, session):
        self.get = GetFromDB(session)
        self.add = AddToDB(session)
        self.update = UpdateDB(session)


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    yield s
    s.close()


@pytest.fixture
def controller(session):
    return StubController(session)


@pytest.fixture(autouse=True)
def _seed_happy_mood(session):
    session.add(Mood(mood_name="Happy"))
    session.commit()


def _make_track(session, **overrides):
    track = Track(track_name="Test Track")
    for field, value in overrides.items():
        setattr(track, field, value)
    session.add(track)
    session.commit()
    return track


def _mood_association_exists(session, track_id):
    happy = session.query(Mood).filter_by(mood_name="Happy").one()
    return (
        session.query(MoodTrackAssociation)
        .filter_by(mood_id=happy.mood_id, track_id=track_id)
        .one_or_none()
        is not None
    )


class _DockHarnessAutotag(PlayerContextMenuMixin, QWidget):
    """Minimal stand-in for PlayerUI exposing only what the mixin's lyrics
    callbacks need, so tests don't have to construct the full player dock
    (media player, timers, layouts, etc)."""

    def __init__(self, controller, current_track):
        super().__init__()
        self.controller = controller
        self.current_track = current_track
        self.parent_window = None
        self._lyric_search_track = current_track


def _make_dock(controller, track):
    return _DockHarnessAutotag(controller, track)


# AC1/AC2 ----------------------------------------------------------------
def test_context_menu_lyrics_save_adds_mood_association(qapp, session, controller):
    track = _make_track(session)
    dock = _make_dock(controller, track)

    dock._on_lyrics_ready("happy happy happy sunshine joyful morning vibes")

    assert _mood_association_exists(session, track.track_id)


def test_context_menu_lyrics_save_reports_tagged_moods(qapp, session, controller, monkeypatch):
    track = _make_track(session)
    dock = _make_dock(controller, track)

    messages = []
    monkeypatch.setattr(
        StatusManager, "show_message", lambda msg, duration=0: messages.append(msg)
    )

    dock._on_lyrics_ready("happy happy happy sunshine joyful morning vibes")

    assert any("Tagged mood(s): Happy" in m for m in messages)


def test_context_menu_lyrics_save_with_no_mood_match_reports_plain_message(
    qapp, session, controller, monkeypatch
):
    track = _make_track(session)
    dock = _make_dock(controller, track)

    messages = []
    monkeypatch.setattr(
        StatusManager, "show_message", lambda msg, duration=0: messages.append(msg)
    )

    dock._on_lyrics_ready("a perfectly unrelated clean lyric line")

    assert not _mood_association_exists(session, track.track_id)
    assert messages == ["Lyrics found and saved."]


# AC4 ----------------------------------------------------------------------
def test_context_menu_mood_autotag_failure_does_not_block_lyrics_save(
    qapp, session, controller, monkeypatch
):
    track = _make_track(session)
    dock = _make_dock(controller, track)

    # Break the auto-tag context build (e.g. a DB read error while loading
    # Mood/Place lookups) -- this is caught inside auto_tag_lyrics_safe's
    # own broad except, so the lyrics save that already happened above it
    # in _on_lyrics_ready must be unaffected.
    monkeypatch.setattr(
        "src.lyrics.autotag.mood_autotag.build_autotag_context",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    dock._on_lyrics_ready("happy happy happy sunshine joyful morning vibes")

    session.refresh(track)
    assert track.lyrics == "happy happy happy sunshine joyful morning vibes"
    assert not _mood_association_exists(session, track.track_id)
