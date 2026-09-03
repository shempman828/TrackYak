"""Karaoke mode shows a stack of upcoming lyric lines, not just one.

Exercises the real ``NowPlayingView`` widget wiring plus the
``NowPlayingLyricsMixin`` position-sync path that fills the preview stack,
including the space-aware row count (``_recalc_preview_capacity``).
"""

from types import SimpleNamespace

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QGraphicsDropShadowEffect
import pytest

from src.nowplaying.nowplaying_view import NowPlayingView

MIN_ROWS = NowPlayingView._PREVIEW_MIN_ROWS
MAX_ROWS = NowPlayingView._PREVIEW_MAX_ROWS

_SYNCED_LYRICS = "\n".join(
    [
        "[00:00.00] line zero",
        "[00:02.00] line one",
        "[00:04.00] line two",
        "[00:06.00] line three",
        "[00:08.00] line four",
        "[00:10.00] line five",
    ]
)

_LONG_LYRICS = "\n".join(f"[00:{i:02d}.00] line {i}" for i in range(20))


class _FakeMediaPlayer(QObject):
    position_changed = Signal(int)


@pytest.fixture
def view(qapp):
    controller = SimpleNamespace(mediaplayer=_FakeMediaPlayer())
    v = NowPlayingView(controller)
    v._sync_offset_ms = 0  # ignore any persisted sync offset
    yield v
    v.deleteLater()


def _visible_previews(v):
    return [lbl.text() for lbl in v._next_lyric_lbls if not lbl.isHidden()]


def test_preview_pool_sized_for_the_tallest_case_and_starts_hidden(view):
    """AC1: the pool holds MAX_ROWS rows, all hidden and empty at rest."""
    assert len(view._next_lyric_lbls) == MAX_ROWS
    assert all(lbl.isHidden() for lbl in view._next_lyric_lbls)
    assert all(lbl.text() == "" for lbl in view._next_lyric_lbls)


def test_capacity_defaults_to_the_minimum_before_any_layout(view):
    """AC2: until a real height is known, only MIN_ROWS lines are promised."""
    assert view._preview_capacity == MIN_ROWS


def test_preview_rows_have_graded_roles_extra_rows_reuse_the_faintest(view):
    """AC3: first three rows dim in steps; any beyond reuse nextLyric3."""
    roles = [lbl.property("npRole") for lbl in view._next_lyric_lbls]
    assert roles[:3] == ["nextLyric", "nextLyric2", "nextLyric3"]
    assert all(r == "nextLyric3" for r in roles[3:])


def test_position_sync_fills_the_upcoming_lines_below_the_current(view):
    """AC4: mid-song, the remaining non-empty lines show below the current one."""
    view._update_lyrics(SimpleNamespace(lyrics=_SYNCED_LYRICS))
    view._on_position_changed(5000)  # active line == "line two" (idx 2)

    assert view._karaoke_lbl.text() == "line two"
    assert _visible_previews(view) == ["line three", "line four", "line five"]


def test_near_end_only_remaining_lines_show_rest_are_cleared(view):
    """AC5: with fewer lines left than capacity, extra rows are emptied/hidden."""
    view._update_lyrics(SimpleNamespace(lyrics=_SYNCED_LYRICS))
    view._on_position_changed(5000)
    view._on_position_changed(9000)  # active line == "line four" (idx 4)

    assert view._karaoke_lbl.text() == "line four"
    assert _visible_previews(view) == ["line five"]
    assert [lbl.text() for lbl in view._next_lyric_lbls] == ["line five"] + [""] * (MAX_ROWS - 1)
    assert all(lbl.isHidden() for lbl in view._next_lyric_lbls[1:])


def test_a_tall_karaoke_block_shows_more_than_the_minimum(view):
    """AC6: given plenty of vertical room, the stack grows past MIN_ROWS."""
    view._update_lyrics(SimpleNamespace(lyrics=_LONG_LYRICS))
    view._karaoke_block.resize(400, 1000)
    changed = view._recalc_preview_capacity()

    assert changed is True
    assert MIN_ROWS < view._preview_capacity <= MAX_ROWS

    view._on_position_changed(3000)  # active idx 3 -> 16 lines still ahead
    assert len(_visible_previews(view)) == view._preview_capacity


def test_capacity_is_clamped_between_min_and_max(view):
    """AC7: a cramped block never drops below MIN_ROWS; a huge one never exceeds MAX_ROWS."""
    view._karaoke_block.resize(400, 40)
    view._recalc_preview_capacity()
    assert view._preview_capacity == MIN_ROWS

    view._karaoke_block.resize(400, 5000)
    view._recalc_preview_capacity()
    assert view._preview_capacity == MAX_ROWS


def test_shrinking_the_block_refills_with_fewer_rows(view):
    """AC8: capacity tracks height in both directions and the stack refills."""
    view._update_lyrics(SimpleNamespace(lyrics=_LONG_LYRICS))
    view._karaoke_block.resize(400, 1000)
    view._on_position_changed(3000)
    tall_count = len(_visible_previews(view))

    view._karaoke_block.resize(400, 40)
    view._on_position_changed(5000)
    assert len(_visible_previews(view)) == MIN_ROWS
    assert tall_count > MIN_ROWS


def test_switching_to_show_all_hides_the_preview_stack(view):
    """AC9: leaving karaoke mode (Show All / no lyrics) hides every preview row."""
    view._update_lyrics(SimpleNamespace(lyrics=_SYNCED_LYRICS))
    view._on_position_changed(5000)
    assert _visible_previews(view)  # populated first

    view._on_toggle_lyrics_mode()  # -> "SHOW ALL" plain text
    assert view._show_all_lyrics is True
    assert all(lbl.isHidden() for lbl in view._next_lyric_lbls)

    view._update_lyrics(SimpleNamespace(lyrics=None))  # no lyrics at all
    assert all(lbl.isHidden() for lbl in view._next_lyric_lbls)
    assert all(lbl.text() == "" for lbl in view._next_lyric_lbls)


def test_karaoke_stack_carries_a_text_shadow_over_busy_art(view):
    """Regression: the current karaoke line and every preview row get the same
    dark halo the metadata labels use, so lyrics stay legible when the backdrop
    cover art itself contains text."""
    line_effect = view._karaoke_lbl.graphicsEffect()
    assert isinstance(line_effect, QGraphicsDropShadowEffect)
    assert line_effect.color().alpha() > 0

    for lbl in view._next_lyric_lbls:
        effect = lbl.graphicsEffect()
        assert isinstance(effect, QGraphicsDropShadowEffect)
        assert effect.color().alpha() > 0
