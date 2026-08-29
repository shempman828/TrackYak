"""Regression: the queue dock's pinned "Now Playing" card must not inflate
its minimum width on a long track title / artist / album.

Same failure mode as tests/player/test_track_info_widget_minsize.py, but for
_NowPlayingCard in the (right-hand) queue dock: a plain QLabel reports its
full unwrapped text width as its layout minimum, which propagates into
QMainWindow's minimum size and pushes the window past the screen edge on a
track change. The card's labels now elide, so its minimum width stays
bounded regardless of text length.
"""

from types import SimpleNamespace

import pytest

from src.player.queue_dock import _NowPlayingCard

pytestmark = pytest.mark.usefixtures("qapp")

_SHORT = "Song"
_LONG = (
    "An Absurdly Long Track Title That Some Live Bootleg Or Classical Work "
    "Might Legitimately Carry Across The Entire Width Of A 4K Monitor And Then "
    "Keep Going For A While Longer Just To Be Sure"
)


def _track(text):
    return SimpleNamespace(
        track_name=text,
        artists=[SimpleNamespace(artist_name=text)],
        album=SimpleNamespace(album_name=text),
    )


def test_card_minimum_width_does_not_scale_with_text_length():
    card = _NowPlayingCard()

    card.update_track(_track(_SHORT))
    short_min = card.minimumSizeHint().width()

    card.update_track(_track(_LONG))
    long_min = card.minimumSizeHint().width()

    assert long_min < 400
    assert long_min - short_min < 50


def test_card_keeps_full_title_text():
    card = _NowPlayingCard()
    card.update_track(_track(_LONG))

    assert card._title.text() == _LONG
