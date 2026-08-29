"""Regression: a long track title / artist / album must not inflate the
player dock's minimum width.

TrackInfoWidget lives in the bottom player dock. A plain QLabel reports its
full unwrapped text width as its minimum size, which flows straight up into
QMainWindow's minimum size -- so a track with a very long title used to force
the whole window wider than the screen and push the player dock off-screen,
even in fullscreen. The title now scrolls and the artist/album elide, so the
widget's minimum width stays bounded regardless of text length.
"""

from types import SimpleNamespace

import pytest

from src.player.track_info_widget import TrackInfoWidget

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
