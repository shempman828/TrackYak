"""Karaoke mode shows a stack of upcoming lyric lines, not just one.

Exercises the real ``NowPlayingView`` widget wiring plus the
``NowPlayingLyricsMixin`` position-sync path that fills the preview stack.
"""

from types import SimpleNamespace

from PySide6.QtCore import QObject, Signal
import pytest

from src.nowplaying.nowplaying_view import NowPlayingView

PREVIEW_ROWS = 3

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


def test_three_preview_rows_exist_and_start_hidden(view):
    """AC1: the karaoke block owns exactly 3 preview rows, all hidden at rest."""
    assert len(view._next_lyric_lbls) == PREVIEW_ROWS
    assert all(lbl.isHidden() for lbl in view._next_lyric_lbls)
    assert all(lbl.text() == "" for lbl in view._next_lyric_lbls)


def test_preview_rows_have_distinct_roles_for_graded_dimming(view):
    """AC2: rows carry nextLyric / nextLyric2 / nextLyric3 so QSS dims each more."""
    roles = [lbl.property("npRole") for lbl in view._next_lyric_lbls]
    assert roles == ["nextLyric", "nextLyric2", "nextLyric3"]


def test_position_sync_fills_all_three_upcoming_lines(view):
    """AC3: mid-song, the next three non-empty lines show below the current one."""
    view._update_lyrics(SimpleNamespace(lyrics=_SYNCED_LYRICS))
    view._on_position_changed(5000)  # active line == "line two" (idx 2)

    assert view._karaoke_lbl.text() == "line two"
    assert _visible_previews(view) == ["line three", "line four", "line five"]


def test_near_end_only_remaining_lines_show_rest_are_cleared(view):
    """AC4: with fewer than 3 lines left, extra rows are emptied and hidden."""
    view._update_lyrics(SimpleNamespace(lyrics=_SYNCED_LYRICS))
    view._on_position_changed(5000)
    view._on_position_changed(9000)  # active line == "line four" (idx 4)

    assert view._karaoke_lbl.text() == "line four"
    assert _visible_previews(view) == ["line five"]
    assert [lbl.text() for lbl in view._next_lyric_lbls] == ["line five", "", ""]
    assert view._next_lyric_lbls[1].isHidden()
    assert view._next_lyric_lbls[2].isHidden()


def test_switching_to_show_all_hides_the_preview_stack(view):
    """AC5: leaving karaoke mode (Show All / no lyrics) hides every preview row."""
    view._update_lyrics(SimpleNamespace(lyrics=_SYNCED_LYRICS))
    view._on_position_changed(5000)
    assert _visible_previews(view)  # populated first

    view._on_toggle_lyrics_mode()  # -> "SHOW ALL" plain text
    assert view._show_all_lyrics is True
    assert all(lbl.isHidden() for lbl in view._next_lyric_lbls)

    view._update_lyrics(SimpleNamespace(lyrics=None))  # no lyrics at all
    assert all(lbl.isHidden() for lbl in view._next_lyric_lbls)
    assert all(lbl.text() == "" for lbl in view._next_lyric_lbls)
