"""Per-track auto-fill in the track editor: is_explicit and mood/place
auto-tagging.
"""

import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.db_helpers.add import AddToDB
from src.db.db_helpers.get import GetFromDB
from src.db.db_tables.base import Base
from src.db.db_tables.mood import Mood, MoodTrackAssociation
from src.db.db_tables.track import Track
from src.foundation import censor
from src.lyrics.autotag import mood_scoring
from src.metadata.readers.metadata_mapping import VORBIS_TRACK_MAPPINGS
from src.track.edit.track_edit_lyrics import LyricsTab

# ---------------------------------------------------------------------------
# Tests for the per-track auto-fill of is_explicit in the track edit
# ---------------------------------------------------------------------------


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


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    yield s
    s.close()


def _make_track(session, **overrides):
    track = Track(track_name="Test Track")
    for field, value in overrides.items():
        setattr(track, field, value)
    session.add(track)
    session.commit()
    return track


class _Controller:
    """LyricsTab only stores this; none of the paths under test call it."""


def _make_tab(tracks):
    tab = LyricsTab(tracks, _Controller())
    tab.load(tracks)
    return tab


# AC1 -------------------------------------------------------------------------
def test_saving_explicit_lyrics_sets_is_explicit_true_when_null(qapp, session):
    track = _make_track(session, is_explicit=None)
    tab = _make_tab([track])
    tab._edit.setPlainText("this shit is real")
    changes = tab.collect_changes()
    assert changes["is_explicit"] is True

    for field, value in changes.items():
        setattr(track, field, value)
    session.commit()
    reloaded = session.get(Track, track.track_id)
    # is_explicit is Column(Integer), not Boolean -- SQLite round-trips
    # Python True as the int 1, so check truthiness, not identity.
    assert reloaded.is_explicit


# AC2 -------------------------------------------------------------------------
def test_saving_clean_lyrics_sets_is_explicit_false_when_null(qapp, session):
    track = _make_track(session, is_explicit=None)
    tab = _make_tab([track])
    tab._edit.setPlainText("a perfectly clean lyric line")
    changes = tab.collect_changes()
    assert changes["is_explicit"] is False

    for field, value in changes.items():
        setattr(track, field, value)
    session.commit()
    reloaded = session.get(Track, track.track_id)
    # is_explicit is Column(Integer): False round-trips as 0, which is
    # falsy but not `is False` -- distinct from "still NULL".
    assert reloaded.is_explicit is not None
    assert not reloaded.is_explicit


# AC3 -------------------------------------------------------------------------
def test_saving_new_lyrics_does_not_overwrite_existing_is_explicit(qapp, session):
    track = _make_track(session, lyrics="old clean lyrics", is_explicit=False)
    tab = _make_tab([track])
    tab._edit.setPlainText("this shit is real")
    changes = tab.collect_changes()
    assert changes["lyrics"] == "this shit is real"
    assert "is_explicit" not in changes


# AC4 -------------------------------------------------------------------------
def test_manual_checkbox_choice_wins_over_autofill_same_session(qapp, session):
    from src.track.edit.track_edit_fieldform import _read_widget

    track = _make_track(session, is_explicit=None)
    tab = _make_tab([track])
    # Clean lyrics -- auto-fill would compute False -- but the user
    # manually checks the box to True in the same session before Save.
    tab._edit.setPlainText("a perfectly clean lyric line")
    tab._explicit_widget.setChecked(True)
    tab._mark_dirty("is_explicit")

    assert _read_widget(tab._explicit_widget) is True
    changes = tab.collect_changes()
    assert changes["is_explicit"] is True


# AC7 -------------------------------------------------------------------------
def test_multi_track_mode_never_autofills(qapp, session):
    t1 = _make_track(session, track_name="Track 1", is_explicit=None)
    t2 = _make_track(session, track_name="Track 2", is_explicit=None, lyrics="shit")
    tab = _make_tab([t1, t2])
    # Multi-track mode blanks the lyrics box on load and disables search;
    # nothing types "dirty" lyrics here, so nothing should be computed.
    changes = tab.collect_changes()
    assert "is_explicit" not in changes


# AC13 (single-track path) -----------------------------------------------------
def test_empty_lyrics_never_autofills(qapp, session):
    track = _make_track(session, lyrics=None, is_explicit=None)
    tab = _make_tab([track])
    # No edits made at all -- lyrics stay empty/None.
    changes = tab.collect_changes()
    assert "is_explicit" not in changes


