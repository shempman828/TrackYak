"""ABOUT tab in the Now Playing panel.

Feature: a third tab (after LYRICS / CREDITS) showing read-only description /
bio prose pulled from existing DB text columns for the entities tied to the
current track — track, album, artist(s), label(s), genre(s), mood(s).

Spec: docs/specs/nowplaying_about_tab.md
"""

from types import SimpleNamespace

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QLabel
import pytest

from src.nowplaying.nowplaying_about import _AboutPanel
from src.nowplaying.nowplaying_view import NowPlayingView

# ── fakes ────────────────────────────────────────────────────────────────────


class _FakeMediaPlayer(QObject):
    position_changed = Signal(int)


def _artist(aid, name, bio):
    return SimpleNamespace(artist_id=aid, artist_name=name, biography=bio)


def _album(name="Kind of Blue", desc=None, publishers=()):
    return SimpleNamespace(album_name=name, album_description=desc, publishers=list(publishers))


def _track(**kw):
    base = {
        "track_name": "So What",
        "track_description": None,
        "album": None,
        "artists": [],
        "primary_artists": [],
        "genres": [],
        "moods": [],
        "is_instrumental": None,
        "lyrics": None,
    }
    base.update(kw)
    return SimpleNamespace(**base)


# ── helpers ──────────────────────────────────────────────────────────────────


def _rows(panel):
    """(kind, name, body) per card; placeholders come back as ('', '', text)."""
    lay = panel._cards_layout
    out = []
    for i in range(lay.count()):
        w = lay.itemAt(i).widget()
        if w is None:
            continue
        if w.property("npAboutCard"):
            by_role = {lb.property("npRole"): lb.text() for lb in w.findChildren(QLabel)}
            out.append(
                (by_role.get("aboutKind"), by_role.get("aboutHeading"), by_role.get("aboutBody"))
            )
        else:
            out.append(("", "", w.text()))
    return out


@pytest.fixture
def panel(qapp):
    p = _AboutPanel()
    yield p
    p.deleteLater()


@pytest.fixture
def view(qapp):
    v = NowPlayingView(SimpleNamespace(mediaplayer=_FakeMediaPlayer()))
    yield v
    v.deleteLater()


# ── AC1: third tab button + switch ───────────────────────────────────────────


def test_about_tab_button_present_and_switch(view):
    assert view._tab_about.text() == "ABOUT"
    assert view._stack.count() == 3

    view._switch_tab(view._PAGE_ABOUT)

    assert view._stack.currentIndex() == view._PAGE_ABOUT
    assert view._tab_about.property("active") is True
    assert view._tab_lyrics.property("active") is False
    assert view._tab_credits.property("active") is False


# ── AC2: track description card ──────────────────────────────────────────────


def test_track_description_card(panel):
    panel.load_about(_track(track_description="  A modal jazz landmark.  "))
    assert _rows(panel) == [("TRACK", "So What", "A modal jazz landmark.")]


# ── AC3 + AC6: one card per entity kind, fixed order ─────────────────────────


def test_one_card_per_entity_kind_in_order(panel):
    miles = _artist(1, "Miles Davis", "Trumpeter and bandleader.")
    alb = _album(
        desc="Cool-jazz cornerstone.",
        publishers=[SimpleNamespace(publisher_name="Columbia", description="US label.")],
    )
    tr = _track(
        track_description="Track blurb.",
        album=alb,
        primary_artists=[miles],
        artists=[miles],
        genres=[SimpleNamespace(genre_name="Jazz", description="Improv-based idiom.")],
        moods=[SimpleNamespace(mood_name="Mellow", mood_description="Low-key and calm.")],
    )
    panel.load_about(tr)
    assert [r[0] for r in _rows(panel)] == ["TRACK", "ALBUM", "ARTIST", "LABEL", "GENRE", "MOOD"]
    assert _rows(panel)[3] == ("LABEL", "Columbia", "US label.")


# ── AC4: blank / None descriptions produce no card ──────────────────────────


def test_blank_descriptions_are_skipped(panel):
    tr = _track(
        track_description="   ",
        album=_album(desc=None),
        primary_artists=[_artist(1, "X", "")],
        artists=[_artist(1, "X", "")],
        genres=[SimpleNamespace(genre_name="G", description=None)],
        moods=[SimpleNamespace(mood_name="M", mood_description="   ")],
    )
    panel.load_about(tr)
    assert _rows(panel) == [("", "", "No descriptions available for this track")]


# ── AC5: artist dedup + primary first ──────────────────────────────────────


def test_artist_dedup_and_primary_first(panel):
    a1 = _artist(1, "Primary Guy", "Bio one.")
    a1_dup = _artist(1, "Primary Guy", "Bio one.")
    a2 = _artist(2, "Session Guy", "Bio two.")
    panel.load_about(_track(primary_artists=[a1], artists=[a2, a1_dup, a1]))
    assert [(k, n) for (k, n, _b) in _rows(panel)] == [
        ("ARTIST", "Primary Guy"),
        ("ARTIST", "Session Guy"),
    ]


# ── AC7: nothing has a description → placeholder ────────────────────────────


def test_placeholder_when_no_descriptions(panel):
    panel.load_about(_track())
    assert _rows(panel) == [("", "", "No descriptions available for this track")]


# ── AC8: cleared player → placeholder, no raise ────────────────────────────


def test_load_about_none_placeholder(panel):
    panel.load_about(None)
    assert _rows(panel) == [("", "", "No track loaded")]


# ── AC9: ABOUT reflects the view's current track each time it is opened ─────


def test_about_reflects_current_track_on_each_open(view):
    view.track = _track(track_name="One", track_description="d1")
    view._switch_tab(view._PAGE_ABOUT)
    assert [n for (_k, n, _b) in _rows(view._about_panel)] == ["One"]

    view.track = _track(track_name="Two", track_description="d2")
    view._switch_tab(view._PAGE_LYRICS)
    view._switch_tab(view._PAGE_ABOUT)
    assert [n for (_k, n, _b) in _rows(view._about_panel)] == ["Two"]


# ── AC10: clearUI resets the ABOUT panel ──────────────────────────────────


def test_clearui_resets_about_panel(view):
    view.track = _track(track_name="One", track_description="d1")
    view._switch_tab(view._PAGE_ABOUT)
    assert view._about_panel._cards_layout.count() >= 1

    view.clearUI()
    assert _rows(view._about_panel) == [("", "", "No track loaded")]
