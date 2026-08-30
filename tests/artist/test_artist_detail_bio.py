"""Unit tests for the artist-detail BioWidget.

Regression context: BioWidget used to render a centered "No biography
available." placeholder label plus an HLine separator even when the
artist had no biography, wasting vertical space beside the infobox. The
placeholder must not be rendered, and the separator must only appear when
it actually divides bio text from a links row below it.
"""

from types import SimpleNamespace

from PySide6.QtWidgets import QFrame, QLabel

from src.artist.artist_detail_bio import BioWidget


def _artist(**kw):
    base = {"biography": "", "MBID": "", "wikipedia_link": "", "website_link": ""}
    base.update(kw)
    return SimpleNamespace(**base)


def _labels(widget):
    return [lbl.text() for lbl in widget.findChildren(QLabel)]


def _separators(widget):
    # QLabel subclasses QFrame, so match the HLine separator by shape.
    return [f for f in widget.findChildren(QFrame) if f.frameShape() == QFrame.HLine]


def test_no_placeholder_label_when_biography_missing(qapp):
    widget = BioWidget(_artist())
    assert "No biography available." not in _labels(widget)


def test_no_separator_when_biography_missing(qapp):
    widget = BioWidget(_artist(MBID="abc-123"))
    assert _separators(widget) == []


def test_separator_present_between_bio_and_links(qapp):
    widget = BioWidget(_artist(biography="A real bio.", wikipedia_link="http://x"))
    assert len(_separators(widget)) == 1


def test_no_trailing_separator_when_bio_but_no_links(qapp):
    widget = BioWidget(_artist(biography="A real bio."))
    assert _separators(widget) == []


def test_is_empty_flag_tracks_presence_of_any_content(qapp):
    assert BioWidget(_artist()).is_empty is True
    assert BioWidget(_artist(biography="hi")).is_empty is False
    assert BioWidget(_artist(MBID="abc")).is_empty is False
    assert BioWidget(_artist(wikipedia_link="http://x")).is_empty is False
    assert BioWidget(_artist(website_link="http://y")).is_empty is False
