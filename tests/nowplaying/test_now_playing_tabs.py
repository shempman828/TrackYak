"""Now Playing tabs: the About tab, auto-cycle mode, and the instrumental
lyrics-tab lockout.
"""

from types import SimpleNamespace

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QLabel
import pytest

from src.nowplaying.nowplaying_about import _AboutPanel
from src.nowplaying.nowplaying_view import NowPlayingView

# ---------------------------------------------------------------------------
# ABOUT tab in the Now Playing panel.
# ---------------------------------------------------------------------------


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


def test_about_tab_button_present_and_switch(view):
    assert view._tab_about.text() == "ABOUT"
    assert view._stack.count() == 3

    view._switch_tab(view._PAGE_ABOUT)

    assert view._stack.currentIndex() == view._PAGE_ABOUT
    assert view._tab_about.property("active") is True
    assert view._tab_lyrics.property("active") is False
    assert view._tab_credits.property("active") is False


def test_track_description_card(panel):
    panel.load_about(_track(track_description="  A modal jazz landmark.  "))
    assert _rows(panel) == [("TRACK", "So What", "A modal jazz landmark.")]


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


def test_artist_dedup_and_primary_first(panel):
    a1 = _artist(1, "Primary Guy", "Bio one.")
    a1_dup = _artist(1, "Primary Guy", "Bio one.")
    a2 = _artist(2, "Session Guy", "Bio two.")
    panel.load_about(_track(primary_artists=[a1], artists=[a2, a1_dup, a1]))
    assert [(k, n) for (k, n, _b) in _rows(panel)] == [
        ("ARTIST", "Primary Guy"),
        ("ARTIST", "Session Guy"),
    ]


def test_placeholder_when_no_descriptions(panel):
    panel.load_about(_track())
    assert _rows(panel) == [("", "", "No descriptions available for this track")]


def test_load_about_none_placeholder(panel):
    panel.load_about(None)
    assert _rows(panel) == [("", "", "No track loaded")]


def test_about_reflects_current_track_on_each_open(view):
    view.track = _track(track_name="One", track_description="d1")
    view._switch_tab(view._PAGE_ABOUT)
    assert [n for (_k, n, _b) in _rows(view._about_panel)] == ["One"]

    view.track = _track(track_name="Two", track_description="d2")
    view._switch_tab(view._PAGE_LYRICS)
    view._switch_tab(view._PAGE_ABOUT)
    assert [n for (_k, n, _b) in _rows(view._about_panel)] == ["Two"]


def test_clearui_resets_about_panel(view):
    view.track = _track(track_name="One", track_description="d1")
    view._switch_tab(view._PAGE_ABOUT)
    assert view._about_panel._cards_layout.count() >= 1

    view.clearUI()
    assert _rows(view._about_panel) == [("", "", "No track loaded")]


# ---------------------------------------------------------------------------
# Auto-cycle mode for the Now Playing view (Ctrl+Shift++).
# ---------------------------------------------------------------------------


class _FakeMediaPlayerMode(QObject):
    position_changed = Signal(int)


@pytest.fixture
def view_mode(qapp):
    controller = SimpleNamespace(mediaplayer=_FakeMediaPlayerMode())
    v = NowPlayingView(controller)
    yield v
    v.deleteLater()


def _track_mode(**kw):
    base = {"track_name": "T", "lyrics": None, "is_instrumental": None}
    base.update(kw)
    return SimpleNamespace(**base)


def test_toggle_starts_and_stops_the_cycle_timer(view_mode):
    assert not view_mode.auto_cycle
    assert not view_mode._auto_cycle_timer.isActive()

    view_mode.toggle_auto_cycle()
    assert view_mode.auto_cycle
    assert view_mode._auto_cycle_timer.isActive()

    view_mode.toggle_auto_cycle()
    assert not view_mode.auto_cycle
    assert not view_mode._auto_cycle_timer.isActive()


