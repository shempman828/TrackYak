"""Tests for the per-track mood/place auto-tag wiring in the track edit
dialog's Lyrics tab (docs/specs/lyrics_mood_tagging.md). Each test maps
1:1 to a numbered acceptance criterion.

LyricsTab writes mood/place associations directly via self.controller
(the "relationship tabs write directly" convention documented in
_BaseTab), rather than through collect_changes()'s scalar-field dict --
so these tests exercise the real DB write path with a StubController
wrapping GetFromDB/AddToDB over a scratch in-memory session.
"""

import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.core import censor
from src.db.db_helpers.add import AddToDB
from src.db.db_helpers.get import GetFromDB
from src.db.db_tables.base import Base
from src.db.db_tables.mood import Mood, MoodTrackAssociation
from src.db.db_tables.track import Track
from src.lyrics import mood_scoring
from src.track.track_edit_lyrics import LyricsTab


@pytest.fixture(autouse=True)
def _isolated_wordlist(tmp_path, monkeypatch):
    wordlist = tmp_path / "explicit_words.txt"
    wordlist.write_text("shit\nfuck\n")
    monkeypatch.setattr(censor, "_WORDLIST_PATH", wordlist)
    censor._cache["mtime"] = None
    censor._cache["pattern"] = None
    yield
    censor._cache["mtime"] = None
    censor._cache["pattern"] = None


@pytest.fixture(autouse=True)
def _isolated_keywords(tmp_path, monkeypatch):
    keywords_path = tmp_path / "mood_keywords.json"
    keywords_path.write_text(
        json.dumps({"Happy": ["happy", "sunshine", "joyful"]})
    )
    monkeypatch.setattr(mood_scoring, "_KEYWORDS_PATH", keywords_path)
    mood_scoring._cache["mtime"] = None
    mood_scoring._cache["keyword_patterns"] = None

    # Isolate from the real assets/mood_opposites.json (see the matching
    # comment in tests/lyrics/test_mood_scoring.py).
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


def _make_tab(tracks, controller):
    tab = LyricsTab(tracks, controller)
    tab.load(tracks)
    return tab


def _mood_association_exists(session, track_id):
    happy = session.query(Mood).filter_by(mood_name="Happy").one()
    return (
        session.query(MoodTrackAssociation)
        .filter_by(mood_id=happy.mood_id, track_id=track_id)
        .one_or_none()
        is not None
    )


# AC15 ------------------------------------------------------------------------
def test_saving_matching_lyrics_adds_mood_association(qapp, session, controller):
    track = _make_track(session)
    tab = _make_tab([track], controller)
    tab._edit.setPlainText("happy happy happy sunshine joyful morning vibes")
    tab.collect_changes()

    assert _mood_association_exists(session, track.track_id)


def test_saving_non_matching_lyrics_adds_no_mood_association(qapp, session, controller):
    track = _make_track(session)
    tab = _make_tab([track], controller)
    tab._edit.setPlainText("a perfectly unrelated clean lyric line")
    tab.collect_changes()

    assert not _mood_association_exists(session, track.track_id)


# AC16 ------------------------------------------------------------------------
def test_lyrics_found_via_search_triggers_autotag_before_save(qapp, session, controller):
    track = _make_track(session)
    tab = _make_tab([track], controller)

    tab._on_lyrics_ready("happy happy happy sunshine joyful morning vibes")

    assert _mood_association_exists(session, track.track_id)


# AC17 ------------------------------------------------------------------------
def test_existing_manual_mood_association_is_not_duplicated_or_altered(
    qapp, session, controller
):
    track = _make_track(session)
    happy = session.query(Mood).filter_by(mood_name="Happy").one()
    session.add(MoodTrackAssociation(mood_id=happy.mood_id, track_id=track.track_id))
    session.commit()

    tab = _make_tab([track], controller)
    tab._edit.setPlainText("happy happy happy sunshine joyful morning vibes")
    tab.collect_changes()

    count = (
        session.query(MoodTrackAssociation)
        .filter_by(mood_id=happy.mood_id, track_id=track.track_id)
        .count()
    )
    assert count == 1


def test_multi_track_mode_never_autotags(qapp, session, controller):
    t1 = _make_track(session, track_name="Track 1")
    t2 = _make_track(
        session,
        track_name="Track 2",
        lyrics="happy happy happy sunshine joyful",
    )
    tab = _make_tab([t1, t2], controller)
    tab.collect_changes()

    assert not _mood_association_exists(session, t1.track_id)
    assert not _mood_association_exists(session, t2.track_id)
