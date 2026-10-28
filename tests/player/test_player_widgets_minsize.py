"""Regression: a long track title / artist / album must not inflate the
minimum width of the player dock's TrackInfoWidget or the queue dock's
Now Playing card. A plain QLabel reports its full unwrapped text width as
its minimum, which flows into QMainWindow's minimum size and pushes the
window past the screen edge.
"""

from types import SimpleNamespace

import pytest

from src.player.ui.queue_dock import _NowPlayingCard
from src.player.ui.track_info_widget import TrackInfoWidget

# ---------------------------------------------------------------------------
# Regression: a long track title / artist / album must not inflate the
# ---------------------------------------------------------------------------


pytestmark = pytest.mark.usefixtures("qapp")


_SHORT = "Song"


_LONG = (
    "An Absurdly Long Track Title That Some Live Bootleg Or Classical Work "
    "Might Legitimately Carry Across The Entire Width Of A 4K Monitor And Then "
    "Keep Going For A While Longer Just To Be Sure"
)


def _track(text):
    return SimpleNamespace(
        track_name=text, primary_artist_names=text, artists=[], album_name=text, release_year=None
    )


def test_minimum_width_does_not_scale_with_text_length():
    widget = TrackInfoWidget(controller=None)

    widget.update_track(_track(_SHORT))
    short_min = widget.minimumSizeHint().width()

    widget.update_track(_track(_LONG))
    long_min = widget.minimumSizeHint().width()

    # The long strings are ~10x the short one; without the fix long_min blows
    # past a thousand pixels. It must stay small and essentially unchanged.
    assert long_min < 400
    assert long_min - short_min < 50


def test_full_text_is_preserved_for_reads():
    widget = TrackInfoWidget(controller=None)
    widget.update_track(_track(_LONG))

    # Other code reads .text() off these labels; it must get the real string,
    # not the on-screen elided form.
    assert widget.title_label.text() == _LONG
    assert widget.artist_label.text() == _LONG


# ---------------------------------------------------------------------------
# Regression: the queue dock's pinned "Now Playing" card must not inflate
# ---------------------------------------------------------------------------


def _card_track(text):
    return SimpleNamespace(
        track_name=text,
        artists=[SimpleNamespace(artist_name=text)],
        album=SimpleNamespace(album_name=text),
    )


def test_card_minimum_width_does_not_scale_with_text_length():
    card = _NowPlayingCard()

    card.update_track(_card_track(_SHORT))
    short_min = card.minimumSizeHint().width()

    card.update_track(_card_track(_LONG))
    long_min = card.minimumSizeHint().width()

    assert long_min < 400
    assert long_min - short_min < 50


def test_card_keeps_full_title_text():
    card = _NowPlayingCard()
    card.update_track(_card_track(_LONG))

    assert card._title.text() == _LONG
