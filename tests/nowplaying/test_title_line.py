"""The Now Playing title line wraps short titles and only pans long ones.

The track title word-wraps up to three lines like a normal label; a title that
would need a fourth line falls back to the horizontally-panning ``MarqueeLabel``
used by the artist line directly below it. Text updates go through ``set_text``
and are routed through ``censor_text``; clearing resets the placeholder.
"""

from types import SimpleNamespace

from PySide6.QtCore import QObject, Signal
import pytest

from src.nowplaying import nowplaying_view as npv
from src.nowplaying.nowplaying_view import NowPlayingView, _AdaptiveTitle


class _FakeMediaPlayer(QObject):
    position_changed = Signal(int)


@pytest.fixture
def view(qapp):
    controller = SimpleNamespace(mediaplayer=_FakeMediaPlayer())
    v = NowPlayingView(controller)
    yield v
    v.deleteLater()


@pytest.fixture
def title(qapp):
    w = _AdaptiveTitle("x", NowPlayingView._TITLE_FONT, "rgba(230,235,255,0.94)")
    w.resize(420, 300)
    yield w
    w.deleteLater()


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


# ── widget wiring ────────────────────────────────────────────────────────────


def test_title_widget_is_adaptive(view):
    assert isinstance(view._title_lbl, _AdaptiveTitle)


# ── wrap vs. marquee decision ────────────────────────────────────────────────


def test_short_title_wraps_no_marquee(title):
    title.set_text("So What")
    assert not title._wrap.isHidden()
    assert title._marquee.isHidden()
    assert title._wrap.text() == "So What"


def test_long_title_falls_back_to_marquee(title):
    long = "supercalifragilisticexpialidocious " * 40
    assert title._line_count(long, title._avail_width()) > 3
    title.set_text(long)
    assert not title._marquee.isHidden()
    assert title._wrap.isHidden()
    assert title._marquee._text == long


def test_three_lines_still_wraps(title, monkeypatch):
    monkeypatch.setattr(title, "_line_count", lambda *a: 3)
    title.set_text("whatever")
    assert title._marquee.isHidden()
    assert not title._wrap.isHidden()


def test_four_lines_switches_to_marquee(title, monkeypatch):
    monkeypatch.setattr(title, "_line_count", lambda *a: 4)
    title.set_text("whatever")
    assert not title._marquee.isHidden()
    assert title._wrap.isHidden()
    assert title._marquee._text == "whatever"


def test_line_count_single_word_is_one(title):
    assert title._line_count("Hi", 400) == 1


def test_wrap_reserves_no_blank_row(title):
    """Regression: the wrapped title must reserve exactly the rows it renders.

    ``_line_count`` used to predict wrapping with ``QFontMetrics.boundingRect``,
    whose break points can drift a line past what the ``QLabel`` actually draws;
    ``_apply_layout`` then pinned ``_wrap`` a whole line too tall, leaving a
    blank row under short titles.
    """
    from PySide6.QtGui import QFontMetrics

    ls = QFontMetrics(NowPlayingView._TITLE_FONT).lineSpacing()
    titles = [
        "So What",
        "A Love Supreme, Pt. I - Acknowledgement",
        "Concerto for Group and Orchestra: Third Movement",
        "While My Guitar Gently Weeps",
        "Sing, Sing, Sing (With a Swing)",
        "Blue in Green",
    ]
    for text in titles:
        for width in range(280, 640, 8):
            title.resize(width, 300)
            title.set_text(text)
            if title._wrap.isHidden():
                continue
            reserved = title._wrap.height()
            # Measure the true render height off the unconstrained probe --
            # _wrap is pinned with setFixedHeight so its own heightForWidth
            # just echoes `reserved` back.
            rendered = title._probe.heightForWidth(title._avail_width())
            assert abs(reserved - rendered) < ls * 0.5, (
                f"{text!r} @ {width}px: reserved {reserved}px for a title that "
                f"renders in {rendered}px"
            )


def test_title_shrinks_after_a_taller_title(title):
    """Regression: a wrapped title must not pin the row height for the session.

    ``_line_count`` used to measure ``_wrap`` itself, but ``_apply_layout`` pins
    ``_wrap`` with ``setFixedHeight`` and ``QLabel.heightForWidth`` clamps to the
    widget's maximum height -- so once a multi-line title set the height every
    later measurement floored there and short titles kept the tall row.
    """
    from PySide6.QtGui import QFontMetrics

    ls = QFontMetrics(NowPlayingView._TITLE_FONT).lineSpacing()

    tall = "Concerto for Group and Orchestra: Third Movement"
    title.set_text(tall)
    assert not title._wrap.isHidden()
    tall_h = title._wrap.height()
    assert tall_h > ls * 1.5, "fixture title did not wrap to multiple lines"

    title.set_text("So What")
    assert not title._wrap.isHidden()
    assert title._wrap.height() < tall_h
    assert abs(title._wrap.height() - ls) < ls * 0.5, "short title not back to one line"


def test_switching_back_to_short_title_restores_wrap(title, monkeypatch):
    monkeypatch.setattr(title, "_line_count", lambda *a: 5)
    title.set_text("way too long")
    assert not title._marquee.isHidden()

    monkeypatch.setattr(title, "_line_count", lambda *a: 1)
    title.set_text("short")
    assert title._marquee.isHidden()
    assert not title._wrap.isHidden()
    assert title._wrap.text() == "short"


# ── update path / censoring / clear ──────────────────────────────────────────


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
