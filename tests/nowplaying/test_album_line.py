"""The Now Playing view shows the album as one line: "Album · Subtitle · Year".

``Album.album_subtitle`` and ``release_year`` are optional; missing parts are
left out with their separator. Switching tracks (or clearing) must not leave
a stale subtitle behind.
"""

from types import SimpleNamespace

from PySide6.QtCore import QObject, Signal
import pytest

from src.nowplaying import nowplaying_view as npv
from src.nowplaying.nowplaying_view import NowPlayingView

_SEP = "  ·  "


class _FakeMediaPlayer(QObject):
    position_changed = Signal(int)


@pytest.fixture
def view(qapp):
    controller = SimpleNamespace(mediaplayer=_FakeMediaPlayer())
    v = NowPlayingView(controller)
    yield v
    v.deleteLater()


def _track(album_subtitle=None, with_album=True, year=1959, **kw):
    album = None
    if with_album:
        album = SimpleNamespace(
            album_name="Kind of Blue", release_year=year, album_subtitle=album_subtitle
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


def test_full_line_with_subtitle_and_year(view):
    view.updateUI(_track(album_subtitle="Legacy Edition"))
    assert view._album_lbl.text() == f"Kind of Blue{_SEP}Legacy Edition{_SEP}1959"


def test_missing_parts_are_left_out(view):
    view.updateUI(_track(album_subtitle=None))
    assert view._album_lbl.text() == f"Kind of Blue{_SEP}1959"

    view.updateUI(_track(album_subtitle="  ", year=None))
    assert view._album_lbl.text() == "Kind of Blue"


def test_stale_subtitle_cleared_on_track_switch(view):
    view.updateUI(_track(album_subtitle="Deluxe Edition"))
    assert "Deluxe Edition" in view._album_lbl.text()

    view.updateUI(_track(album_subtitle=None))
    assert "Deluxe Edition" not in view._album_lbl.text()

    view.updateUI(_track(with_album=False))
    assert view._album_lbl.text() == "—"

    view.updateUI(_track(album_subtitle="Remastered"))
    view.clearUI()
    assert view._album_lbl.text() == "—"


def test_name_and_subtitle_routed_through_censor_text(view, monkeypatch):
    seen = []

    def _fake_censor(text, force=False):
        seen.append(text)
        return "CENSORED"

    monkeypatch.setattr(npv, "censor_text", _fake_censor)
    view.updateUI(_track(album_subtitle="rude words"))
    assert "rude words" in seen
    assert "Kind of Blue" in seen
    assert view._album_lbl.text() == f"CENSORED{_SEP}CENSORED{_SEP}1959"