# AC12 --------------------------------------------------------------------------
def test_metadata_tag_mapping_covers_is_explicit():
    assert VORBIS_TRACK_MAPPINGS["EXPLICIT"] == {
        "field": "is_explicit",
        "type": int,
        "entity": "Track",
    }


# ---------------------------------------------------------------------------
# Tests for the per-track mood/place auto-tag wiring in the track edit
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolated_wordlist_autotag(tmp_path, monkeypatch):
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
    # comment in tests/mood/test_mood_scoring.py).
    monkeypatch.setattr(mood_scoring, "_OPPOSITES_PATH", tmp_path / "no_opposites.json")
    mood_scoring._opposites_cache["mtime"] = None
    mood_scoring._opposites_cache["pairs"] = None
    yield
    mood_scoring._cache["mtime"] = None
    mood_scoring._cache["keyword_patterns"] = None
    mood_scoring._opposites_cache["mtime"] = None
    mood_scoring._opposites_cache["pairs"] = None


class StubController:
    def __init__(self, session_autotag):
        self.get = GetFromDB(session_autotag)
        self.add = AddToDB(session_autotag)


@pytest.fixture
def session_autotag():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    yield s
    s.close()


@pytest.fixture
def controller(session_autotag):
    return StubController(session_autotag)


@pytest.fixture(autouse=True)
def _seed_happy_mood(session_autotag):
    session_autotag.add(Mood(mood_name="Happy"))
    session_autotag.commit()


def _make_track_autotag(session_autotag, **overrides):
    track = Track(track_name="Test Track")
    for field, value in overrides.items():
        setattr(track, field, value)
    session_autotag.add(track)
    session_autotag.commit()
    return track


def _make_tab_autotag(tracks, controller):
    tab = LyricsTab(tracks, controller)
    tab.load(tracks)
    return tab


def _mood_association_exists(session_autotag, track_id):
    happy = session_autotag.query(Mood).filter_by(mood_name="Happy").one()
    return (
        session_autotag.query(MoodTrackAssociation)
        .filter_by(mood_id=happy.mood_id, track_id=track_id)
        .one_or_none()
        is not None
    )


# AC15 ------------------------------------------------------------------------
def test_saving_matching_lyrics_adds_mood_association(qapp, session_autotag, controller):
    track = _make_track_autotag(session_autotag)
    tab = _make_tab_autotag([track], controller)
    tab._edit.setPlainText("happy happy happy sunshine joyful morning vibes")
    tab.collect_changes()

    assert _mood_association_exists(session_autotag, track.track_id)


def test_saving_non_matching_lyrics_adds_no_mood_association(qapp, session_autotag, controller):
    track = _make_track_autotag(session_autotag)
    tab = _make_tab_autotag([track], controller)
    tab._edit.setPlainText("a perfectly unrelated clean lyric line")
    tab.collect_changes()

    assert not _mood_association_exists(session_autotag, track.track_id)


# AC16 ------------------------------------------------------------------------
def test_lyrics_found_via_search_triggers_autotag_before_save(qapp, session_autotag, controller):
    track = _make_track_autotag(session_autotag)
    tab = _make_tab_autotag([track], controller)

    tab._on_lyrics_ready("happy happy happy sunshine joyful morning vibes")

    assert _mood_association_exists(session_autotag, track.track_id)


# AC17 ------------------------------------------------------------------------
def test_existing_manual_mood_association_is_not_duplicated_or_altered(
    qapp, session_autotag, controller
):
    track = _make_track_autotag(session_autotag)
    happy = session_autotag.query(Mood).filter_by(mood_name="Happy").one()
    session_autotag.add(MoodTrackAssociation(mood_id=happy.mood_id, track_id=track.track_id))
    session_autotag.commit()

    tab = _make_tab_autotag([track], controller)
    tab._edit.setPlainText("happy happy happy sunshine joyful morning vibes")
    tab.collect_changes()

    count = (
        session_autotag.query(MoodTrackAssociation)
        .filter_by(mood_id=happy.mood_id, track_id=track.track_id)
        .count()
    )
    assert count == 1


def test_multi_track_mode_never_autotags(qapp, session_autotag, controller):
    t1 = _make_track_autotag(session_autotag, track_name="Track 1")
    t2 = _make_track_autotag(
        session_autotag,
        track_name="Track 2",
        lyrics="happy happy happy sunshine joyful",
    )
    tab = _make_tab_autotag([t1, t2], controller)
    tab.collect_changes()

    assert not _mood_association_exists(session_autotag, t1.track_id)
    assert not _mood_association_exists(session_autotag, t2.track_id)
