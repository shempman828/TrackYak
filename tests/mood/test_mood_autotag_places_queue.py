"""Tests for the "Song About" place review queue wired into
src/mood/mood_autotag.py. A lyric-detected place with no saved decision
must never be written straight to place_associations -- lyric place
detection can pick up a common word/name (e.g. "Bath", "England") that
matches a place already in the library without the lyric actually being
about it. It's queued for PlaceSongAboutReviewDialog instead, and a
previously-approved/rejected/remapped place name resolves automatically.
"""

import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.db_helpers.add import AddToDB
from src.db.db_helpers.get import GetFromDB
from src.db.db_tables.base import Base
from src.db.db_tables.place import Place, PlaceAssociation
from src.db.db_tables.place_association_type import PlaceAssociationType
from src.db.db_tables.track import Track
from src.mood import mood_autotag, mood_scoring
from src.place import place_song_about_store as store


@pytest.fixture(autouse=True)
def _isolated_keywords(tmp_path, monkeypatch):
    keywords_path = tmp_path / "mood_keywords.json"
    keywords_path.write_text(json.dumps({}))
    monkeypatch.setattr(mood_scoring, "_KEYWORDS_PATH", keywords_path)
    mood_scoring._cache["mtime"] = None
    mood_scoring._cache["keyword_patterns"] = None
    monkeypatch.setattr(mood_scoring, "_OPPOSITES_PATH", tmp_path / "no_opposites.json")
    mood_scoring._opposites_cache["mtime"] = None
    mood_scoring._opposites_cache["pairs"] = None
    yield
    mood_scoring._cache["mtime"] = None
    mood_scoring._cache["keyword_patterns"] = None


@pytest.fixture(autouse=True)
def _isolated_store(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "_QUEUE_PATH", tmp_path / "queue.json")
    monkeypatch.setattr(store, "_DECISIONS_PATH", tmp_path / "decisions.json")
    yield


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


def _make_track(session, **overrides):
    track = Track(track_name="Test Track")
    for field, value in overrides.items():
        setattr(track, field, value)
    session.add(track)
    session.commit()
    return track


def _make_place(session, name):
    place = Place(place_name=name)
    session.add(place)
    session.commit()
    return place


def _song_about_associations(session, track_id):
    return session.query(PlaceAssociation).filter_by(entity_id=track_id, entity_type="Track").all()


def test_new_place_detection_is_queued_not_written(session, controller):
    _make_place(session, "Bath")
    track = _make_track(session)
    context = mood_autotag.build_autotag_context(controller)

    _moods, places_added, places_queued = mood_autotag.auto_tag_track(
        controller, track.track_id, "I took a Bath this morning", context
    )

    assert places_added == []
    assert places_queued == ["Bath"]
    assert _song_about_associations(session, track.track_id) == []
    # Not written to disk yet -- flush_pending_queue() hasn't run.
    assert store.load_queue() == []


def test_flush_pending_queue_writes_the_accumulated_batch(session, controller):
    _make_place(session, "Bath")
    track = _make_track(session)
    context = mood_autotag.build_autotag_context(controller)

    mood_autotag.auto_tag_track(controller, track.track_id, "a Bath reference", context)
    mood_autotag.flush_pending_queue(context)

    queue = store.load_queue()
    assert len(queue) == 1
    assert queue[0]["place_name"] == "Bath"
    assert queue[0]["track_id"] == track.track_id
    assert context.pending_queue == []


def test_approved_place_decision_is_written_immediately(session, controller):
    place = _make_place(session, "Paris")
    track = _make_track(session)
    store.save_decision("Paris", store.DECISION_APPROVED)
    context = mood_autotag.build_autotag_context(controller)

    _moods, places_added, places_queued = mood_autotag.auto_tag_track(
        controller, track.track_id, "I left my heart in Paris", context
    )

    assert places_added == ["Paris"]
    assert places_queued == []
    assocs = _song_about_associations(session, track.track_id)
    assert len(assocs) == 1
    assert assocs[0].place_id == place.place_id


def test_rejected_place_decision_is_never_written_or_queued(session, controller):
    _make_place(session, "England")
    track = _make_track(session)
    store.save_decision("England", store.DECISION_REJECTED)
    context = mood_autotag.build_autotag_context(controller)

    _moods, places_added, places_queued = mood_autotag.auto_tag_track(
        controller, track.track_id, "Merry old England", context
    )

    assert places_added == []
    assert places_queued == []
    assert _song_about_associations(session, track.track_id) == []


def test_remapped_place_decision_writes_the_remap_target(session, controller):
    _make_place(session, "Kingston")
    jamaica_kingston = _make_place(session, "Kingston, Jamaica")
    track = _make_track(session)
    store.save_decision(
        "Kingston",
        store.DECISION_REMAPPED,
        place_id=jamaica_kingston.place_id,
        remap_place_name="Kingston, Jamaica",
    )
    context = mood_autotag.build_autotag_context(controller)

    _moods, places_added, _queued = mood_autotag.auto_tag_track(
        controller, track.track_id, "born in Kingston", context
    )

    assert places_added == ["Kingston"]
    assocs = _song_about_associations(session, track.track_id)
    assert len(assocs) == 1
    assert assocs[0].place_id == jamaica_kingston.place_id


def test_auto_tag_lyrics_safe_flushes_the_queue_on_its_own(session, controller):
    _make_place(session, "Bath")
    track = _make_track(session)

    mood_autotag.auto_tag_lyrics_safe(controller, track.track_id, "a Bath reference")

    queue = store.load_queue()
    assert len(queue) == 1
    assert queue[0]["place_name"] == "Bath"


def test_existing_association_is_never_requeued(session, controller):
    place = _make_place(session, "Bath")
    track = _make_track(session)
    song_about = PlaceAssociationType(type_name="Song About")
    session.add(song_about)
    session.commit()
    session.add(
        PlaceAssociation(
            place_id=place.place_id,
            entity_id=track.track_id,
            entity_type="Track",
            association_type_id=song_about.association_type_id,
        )
    )
    session.commit()
    context = mood_autotag.build_autotag_context(controller)

    _moods, places_added, places_queued = mood_autotag.auto_tag_track(
        controller, track.track_id, "a Bath reference", context
    )

    assert places_added == []
    assert places_queued == []
