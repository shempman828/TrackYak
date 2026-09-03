"""Instrumental tracks disable the LYRICS tab in the Now Playing panel.

Regression: ``is_instrumental`` was never consulted by the Now Playing view, so
an instrumental track showed a clickable LYRICS tab landing on an empty "No
lyrics available" pane. It must be disabled outright and the panel pinned to
CREDITS.
"""

from types import SimpleNamespace

from PySide6.QtCore import QObject, Signal
import pytest

from src.nowplaying.nowplaying_view import NowPlayingView


class _FakeMediaPlayer(QObject):
    position_changed = Signal(int)


@pytest.fixture
def view(qapp):
    controller = SimpleNamespace(mediaplayer=_FakeMediaPlayer())
    v = NowPlayingView(controller)
    yield v
    v.deleteLater()


def _track(**kw):
    base = {"track_name": "T", "lyrics": None, "is_instrumental": None}
    base.update(kw)
    return SimpleNamespace(**base)


def test_instrumental_disables_lyrics_tab_and_pins_credits(view):
    view._update_lyrics(_track(is_instrumental=1))
    assert not view._tab_lyrics.isEnabled()
    assert view._stack.currentIndex() == view._PAGE_CREDITS


def test_switch_tab_ignores_lyrics_while_disabled(view):
    view._update_lyrics(_track(is_instrumental=1))
    view._switch_tab(view._PAGE_LYRICS)
    assert view._stack.currentIndex() == view._PAGE_CREDITS


def test_instrumental_ignores_stale_lyrics_string(view):
    view._update_lyrics(_track(is_instrumental=1, lyrics="[00:01.00]ghost line"))
    assert not view._tab_lyrics.isEnabled()
    assert view._stack.currentIndex() == view._PAGE_CREDITS


def test_non_instrumental_track_re_enables_lyrics_tab(view):
    view._update_lyrics(_track(is_instrumental=1))
    assert not view._tab_lyrics.isEnabled()

    view._update_lyrics(_track(is_instrumental=0, lyrics="plain words"))
    assert view._tab_lyrics.isEnabled()
    assert view._stack.currentIndex() == view._PAGE_LYRICS
