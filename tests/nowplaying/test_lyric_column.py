"""Lyrics show as one scrolling column: past, current and upcoming lines.

Exercises the real ``NowPlayingView`` wiring plus the ``NowPlayingLyricsMixin``
position-sync path that drives ``_LyricColumn``, the follow / browse-all
toggle, and plain (unsynced) lyrics.
"""

from types import SimpleNamespace

from PySide6.QtCore import QObject, QPoint, QPointF, Qt, Signal
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QGraphicsDropShadowEffect
import pytest

from src.nowplaying.nowplaying_lyric_column import _LyricColumn
from src.nowplaying.nowplaying_view import NowPlayingView

_SYNCED_LYRICS = "\n".join(
    [
        "[00:03.00] line zero",
        "[00:05.00] line one",
        "[00:07.00] line two",
        "[00:09.00] line three",
        "[00:11.00] line four",
        "[00:13.00] line five",
    ]
)

_PLAIN_LYRICS = "first plain line\nsecond plain line\nthird plain line"


class _FakeMediaPlayer(QObject):
    position_changed = Signal(int)


@pytest.fixture
def view(qapp):
    controller = SimpleNamespace(mediaplayer=_FakeMediaPlayer())
    v = NowPlayingView(controller)
    v._sync_offset_ms = 0  # ignore any persisted sync offset
    v.resize(1000, 700)
    yield v
    v._offset_save_timer.stop()
    v.deleteLater()


@pytest.fixture
def column(qapp):
    c = _LyricColumn()
    c.resize(400, 400)
    yield c
    c.deleteLater()


def _wheel(widget, dy=-120):
    return QWheelEvent(
        QPointF(10, 10),
        QPointF(widget.mapToGlobal(QPoint(10, 10))),
        QPoint(0, 0),
        QPoint(0, dy),
        Qt.NoButton,
        Qt.NoModifier,
        Qt.NoScrollPhase,
        False,
    )


# ── view wiring ──────────────────────────────────────────────────────────


def test_synced_lyrics_fill_the_column_and_follow(view):
    view._update_lyrics(SimpleNamespace(lyrics=_SYNCED_LYRICS))
    col = view._lyric_column
    assert col.lines() == [f"line {w}" for w in ("zero", "one", "two", "three", "four", "five")]
    assert col.is_following() is True
    assert col.active_index() == -1  # nothing highlighted before playback


def test_position_sync_moves_the_highlight(view):
    view._update_lyrics(SimpleNamespace(lyrics=_SYNCED_LYRICS))
    view._on_position_changed(7500)
    assert view._lyric_column.active_text() == "line two"

    view._on_position_changed(11200)
    assert view._lyric_column.active_text() == "line four"


def test_nothing_highlighted_before_the_first_line(view):
    view._update_lyrics(SimpleNamespace(lyrics=_SYNCED_LYRICS))
    view._on_position_changed(1000)  # first line is at 3 s
    assert view._lyric_column.active_index() == -1


def test_countdown_shows_while_the_next_line_is_far_away(view):
    lyrics = "[00:20.00] late start\n[00:22.00] next"
    view._update_lyrics(SimpleNamespace(lyrics=lyrics))
    view._on_position_changed(1000)
    assert view._countdown_lbl.isVisibleTo(view)
    assert "19s" in view._countdown_lbl.text()

    view._on_position_changed(20500)
    assert not view._countdown_lbl.isVisibleTo(view)


def test_all_lines_button_stops_following_and_keeps_highlight(view):
    view._update_lyrics(SimpleNamespace(lyrics=_SYNCED_LYRICS))
    view._on_position_changed(7500)

    view._on_toggle_lyrics_mode()
    assert view._lyric_column.is_following() is False
    assert view._show_all_lyrics is True
    assert view._toggle_mode_btn.property("active") is True

    # The highlight still tracks the song while browsing.
    view._on_position_changed(9500)
    assert view._lyric_column.active_text() == "line three"

    view._on_toggle_lyrics_mode()
    assert view._lyric_column.is_following() is True
    assert view._show_all_lyrics is False
    assert view._toggle_mode_btn.property("active") is False


def test_wheel_scroll_switches_to_browse_mode(view):
    view._update_lyrics(SimpleNamespace(lyrics=_SYNCED_LYRICS))
    view._lyric_column.wheelEvent(_wheel(view._lyric_column))
    assert view._lyric_column.is_following() is False
    assert view._toggle_mode_btn.property("active") is True


def test_new_track_resets_to_follow_mode(view):
    view._update_lyrics(SimpleNamespace(lyrics=_SYNCED_LYRICS))
    view._on_toggle_lyrics_mode()
    view._update_lyrics(SimpleNamespace(lyrics=_SYNCED_LYRICS))
    assert view._lyric_column.is_following() is True
    assert view._show_all_lyrics is False


def test_plain_lyrics_have_no_highlight_and_no_follow(view):
    view._update_lyrics(SimpleNamespace(lyrics=_PLAIN_LYRICS))
    col = view._lyric_column
    assert col.lines() == ["first plain line", "second plain line", "third plain line"]
    assert col.is_following() is False
    assert col.active_index() == -1
    assert view._stack.currentIndex() == view._PAGE_LYRICS


def test_no_lyrics_clears_the_column_and_shows_credits(view):
    view._update_lyrics(SimpleNamespace(lyrics=_SYNCED_LYRICS))
    view._update_lyrics(SimpleNamespace(lyrics=None))
    assert view._lyric_column.lines() == []
    assert view._stack.currentIndex() == view._PAGE_CREDITS


def test_lyric_column_carries_a_text_shadow_over_busy_art(view):
    """Regression: lyrics get the same dark halo the metadata labels use."""
    effect = view._lyric_column.graphicsEffect()
    assert isinstance(effect, QGraphicsDropShadowEffect)
    assert effect.color().alpha() > 0


# ── widget behaviour ─────────────────────────────────────────────────────


def test_following_keeps_the_active_line_on_the_anchor(column):
    column.set_lines([f"line {i}" for i in range(20)], synced=True)
    column.set_active(10)  # hidden widget: scroll jumps without animating
    centre = column._tops[10] + column._heights[10] / 2
    assert column._scroll == pytest.approx(centre - column.height() * column._ANCHOR)


def test_browsing_scroll_is_clamped_to_the_content(column):
    column.set_lines([f"line {i}" for i in range(5)], synced=True)
    for _ in range(50):
        column.wheelEvent(_wheel(column, dy=-120))
    lo, hi = column._scroll_bounds()
    assert column._scroll == pytest.approx(hi)
    for _ in range(50):
        column.wheelEvent(_wheel(column, dy=120))
    assert column._scroll == pytest.approx(lo)


def test_plain_lines_cannot_follow(column):
    column.set_lines(["a", "b"], synced=False)
    column.set_following(True)
    assert column.is_following() is False


def test_active_line_is_brightest_and_sung_lines_recede(column):
    column.set_lines([f"line {i}" for i in range(7)], synced=True)
    column.set_active(3)
    column._emphasis = 1.0
    active = column._base_alpha(3) + (column._ACTIVE_ALPHA - column._base_alpha(3))
    assert active == pytest.approx(column._ACTIVE_ALPHA)
    assert column._base_alpha(4) > column._base_alpha(5)  # upcoming dims with distance
    assert column._base_alpha(2) < column._base_alpha(4)  # sung line dimmer than upcoming


def test_wheel_on_empty_column_is_ignored(column):
    event = _wheel(column)
    column.wheelEvent(event)
    assert not event.isAccepted()