def test_advance_moves_to_next_tab_and_wraps(view_mode):
    view_mode._switch_tab(view_mode._PAGE_LYRICS)

    view_mode._advance_auto_cycle()
    assert view_mode._stack.currentIndex() == view_mode._PAGE_CREDITS

    view_mode._advance_auto_cycle()
    assert view_mode._stack.currentIndex() == view_mode._PAGE_ABOUT

    view_mode._advance_auto_cycle()
    assert view_mode._stack.currentIndex() == view_mode._PAGE_LYRICS


def test_advance_skips_disabled_tabs(view_mode):
    view_mode._update_lyrics(_track_mode(is_instrumental=1))
    assert not view_mode._tab_lyrics.isEnabled()
    assert view_mode._stack.currentIndex() == view_mode._PAGE_CREDITS

    view_mode._advance_auto_cycle()
    assert view_mode._stack.currentIndex() == view_mode._PAGE_ABOUT

    # LYRICS is disabled, so ABOUT wraps straight back to CREDITS.
    view_mode._advance_auto_cycle()
    assert view_mode._stack.currentIndex() == view_mode._PAGE_CREDITS


def test_advance_is_noop_with_only_one_enabled_tab(view_mode):
    view_mode._update_lyrics(_track_mode(is_instrumental=1))
    view_mode._tabs[view_mode._PAGE_ABOUT].button.setEnabled(False)
    assert view_mode._stack.currentIndex() == view_mode._PAGE_CREDITS

    view_mode._advance_auto_cycle()
    assert view_mode._stack.currentIndex() == view_mode._PAGE_CREDITS


def test_toggling_off_leaves_current_tab_in_place(view_mode):
    view_mode.toggle_auto_cycle()
    view_mode._advance_auto_cycle()
    landed = view_mode._stack.currentIndex()
    assert landed != view_mode._PAGE_LYRICS

    view_mode.toggle_auto_cycle()
    assert view_mode._stack.currentIndex() == landed


# ---------------------------------------------------------------------------
# Instrumental tracks disable the LYRICS tab in the Now Playing panel.
# ---------------------------------------------------------------------------


class _FakeMediaPlayerTab(QObject):
    position_changed = Signal(int)


@pytest.fixture
def view_tab(qapp):
    controller = SimpleNamespace(mediaplayer=_FakeMediaPlayerTab())
    v = NowPlayingView(controller)
    yield v
    v.deleteLater()


def _track_tab(**kw):
    base = {"track_name": "T", "lyrics": None, "is_instrumental": None}
    base.update(kw)
    return SimpleNamespace(**base)


def test_instrumental_disables_lyrics_tab_and_pins_credits(view_tab):
    view_tab._update_lyrics(_track_tab(is_instrumental=1))
    assert not view_tab._tab_lyrics.isEnabled()
    assert view_tab._stack.currentIndex() == view_tab._PAGE_CREDITS


def test_switch_tab_ignores_lyrics_while_disabled(view_tab):
    view_tab._update_lyrics(_track_tab(is_instrumental=1))
    view_tab._switch_tab(view_tab._PAGE_LYRICS)
    assert view_tab._stack.currentIndex() == view_tab._PAGE_CREDITS


def test_instrumental_ignores_stale_lyrics_string(view_tab):
    view_tab._update_lyrics(_track_tab(is_instrumental=1, lyrics="[00:01.00]ghost line"))
    assert not view_tab._tab_lyrics.isEnabled()
    assert view_tab._stack.currentIndex() == view_tab._PAGE_CREDITS


def test_non_instrumental_track_re_enables_lyrics_tab(view_tab):
    view_tab._update_lyrics(_track_tab(is_instrumental=1))
    assert not view_tab._tab_lyrics.isEnabled()

    view_tab._update_lyrics(_track_tab(is_instrumental=0, lyrics="plain words"))
    assert view_tab._tab_lyrics.isEnabled()
    assert view_tab._stack.currentIndex() == view_tab._PAGE_LYRICS
