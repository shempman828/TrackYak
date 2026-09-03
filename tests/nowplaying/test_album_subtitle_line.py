"""The Now Playing view shows an optional album-subtitle line.

``Album.album_subtitle`` is an existing, nullable column. When the playing
track's album carries a subtitle it gets its own parenthesised line directly
under the album name/year line; when it doesn't, that line is hidden and
consumes no space. Switching tracks (or clearing) must not leave a stale
subtitle behind.
"""

from types import SimpleNamespace

from PySide6.QtCore import QObject, Signal
import pytest

from src.nowplaying import nowplaying_view as npv
from src.nowplaying.nowplaying_view import NowPlayingView


class _FakeMediaPlayer(QObject):
    position_changed = Signal(int)


@pytest.fixture
def view(qapp):
    controller = SimpleNamespace(mediaplayer=_FakeMediaPlayer())
    v = NowPlayingView(controller)
    yield v
    v.deleteLater()


def _track(album_subtitle=None, with_album=True, **kw):
    album = None
    if with_album:
        album = SimpleNamespace(
            album_name="Kind of Blue", release_year=1959, album_subtitle=album_subtitle
        )
    base = {
        "track_name": "So What",
        "primary_artist_names": "Miles Davis",
        "album": album,
        "lyrics": None,
        "is_instrumental": None,
    }
    base.update(kw)
    return SimpleNamespace(**base)


def test_subtitle_shown_when_album_has_one(view):
    view.updateUI(_track(album_subtitle="Legacy Edition"))
    assert view._album_subtitle_lbl.isVisibleTo(view)
    assert view._album_subtitle_lbl.text() == "(Legacy Edition)"


def test_subtitle_hidden_when_album_has_none(view):
    view.updateUI(_track(album_subtitle=None))
    assert not view._album_subtitle_lbl.isVisibleTo(view)
    assert view._album_subtitle_lbl.text() == ""

    view.updateUI(_track(album_subtitle=""))
    assert not view._album_subtitle_lbl.isVisibleTo(view)


def test_stale_subtitle_cleared_on_track_switch(view):
    view.updateUI(_track(album_subtitle="Deluxe Edition"))
    assert view._album_subtitle_lbl.isVisibleTo(view)

    # Next track: album without a subtitle.
    view.updateUI(_track(album_subtitle=None))
    assert not view._album_subtitle_lbl.isVisibleTo(view)
    assert view._album_subtitle_lbl.text() == ""

    view.updateUI(_track(album_subtitle="Anniversary Edition"))
    assert view._album_subtitle_lbl.isVisibleTo(view)

    # Next track: no album at all.
    view.updateUI(_track(with_album=False))
    assert not view._album_subtitle_lbl.isVisibleTo(view)

    view.updateUI(_track(album_subtitle="Remastered"))
    view.clearUI()
    assert not view._album_subtitle_lbl.isVisibleTo(view)
    assert view._album_subtitle_lbl.text() == ""


def test_subtitle_routed_through_censor_text(view, monkeypatch):
    seen = []

    def _fake_censor(text, force=False):
        seen.append(text)
        return "CENSORED"

    monkeypatch.setattr(npv, "censor_text", _fake_censor)
    view.updateUI(_track(album_subtitle="rude words"))
    assert "rude words" in seen
    assert view._album_subtitle_lbl.text() == "(CENSORED)"
