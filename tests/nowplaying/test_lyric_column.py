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

_SYNCED_LYRICS = "\n".join(["[00:03.00] line zero", "[00:05.00] line one", "[00:07.00] line two", "[00:09.00] line three", "[00:11.00] line four", "[00:13.00] line five"])

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
    return QWheelEvent(QPointF(10, 10), QPointF(widget.mapToGlobal(QPoint(10, 10))), QPoint(0, 0), QPoint(0, dy), Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False)


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


def test_plain_lyrics_follow_by_progress_without_a_highlight(view):
    view._update_lyrics(SimpleNamespace(lyrics=_PLAIN_LYRICS))
    col = view._lyric_column
    assert col.lines() == ["first plain line", "second plain line", "third plain line"]
    assert col.is_following() is True
    assert view._toggle_mode_btn.isVisibleTo(view)
    assert not view._offset_group.isVisibleTo(view)
    view.controller.mediaplayer.duration = 100_000
    view._on_position_changed(90_000)
    assert col.active_index() == -1  # the paced line is never highlighted
    assert view._stack.currentIndex() == view._PAGE_LYRICS


def test_plain_lyrics_are_paced_over_five_to_ninety_five_percent(view):
    lines = "\n".join(f"plain line {i}" for i in range(100))
    view._update_lyrics(SimpleNamespace(lyrics=lines))
    col = view._lyric_column
    view.controller.mediaplayer.duration = 100_000
    view._on_position_changed(3_000)  # inside the 5 % intro
    assert col._paced == 0
    view._on_position_changed(50_000)  # half-way through the song
    assert col._paced == pytest.approx(50, abs=1)
    view._on_position_changed(97_000)  # inside the 5 % outro
    assert col._paced == 99


def test_plain_lyrics_stay_put_without_a_duration(view):
    view._update_lyrics(SimpleNamespace(lyrics="\n".join(f"l {i}" for i in range(100))))
    view._on_position_changed(50_000)  # fake player has no duration
    assert view._lyric_column._paced == -1


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


def test_synced_lyrics_start_at_the_top(column):
    """Regression: the first line used to wait 38 % down, leaving empty space above."""
    column.set_lines([f"line {i}" for i in range(20)], synced=True)
    assert column._scroll == 0.0
    column.set_active(0)
    assert column._scroll == 0.0


def test_following_centres_the_active_line_mid_song(column):
    column.set_lines([f"line {i}" for i in range(20)], synced=True)
    column.set_active(10)  # hidden widget: scroll jumps without animating
    centre = column._tops[10] + column._heights[10] / 2
    assert column._scroll == pytest.approx(centre - column.height() / 2)


def test_following_stops_scrolling_at_the_last_lines(column):
    column.set_lines([f"line {i}" for i in range(20)], synced=True)
    column.set_active(19)
    _, hi = column._scroll_bounds()
    assert column._scroll == pytest.approx(hi)
    # The last line is still fully on screen.
    assert column._tops[19] + column._heights[19] - column._scroll <= column.height()


def test_synced_lyrics_that_fit_never_scroll(column):
    column.set_lines([f"line {i}" for i in range(3)], synced=True)
    for idx in range(3):
        column.set_active(idx)
        assert column._scroll == 0.0


def test_browsing_scroll_is_clamped_to_the_content(column):
    column.set_lines([f"line {i}" for i in range(5)], synced=True)
    for _ in range(50):
        column.wheelEvent(_wheel(column, dy=-120))
    lo, hi = column._scroll_bounds()
    assert column._scroll == pytest.approx(hi)
    for _ in range(50):
        column.wheelEvent(_wheel(column, dy=120))
    assert column._scroll == pytest.approx(lo)


def test_empty_column_cannot_follow(column):
    column.clear()
    column.set_following(True)
    assert column.is_following() is False


def test_paced_plain_line_is_centred_like_a_synced_line(column):
    column.set_lines([f"plain line {i}" for i in range(100)], synced=False)
    column.set_progress(0.5)  # hidden widget: scroll jumps without animating
    idx = column._paced
    centre = column._tops[idx] + column._heights[idx] / 2
    assert column._scroll == pytest.approx(centre - column.height() / 2)
    assert column.active_index() == -1


def test_browsing_plain_lyrics_ignores_progress(column):
    column.set_lines([f"plain line {i}" for i in range(100)], synced=False)
    column.set_following(False)
    column.set_progress(0.5)
    assert column._scroll == 0.0
    column.set_following(True)  # following again jumps to the paced line
    assert column._scroll > 0.0


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


# ── plain lyrics fit the widget ──────────────────────────────────────────


def test_plain_lyrics_shrink_to_show_every_line(column):
    """Regression: unsynced lyrics used the large synced font and needed scrolling."""
    column.set_lines([f"plain line {i}" for i in range(10)], synced=False)
    assert column._font.pointSize() < column._FONT.pointSize()
    assert column._font.pointSize() >= column._MIN_PT
    assert column._content_h <= column.height()


def test_short_plain_lyrics_keep_the_full_size(column):
    column.set_lines(["one", "two", "three"], synced=False)
    assert column._font.pointSize() == column._FONT.pointSize()


def test_long_plain_lyrics_stop_at_the_readable_minimum_and_scroll(column):
    column.set_lines([f"plain line {i}" for i in range(80)], synced=False)
    assert column._font.pointSize() == column._MIN_PT
    _, hi = column._scroll_bounds()
    assert hi > 0


def test_plain_lyrics_refit_when_the_widget_grows(column):
    column.set_lines([f"plain line {i}" for i in range(10)], synced=False)
    small = column._font.pointSize()
    column.resize(400, 900)
    column._relayout()
    assert column._font.pointSize() > small


def test_synced_lyrics_keep_the_large_font(column):
    column.set_lines([f"line {i}" for i in range(80)], synced=True)
    assert column._font.pointSize() == column._FONT.pointSize()


def test_edges_fade_only_where_more_text_continues(column):
    """Regression: the true first line sat in the top fade and looked permanently darker."""
    column.set_lines([f"line {i}" for i in range(40)], synced=True)
    assert column._edge_alpha(1.0) == 1.0  # at the top: nothing above line 0
    assert column._edge_alpha(column.height() - 1.0) < 1.0  # more lines below
    column.set_active(39)
    assert column._edge_alpha(1.0) < 1.0  # sung lines scrolled off the top
    assert column._edge_alpha(column.height() - 1.0) == 1.0  # last line is the end
