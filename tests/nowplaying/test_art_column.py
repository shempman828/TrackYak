"""Art column pieces: progress strip, slide dots, art shadow pad, backdrop."""

from types import SimpleNamespace

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QColor, QFont, QPixmap
import pytest

from src.nowplaying.art.nowplaying_art import _ArtCard
from src.nowplaying.art.nowplaying_art_column import _ArtColumn, _SlideDots
from src.nowplaying.art.nowplaying_backdrop import _BlurredBackdrop, _tint_from
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


# ── Finalize fixes ───────────────────────────────────────────────────────


class _FakeWorker(QObject):
    resolved = Signal(int)
    finished = Signal()

    def __init__(self):
        super().__init__()
        self.cancelled = False
        self.waited = False
        self.done = False

    def request_cancel(self):
        self.cancelled = True

    def wait(self):
        self.waited = True

    def isFinished(self):
        return self.done


def test_cancel_art_worker_does_not_block_and_keeps_the_worker_until_it_ends(view):
    worker = _FakeWorker()
    view._art_worker = worker
    view._cancel_art_worker()
    assert worker.cancelled and not worker.waited
    assert view._art_worker is None
    assert worker in view._retired_art_workers
    worker.finished.emit()
    assert worker not in view._retired_art_workers


def test_backdrop_fade_starts_from_zero_through_the_property(view):
    view._backdrop.setProperty("backdropOpacity", 1.0)
    view._apply_backdrop(_px())
    view._fade_anim.stop()
    assert view._backdrop._opacity == 0.0


def test_artist_photo_cache_reuses_and_downscales(tmp_path, qapp):
    from src.nowplaying.art import nowplaying_art_slideshow as slideshow

    path = tmp_path / "photo.png"
    _px(2000, 1000).save(str(path))
    slideshow._artist_photo_cache.clear()
    first = slideshow._load_artist_photo(str(path))
    second = slideshow._load_artist_photo(str(path))
    assert first is second
    assert max(first.width(), first.height()) == slideshow._ARTIST_PHOTO_MAX_PX
    assert slideshow._load_artist_photo(str(tmp_path / "missing.png")) is None


def test_art_card_reuses_the_scaled_pixmap(qapp):
    card = _ArtCard()
    px = _px(1200, 1200)
    a = card._scaled_pixmap(px, 300, 300)
    b = card._scaled_pixmap(px, 300, 300)
    assert a is b
    for size in range(10, 10 + 2 * card._SCALED_CACHE_SIZE):
        card._scaled_pixmap(px, size, size)
    assert len(card._scaled_cache) == card._SCALED_CACHE_SIZE
    card.deleteLater()


def test_hidden_view_pauses_slideshow_and_auto_cycle(view):
    view.show()
    view._start_art_slideshow([(_px(), False, None), (_px(color="blue"), False, None)])
    view.toggle_auto_cycle()
    view.hide()
    assert not view._art_slide_timer.isActive()
    assert not view._auto_cycle_timer.isActive()
    view.show()
    assert view._art_slide_timer.isActive()
    assert view._auto_cycle_timer.isActive()
    view.toggle_auto_cycle()


def test_marquee_parses_its_colour_once(qapp):
    from src.nowplaying.nowplaying_marquee import MarqueeLabel, _parse_color

    assert _parse_color("rgba(180,190,240,0.70)") == QColor(180, 190, 240, 178)
    assert _parse_color("#ff0000") == QColor("#ff0000")
    assert _parse_color("rgba(bad)").isValid()
    label = MarqueeLabel("x", QFont(), "rgba(10, 20, 30, 1)")
    assert label._qcolor == QColor(10, 20, 30, 255)
    assert label.accessibleName() == "x"
    label.deleteLater()


def test_marquee_pans_only_while_visible(qapp):
    from src.nowplaying.nowplaying_marquee import MarqueeLabel

    label = MarqueeLabel("a very long title " * 10, QFont(), "#ffffff")
    label.resize(50, 20)
    label._check_scroll_needed()
    assert not label._timer.isActive()
    label.show()
    assert label._timer.isActive()
    label.hide()
    assert not label._timer.isActive()
    label.deleteLater()


def test_progress_strip_describes_itself_to_screen_readers(qapp):
    strip = _ProgressStrip()
    assert strip.accessibleName() == "Song progress"
    strip.set_duration(200_000)
    strip.set_position(50_000)
    assert strip.accessibleDescription() == "0:50 elapsed, 2:30 remaining"
    strip.reset()
    assert strip.accessibleDescription() == ""


def test_countdown_keeps_its_text_while_the_position_is_unknown(view):
    view._countdown_lbl.setText("♪  in 9s")
    view._next_lyric_ms = 20_000
    view._last_position_ms = -1
    view._update_countdown()
    assert view._countdown_lbl.text() == "♪  in 9s"
