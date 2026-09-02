"""Tests for the player dock's Ctrl+Shift+L lyrics-search keyboard shortcut.

The shortcut just binds to the existing PlayerContextMenuMixin
``_context_search_lyrics`` handler, so these exercise the real
``setup_keyboard_shortcuts`` wiring plus that handler's guards without
constructing the full PlayerUI (media player, layouts, timers).
"""

from types import SimpleNamespace

from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QWidget
import pytest

from src.foundation.status_utility import StatusManager
from src.player.player_context_menu import PlayerContextMenuMixin
from src.player.player_dock import PlayerUI


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
