"""The lyric toolbar under the lyric column: ALL LINES, offset stepper, SYNC.

Regression kept from the old ⏱ tab-bar toggle: the [npToggle] stylesheet
reserves horizontal padding, so these pills must pin only their height and
let the width follow the size hint, or the glyph clips.
"""

from types import SimpleNamespace

from PySide6.QtCore import QObject, Signal
import pytest

from src.nowplaying import nowplaying_lyrics
from src.nowplaying.nowplaying_view import NowPlayingView

_SYNCED_LYRICS = "[00:02.00] one\n[00:04.00] two"


class _FakeMediaPlayer(QObject):
    position_changed = Signal(int)


@pytest.fixture
def view(qapp, monkeypatch):
    # Never write the user's real config from a test.
    monkeypatch.setattr(nowplaying_lyrics.app_config, "set_lyrics_sync_offset", lambda v: None)
    monkeypatch.setattr(nowplaying_lyrics.app_config, "save", lambda: None)
    controller = SimpleNamespace(mediaplayer=_FakeMediaPlayer())
    v = NowPlayingView(controller)
    v._on_offset_changed(0)
    yield v
    v._offset_save_timer.stop()
    v.deleteLater()


def _pills(view):
    return [
        view._toggle_mode_btn,
        view._offset_minus_btn,
        view._offset_value_btn,
        view._offset_plus_btn,
        view._manual_sync_btn,
    ]


def test_pill_heights_are_fixed(view):
    for btn in _pills(view):
        assert btn.minimumHeight() == btn.maximumHeight() == 24


def test_pill_widths_are_not_pinned_below_their_hint(view):
    for btn in _pills(view):
        assert btn.sizeHint().width() <= btn.maximumWidth()


def test_toolbar_hidden_without_lyrics(view):
    view._update_lyrics(SimpleNamespace(lyrics=None))
    assert not view._lyrics_toolbar.isVisibleTo(view)


def test_synced_lyrics_show_the_whole_toolbar(view):
    view._update_lyrics(SimpleNamespace(lyrics=_SYNCED_LYRICS))
    assert view._lyrics_toolbar.isVisibleTo(view)
    assert view._toggle_mode_btn.isVisibleTo(view)
    assert view._offset_group.isVisibleTo(view)
    assert view._manual_sync_btn.isEnabled()


def test_plain_lyrics_hide_follow_and_offset(view):
    view._update_lyrics(SimpleNamespace(lyrics="just\nplain"))
    assert view._lyrics_toolbar.isVisibleTo(view)
    assert not view._toggle_mode_btn.isVisibleTo(view)
    assert not view._offset_group.isVisibleTo(view)
    assert view._manual_sync_btn.isEnabled()


def test_stepper_nudges_by_a_tenth_of_a_second(view):
    view._offset_plus_btn.click()
    view._offset_plus_btn.click()
    assert view._sync_offset_ms == 200
    assert view._offset_value_btn.text() == "⏱ +0.2s"
    assert view._offset_value_btn.property("active") is True

    view._offset_minus_btn.click()
    view._offset_minus_btn.click()
    view._offset_minus_btn.click()
    assert view._sync_offset_ms == -100
    assert view._offset_value_btn.text() == "⏱ −0.1s"  # noqa: RUF001 (U+2212 minus glyph)


def test_value_button_resets_to_zero(view):
    view._on_offset_changed(13)
    view._offset_value_btn.click()
    assert view._sync_offset_ms == 0
    assert view._offset_value_btn.text() == "⏱ 0.0s"
    assert view._offset_value_btn.property("active") is False


def test_offset_is_clamped_to_five_seconds(view):
    view._on_offset_changed(49)
    view._offset_plus_btn.click()
    view._offset_plus_btn.click()
    assert view._sync_offset_ms == 5000
    view._on_offset_changed(-999)
    assert view._sync_offset_ms == -5000


def test_offset_change_debounces_the_config_save(view):
    view._offset_plus_btn.click()
    assert view._offset_save_timer.isActive()
