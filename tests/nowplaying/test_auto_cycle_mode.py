"""Auto-cycle mode for the Now Playing view (Ctrl+Shift++).

When enabled, a timer rotates the right-hand tab stack through its enabled
tabs, wrapping past the last one. Toggling it off just stops the rotation.
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


def test_toggle_starts_and_stops_the_cycle_timer(view):
    assert not view.auto_cycle
    assert not view._auto_cycle_timer.isActive()

    view.toggle_auto_cycle()
    assert view.auto_cycle
    assert view._auto_cycle_timer.isActive()

    view.toggle_auto_cycle()
    assert not view.auto_cycle
    assert not view._auto_cycle_timer.isActive()


def test_advance_moves_to_next_tab_and_wraps(view):
    view._switch_tab(view._PAGE_LYRICS)

    view._advance_auto_cycle()
    assert view._stack.currentIndex() == view._PAGE_CREDITS

    view._advance_auto_cycle()
    assert view._stack.currentIndex() == view._PAGE_ABOUT

    view._advance_auto_cycle()
    assert view._stack.currentIndex() == view._PAGE_LYRICS


def test_advance_skips_disabled_tabs(view):
    view._update_lyrics(_track(is_instrumental=1))
    assert not view._tab_lyrics.isEnabled()
    assert view._stack.currentIndex() == view._PAGE_CREDITS

    view._advance_auto_cycle()
    assert view._stack.currentIndex() == view._PAGE_ABOUT

    # LYRICS is disabled, so ABOUT wraps straight back to CREDITS.
    view._advance_auto_cycle()
    assert view._stack.currentIndex() == view._PAGE_CREDITS


def test_advance_is_noop_with_only_one_enabled_tab(view):
    view._update_lyrics(_track(is_instrumental=1))
    view._tabs[view._PAGE_ABOUT].button.setEnabled(False)
    assert view._stack.currentIndex() == view._PAGE_CREDITS

    view._advance_auto_cycle()
    assert view._stack.currentIndex() == view._PAGE_CREDITS


def test_toggling_off_leaves_current_tab_in_place(view):
    view.toggle_auto_cycle()
    view._advance_auto_cycle()
    landed = view._stack.currentIndex()
    assert landed != view._PAGE_LYRICS

    view.toggle_auto_cycle()
    assert view._stack.currentIndex() == landed
