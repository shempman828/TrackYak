"""The lyric-sync toggle button (⏱) in the Now Playing tab bar.

Regression: the button was pinned to a 24x24 square while the shared
``QPushButton[npToggle="true"]`` stylesheet reserves 8px of horizontal padding
per side plus a 1px border, leaving only ~6px for the glyph — so the stopwatch
icon rendered clipped.
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


def test_sync_toggle_height_is_fixed(view):
    """Height stays pinned so the tab-bar row height is stable."""
    btn = view._sync_toggle_btn
    assert btn.minimumHeight() == btn.maximumHeight() == 24


def test_sync_toggle_width_is_not_pinned_below_its_hint(view):
    """Width must be free to grow to the glyph + QSS padding + border.

    With the old ``setFixedSize(24, 24)`` the maximum width was 24 while the
    size hint (glyph + 2*8px padding + 2*1px border) is larger, forcing a clip.
    """
    btn = view._sync_toggle_btn
    assert btn.sizeHint().width() <= btn.maximumWidth()
    assert btn.sizeHint().width() >= 24
