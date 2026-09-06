"""The Now Playing title line pans long titles instead of wrapping.

The track title uses the same ``MarqueeLabel`` as the artist line directly
below it, so an over-long title scrolls horizontally rather than word-wrapping
onto a second line. Text updates go through ``set_text`` and are routed through
``censor_text``; clearing resets the placeholder.
"""

from types import SimpleNamespace

from PySide6.QtCore import QObject, Signal
import pytest

from src.nowplaying import nowplaying_view as npv
from src.nowplaying.nowplaying_marquee import MarqueeLabel
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
    base = {
        "track_name": "So What",
        "primary_artist_names": "Miles Davis",
        "album": None,
        "lyrics": None,
        "is_instrumental": None,
    }
    base.update(kw)
    return SimpleNamespace(**base)


def test_title_uses_same_marquee_widget_as_artist(view):
    assert isinstance(view._title_lbl, MarqueeLabel)
    assert isinstance(view._artist_marquee, MarqueeLabel)


def test_title_text_updates_via_set_text(view):
    view.updateUI(_track(track_name="A Love Supreme, Pt. I - Acknowledgement"))
    assert view._title_lbl._text == "A Love Supreme, Pt. I - Acknowledgement"


def test_title_falls_back_when_track_name_missing(view):
    view.updateUI(_track(track_name=None))
    assert view._title_lbl._text == "Unknown Title"


def test_title_reset_on_clear(view):
    view.updateUI(_track(track_name="So What"))
    view.clearUI()
    assert view._title_lbl._text == "No Track Playing"


def test_title_routed_through_censor_text(view, monkeypatch):
    seen = []

    def _fake_censor(text, force=False):
        seen.append(text)
        return "CENSORED"

    monkeypatch.setattr(npv, "censor_text", _fake_censor)
    view.updateUI(_track(track_name="rude words"))
    assert "rude words" in seen
    assert view._title_lbl._text == "CENSORED"
