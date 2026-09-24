"""Art column pieces: progress strip, slide dots, art shadow pad, backdrop."""

from types import SimpleNamespace

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QColor, QPixmap
import pytest

from src.nowplaying.nowplaying_art import _ArtCard
from src.nowplaying.nowplaying_art_column import _ArtColumn, _SlideDots
from src.nowplaying.nowplaying_backdrop import _BlurredBackdrop, _tint_from
from src.nowplaying.nowplaying_progress import _ProgressStrip, format_ms
from src.nowplaying.nowplaying_view import NowPlayingView


class _FakeMediaPlayer(QObject):
    position_changed = Signal(int)
    duration_changed = Signal(int)

    def __init__(self):
        super().__init__()
        self.duration = 0


@pytest.fixture
def player(qapp):
    return _FakeMediaPlayer()


@pytest.fixture
def view(qapp, player):
    v = NowPlayingView(SimpleNamespace(mediaplayer=player))
    yield v
    v.deleteLater()


def _px(w=300, h=300, color="red") -> QPixmap:
    px = QPixmap(w, h)
    px.fill(QColor(color))
    return px


# ── progress strip ───────────────────────────────────────────────────────


@pytest.mark.parametrize(("ms", "text"), [(0, "0:00"), (61_000, "1:01"), (3_723_000, "1:02:03"), (-5, "0:00")])
def test_format_ms(ms, text):
    assert format_ms(ms) == text


def test_progress_strip_texts_and_clamp(qapp):
    strip = _ProgressStrip()
    assert strip.elapsed_text() == "" and strip.remaining_text() == ""
    strip.set_duration(200_000)
    strip.set_position(50_000)
    assert strip.fraction() == pytest.approx(0.25)
    assert strip.elapsed_text() == "0:50"
    assert strip.remaining_text() == "−2:30"  # noqa: RUF001 (U+2212 minus glyph)
    strip.set_position(999_999)
    assert strip.fraction() == 1.0
    strip.reset()
    assert strip.fraction() == 0.0


def test_view_feeds_the_progress_strip_from_player_signals(view, player):
    player.duration_changed.emit(120_000)
    player.position_changed.emit(30_000)
    assert view._progress.fraction() == pytest.approx(0.25)
    view.clearUI()
    assert view._progress.fraction() == 0.0


def test_update_ui_reads_the_current_duration(view, player):
    player.duration = 90_000
    view.updateUI(SimpleNamespace(track_name="x", album=None, lyrics=None, is_instrumental=None))
    player.position_changed.emit(45_000)
    assert view._progress.fraction() == pytest.approx(0.5)


# ── slide dots ───────────────────────────────────────────────────────────


def test_slide_dots_clamp_index(qapp):
    dots = _SlideDots()
    dots.set_count(3)
    dots.set_index(7)
    assert dots.index() == 2
    dots.set_count(0)
    assert dots.index() == 0


def test_dots_count_distinct_images_with_interleaved_front(view):
    front, rear, liner = _px(color="red"), _px(color="green"), _px(color="blue")
    view._start_art_slideshow([(front, False, None), (rear, False, None), (liner, False, None)], has_front=True)
    # Sequence is front, rear, front, liner — but only 3 distinct images.
    assert len(view._art_images) == 4
    assert view._slide_dots.count() == 3
    assert [view._dot_index(i) for i in range(4)] == [0, 1, 0, 2]

    view._advance_art_slide()
    assert view._slide_dots.index() == 1
    view._advance_art_slide()
    assert view._slide_dots.index() == 0
    view._advance_art_slide()
    assert view._slide_dots.index() == 2


def test_dots_without_front_follow_the_sequence(view):
    view._start_art_slideshow([(_px(), True, "A"), (_px(), True, "B")], has_front=False)
    view._advance_art_slide()
    assert view._slide_dots.index() == 1


# ── art card shadow pad / column layout ──────────────────────────────────


def test_art_sits_inside_the_shadow_pad(qapp):
    card = _ArtCard(shadow_pad=20)
    card.resize(240, 240)
    rect = card._rest_rect(_px(100, 100), False)
    assert rect.left() >= 20 and rect.top() >= 20
    assert rect.right() <= 240 - 20 and rect.bottom() <= 240 - 20


def test_art_column_centres_the_group_and_stacks_rows_under_the_art(qapp):
    card, dots, prog = _ArtCard(shadow_pad=24), _SlideDots(), _ProgressStrip()
    col = _ArtColumn(card, dots, prog)
    col.setContentsMargins(32, 36, 24, 28)
    col.resize(500, 800)
    col._relayout()
    art_bottom = card.geometry().bottom() - 24
    assert dots.geometry().top() > art_bottom
    assert prog.geometry().top() > dots.geometry().bottom()
    assert prog.width() == card.width() - 48  # strip matches the art width
    top_gap = card.geometry().top() + 24
    bottom_gap = col.height() - prog.geometry().bottom()
    assert abs(top_gap - bottom_gap) <= 36  # roughly centred in the column


def test_hidden_progress_strip_frees_its_row_for_the_art(qapp):
    card, dots, prog = _ArtCard(shadow_pad=24), _SlideDots(), _ProgressStrip()
    col = _ArtColumn(card, dots, prog)
    col.resize(500, 400)  # height-bound, so the art side depends on the rows below
    col._relayout()
    side_with_strip = card.width()
    col.set_progress_visible(False)
    assert prog.isHidden()
    assert card.width() > side_with_strip


def test_progress_strip_shows_only_in_cinema_mode(view):
    assert view._progress.isHidden()
    view.toggle_cinema_mode()
    assert not view._progress.isHidden()
    view.toggle_cinema_mode()
    assert view._progress.isHidden()


# ── backdrop ─────────────────────────────────────────────────────────────


def test_backdrop_blurs_a_small_copy_and_takes_a_tint(qapp):
    bd = _BlurredBackdrop()
    bd.set_pixmap(_px(1200, 1200, "#c03020"))
    assert bd._blurred is not None and not bd._blurred.isNull()
    assert max(bd._blurred.width(), bd._blurred.height()) <= 160
    hue = bd.tint().hsvHue()
    assert hue < 30 or hue > 330  # red-ish tint from a red cover

    bd.set_pixmap(None)
    assert bd._blurred is None


def test_greyscale_art_uses_the_accent_tint(qapp):
    assert _tint_from(_px(color="#808080")) == QColor(133, 153, 234)
