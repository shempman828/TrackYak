"""The Now Playing art card captions each artist photo with the artist's
name/credit. A caption that fits is drawn statically; one too wide for the
card is panned horizontally — and faster than the metadata column's
``MarqueeLabel``, because an artist photo only dwells on screen for a few
seconds so a slow pan would never reach the end of a long credit line.
"""

from PySide6.QtGui import QPixmap
import pytest

from src.nowplaying.nowplaying_art import _ArtCard
from src.nowplaying.nowplaying_marquee import MarqueeLabel


@pytest.fixture
def card(qapp):
    w = _ArtCard()
    w.resize(400, 400)
    yield w
    w.deleteLater()


def _photo() -> QPixmap:
    px = QPixmap(400, 400)
    px.fill()
    return px


def test_short_caption_does_not_scroll(card):
    card.set_art(_photo(), is_artist=True, label="AB")
    card._check_label_scroll()
    assert not card._label_timer.isActive()
    assert card._label_offset == 0


def test_non_artist_art_never_scrolls(card):
    card.set_art(_photo(), is_artist=False, label="ignored for album covers")
    card._check_label_scroll()
    assert not card._label_timer.isActive()


def test_long_caption_pans_and_reverses(card):
    long_credit = "Wynton Learson Marsalis " * 6 + "(Trumpet, Flugelhorn, Cornet)"
    card.set_art(_photo(), is_artist=True, label=long_credit)
    card._check_label_scroll()
    assert card._label_timer.isActive()
    assert card._label_text_w > card._label_avail_w

    # Burn the start pause, then confirm the offset advances toward the end.
    card._label_pause = 0
    card._tick_label_scroll()
    assert card._label_offset == _ArtCard._LABEL_SCROLL_STEP_PX
    assert card._label_scroll_dir == 1

    # Push past the far end: the offset clamps, direction flips, and the
    # end-pause is re-armed.
    card._label_pause = 0
    card._label_offset = card._label_text_w
    card._tick_label_scroll()
    max_off = card._label_text_w - card._label_avail_w + _ArtCard._LABEL_END_PAD
    assert card._label_offset == max_off
    assert card._label_scroll_dir == -1
    assert card._label_pause == _ArtCard._LABEL_PAUSE_TICKS


def test_switching_art_resets_the_pan(card):
    card.set_art(_photo(), is_artist=True, label="Some Very Long Artist Credit " * 5)
    card._check_label_scroll()
    card._label_pause = 0
    card._tick_label_scroll()
    assert card._label_offset > 0

    card.set_art(_photo(), is_artist=True, label="Short")
    assert card._label_offset == 0
    assert card._label_scroll_dir == 1


def test_caption_pan_is_faster_than_marquee():
    card_px_per_ms = _ArtCard._LABEL_SCROLL_STEP_PX / _ArtCard._LABEL_SCROLL_INTERVAL_MS
    marquee_px_per_ms = MarqueeLabel._SCROLL_STEP_PX / MarqueeLabel._SCROLL_INTERVAL_MS
    assert card_px_per_ms > marquee_px_per_ms
