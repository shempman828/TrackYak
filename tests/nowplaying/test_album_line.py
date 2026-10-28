"""Now Playing album/artist line and its duration/year chip row."""

from types import SimpleNamespace

from PySide6.QtCore import QObject, Signal
import pytest

from src.nowplaying import nowplaying_view as npv
from src.nowplaying.nowplaying_chip import _Chip, _ScrollingChipRow
from src.nowplaying.nowplaying_view import NowPlayingView

# ---------------------------------------------------------------------------
# The Now Playing view shows the album as one line: "Album · Subtitle · Year".
# ---------------------------------------------------------------------------


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
        album = SimpleNamespace(album_name="Kind of Blue", release_year=year, album_subtitle=album_subtitle)
    base = {"track_name": "So What", "primary_artist_names": "Miles Davis", "album": album, "lyrics": None, "is_instrumental": None}
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


def test_artist_line_is_censored(view, monkeypatch):
    monkeypatch.setattr(npv, "censor_text", lambda t: t.replace("Bad", "B**") if t else t)
    view.updateUI(_track(primary_artist_names="Bad Artist"))
    assert view._artist_marquee._text == "B** Artist"


def test_duration_and_recorded_year_chips_show(view):
    view.updateUI(_track(duration=245.6, recorded_year=1959))
    assert view._chip_duration.text().endswith("4:05")
    assert view._chip_rec_year.text().endswith("1959")
    assert view._chip_duration.isVisibleTo(view._chip_row)
    assert view._chip_rec_year.isVisibleTo(view._chip_row)


def test_missing_duration_and_year_hide_their_chips(view):
    view.updateUI(_track())
    assert not view._chip_duration.isVisibleTo(view._chip_row)
    assert not view._chip_rec_year.isVisibleTo(view._chip_row)


def test_track_change_loads_credits_once(view):
    calls = []
    view._credits_panel.load_credits = lambda t: calls.append(t)
    track = _track()
    view.updateUI(track)  # no lyrics -> CREDITS tab
    assert calls == [track]


def test_pre_cinema_queue_state_is_initialised(view):
    assert view._pre_cinema_queue_visible is False


# ---------------------------------------------------------------------------
# Chip row: chips keep their given order, pan only on screen when overflowing, and carry names for tooltips and screen readers.
# ---------------------------------------------------------------------------


@pytest.fixture
def row(qapp):
    r = _ScrollingChipRow()
    r.resize(600, 36)
    yield r
    r.deleteLater()


def _order(row):
    return [row._row.itemAt(i).widget() for i in range(row._row.count()) if not row._row.itemAt(i).widget().isHidden()]


def test_chip_order_follows_the_given_order_not_history(row):
    bpm, key = _Chip("♩", "120 BPM"), _Chip("key", "C")
    row.set_chips([key])  # first track only has a key
    row.set_chips([bpm, key])
    assert _order(row) == [bpm, key]


def test_hidden_chips_stay_alive_for_the_next_track(row):
    a, b = _Chip("a", "1"), _Chip("b", "2")
    row.set_chips([a, b])
    row.set_chips([b])
    row.set_chips([a, b])
    assert _order(row) == [a, b]


def test_pan_starts_only_when_visible_and_overflowing(row):
    chips = [_Chip("x", "a long chip value " * 3) for _ in range(6)]
    row.set_chips(chips)
    assert not row._pan_timer.isActive()  # not on screen yet
    row.show()
    assert row._pan_timer.isActive()
    row.hide()
    assert not row._pan_timer.isActive()


def test_resize_rechecks_the_pan(row):
    row.show()
    row.set_chips([_Chip("x", "short")])
    assert not row._pan_timer.isActive()
    row.resize(20, 36)
    assert row._pan_timer.isActive()
    row.resize(2000, 36)
    assert not row._pan_timer.isActive()


def test_chip_tooltip_and_accessible_name(qapp):
    chip = _Chip("♩", "—", tooltip="Tempo")
    chip.set_value("120 BPM")
    assert chip.toolTip() == "Tempo"
    assert chip.accessibleName() == "Tempo: 120 BPM"
    assert chip.text() == "♩  120 BPM"
