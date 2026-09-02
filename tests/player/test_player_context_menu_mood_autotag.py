"""Tests for mood auto-tagging wired into the player dock's context-menu
lyrics search/save path (PlayerContextMenuMixin._on_lyrics_ready).

Mirrors tests/track/test_track_edit_mood_autotag.py's fixtures, but drives
the player-dock mixin instead of the track-edit LyricsTab, since the two
call sites are independent and previously drifted (the dock never called
the auto-tag helper at all).
"""

import json

from PySide6.QtWidgets import QWidget
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.core.status_utility import StatusManager
from src.db.db_helpers.add import AddToDB
from src.db.db_helpers.get import GetFromDB
from src.db.db_helpers.update import UpdateDB
from src.db.db_tables.base import Base
from src.db.db_tables.mood import Mood, MoodTrackAssociation
from src.db.db_tables.track import Track
from src.mood import mood_scoring
from src.player.player_context_menu import PlayerContextMenuMixin


@pytest.fixture(autouse=True)
def _isolated_keywords(tmp_path, monkeypatch):
    keywords_path = tmp_path / "mood_keywords.json"
    keywords_path.write_text(json.dumps({"Happy": ["happy", "sunshine", "joyful"]}))
    monkeypatch.setattr(mood_scoring, "_KEYWORDS_PATH", keywords_path)
    mood_scoring._cache["mtime"] = None
    mood_scoring._cache["keyword_patterns"] = None

    monkeypatch.setattr(mood_scoring, "_OPPOSITES_PATH", tmp_path / "no_opposites.json")
    mood_scoring._opposites_cache["mtime"] = None
    mood_scoring._opposites_cache["pairs"] = None
    yield
    mood_scoring._cache["mtime"] = None
    mood_scoring._cache["keyword_patterns"] = None
    mood_scoring._opposites_cache["mtime"] = None
    mood_scoring._opposites_cache["pairs"] = None


class StubController:
    def __init__(self, session):
        self.get = GetFromDB(session)
        self.add = AddToDB(session)
        self.update = UpdateDB(session)


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    yield s
    s.close()


@pytest.fixture
def controller(session):
    return StubController(session)


@pytest.fixture(autouse=True)
def _seed_happy_mood(session):
    session.add(Mood(mood_name="Happy"))
    session.commit()


def _make_track(session, **overrides):
    track = Track(track_name="Test Track")
    for field, value in overrides.items():
        setattr(track, field, value)
    session.add(track)
    session.commit()
    return track


def _mood_association_exists(session, track_id):
    happy = session.query(Mood).filter_by(mood_name="Happy").one()
    return (
        session.query(MoodTrackAssociation)
        .filter_by(mood_id=happy.mood_id, track_id=track_id)
        .one_or_none()
        is not None
    )


class _DockHarness(PlayerContextMenuMixin, QWidget):
    """Minimal stand-in for PlayerUI exposing only what the mixin's lyrics
    callbacks need, so tests don't have to construct the full player dock
    (media player, timers, layouts, etc)."""

    def __init__(self, controller, current_track):
        super().__init__()
        self.controller = controller
        self.current_track = current_track
        self.parent_window = None
        self._lyric_search_track = current_track


def _make_dock(controller, track):
    return _DockHarness(controller, track)


# AC1/AC2 ----------------------------------------------------------------
def test_context_menu_lyrics_save_adds_mood_association(qapp, session, controller):
    track = _make_track(session)
    dock = _make_dock(controller, track)

    dock._on_lyrics_ready("happy happy happy sunshine joyful morning vibes")

    assert _mood_association_exists(session, track.track_id)


def test_context_menu_lyrics_save_reports_tagged_moods(qapp, session, controller, monkeypatch):
    track = _make_track(session)
    dock = _make_dock(controller, track)

    messages = []
    monkeypatch.setattr(
        StatusManager, "show_message", lambda msg, duration=0: messages.append(msg)
    )

    dock._on_lyrics_ready("happy happy happy sunshine joyful morning vibes")

    assert any("Tagged mood(s): Happy" in m for m in messages)


def test_context_menu_lyrics_save_with_no_mood_match_reports_plain_message(
    qapp, session, controller, monkeypatch
):
    track = _make_track(session)
    dock = _make_dock(controller, track)

    messages = []
    monkeypatch.setattr(
        StatusManager, "show_message", lambda msg, duration=0: messages.append(msg)
    )

    dock._on_lyrics_ready("a perfectly unrelated clean lyric line")

    assert not _mood_association_exists(session, track.track_id)
    assert messages == ["Lyrics found and saved."]


# AC4 ----------------------------------------------------------------------
def test_context_menu_mood_autotag_failure_does_not_block_lyrics_save(
    qapp, session, controller, monkeypatch
):
    track = _make_track(session)
    dock = _make_dock(controller, track)

    # Break the auto-tag context build (e.g. a DB read error while loading
    # Mood/Place lookups) -- this is caught inside auto_tag_lyrics_safe's
    # own broad except, so the lyrics save that already happened above it
    # in _on_lyrics_ready must be unaffected.
    monkeypatch.setattr(
        "src.mood.mood_autotag.build_autotag_context",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    dock._on_lyrics_ready("happy happy happy sunshine joyful morning vibes")

    session.refresh(track)
    assert track.lyrics == "happy happy happy sunshine joyful morning vibes"
    assert not _mood_association_exists(session, track.track_id)
