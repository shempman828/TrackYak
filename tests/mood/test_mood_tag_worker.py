"""Tests for MoodAutoTagWorker (src/mood/mood_tag_worker.py), the
library-wide half of docs/specs/lyrics_mood_tagging.md. Each test maps
1:1 to a numbered acceptance criterion.

Follows tests/lyrics/test_explicit_recalc_worker.py's pattern: a scratch
in-memory SQLite session plus a StubController wrapping the real
GetFromDB/AddToDB helpers, calling .run() synchronously.
"""

import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.db_helpers.add import AddToDB
from src.db.db_helpers.get import GetFromDB
from src.db.db_tables.base import Base
from src.db.db_tables.mood import Mood, MoodTrackAssociation
from src.db.db_tables.place import Place, PlaceAssociation
from src.db.db_tables.place_association_type import PlaceAssociationType
from src.db.db_tables.track import Track
from src.mood import mood_autotag_state, mood_scoring, mood_tag_worker
from src.mood.mood_tag_worker import MoodAutoTagWorker
from src.place import place_song_about_store


@pytest.fixture(autouse=True)
def _isolated_keywords(tmp_path, monkeypatch):
    keywords_path = tmp_path / "mood_keywords.json"
    keywords_path.write_text(json.dumps({"Happy": ["happy", "sunshine", "joyful"], "Sad": ["crying", "tears", "lonely"]}))
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
def _isolated_song_about_store(tmp_path, monkeypatch):
    monkeypatch.setattr(place_song_about_store, "_QUEUE_PATH", tmp_path / "queue.json")
    monkeypatch.setattr(place_song_about_store, "_DECISIONS_PATH", tmp_path / "decisions.json")
    yield


@pytest.fixture(autouse=True)
def _isolated_scan_state(tmp_path, monkeypatch):
    monkeypatch.setattr(mood_autotag_state, "_STATE_PATH", tmp_path / "scan_state.json")
    yield


@pytest.fixture(autouse=True)
def _seed_moods(session):
    session.add_all([Mood(mood_name="Happy"), Mood(mood_name="Sad")])
    session.commit()


def _make_track(session, **overrides):
    track = Track(track_name="Test Track")
    for field, value in overrides.items():
        setattr(track, field, value)
    session.add(track)
    session.commit()
    return track


def test_writes_mood_association_for_matching_lyrics(session, controller):
    track = _make_track(session, lyrics="happy happy happy sunshine joyful morning vibes")
    MoodAutoTagWorker(controller).run()

    happy = session.query(Mood).filter_by(mood_name="Happy").one()
    assoc = session.query(MoodTrackAssociation).filter_by(mood_id=happy.mood_id, track_id=track.track_id).one_or_none()
    assert assoc is not None


def test_finished_signal_reports_scanned_and_mood_tags_added(session, controller):
    _make_track(session, lyrics="happy happy happy sunshine joyful")
    _make_track(session, lyrics="a perfectly unrelated clean lyric line")

    worker = MoodAutoTagWorker(controller)
    results = []
    worker.finished.connect(lambda scanned, moods, places: results.append((scanned, moods, places)))
    worker.run()

    assert results == [(2, 1, 0)]


def test_progress_signal_reports_running_counts_not_final_totals(session, controller, song_about_type, monkeypatch):
    # Force an emit after every track (default PROGRESS_INTERVAL is 25)
    # so the mid-scan payloads are observable.
    monkeypatch.setattr(mood_tag_worker, "PROGRESS_INTERVAL", 1)

    paris = Place(place_name="Paris")
    session.add(paris)
    session.commit()

    _make_track(session, lyrics="happy happy happy sunshine joyful")
    _make_track(session, lyrics="I left my heart in Paris one cold night")
    _make_track(session, lyrics="a perfectly unrelated clean lyric line")

    worker = MoodAutoTagWorker(controller)
    updates = []
    worker.progress.connect(lambda scanned, total, moods, places, queued: updates.append((scanned, total, moods, places, queued)))
    worker.run()

    # Running counts climb as each track is scanned -- the mood tag lands
    # on track 1; "Paris" has no saved decision yet, so track 2 queues it
    # for review instead of writing a place tag -- not just a single emit
    # with the final totals.
    assert updates == [
        (1, 3, 1, 0, 0),
        (2, 3, 1, 0, 1),
        (3, 3, 1, 0, 1),
        (3, 3, 1, 0, 1),  # unconditional post-loop emit
    ]


# AC9 -------------------------------------------------------------------------
def test_second_run_is_idempotent_and_reports_zero(session, controller):
    _make_track(session, lyrics="happy happy happy sunshine joyful")

    MoodAutoTagWorker(controller).run()

    worker2 = MoodAutoTagWorker(controller)
    results = []
    worker2.finished.connect(lambda scanned, moods, places: results.append((scanned, moods, places)))
    worker2.run()

    # Nothing changed since the first run -- the track isn't scanned again.
    assert results == [(0, 0, 0)]


# AC10 ------------------------------------------------------------------------
def test_never_touches_a_track_with_existing_association(session, controller):
    track = _make_track(session, lyrics="happy happy happy sunshine joyful")
    happy = session.query(Mood).filter_by(mood_name="Happy").one()
    session.add(MoodTrackAssociation(mood_id=happy.mood_id, track_id=track.track_id))
    session.commit()

    worker = MoodAutoTagWorker(controller)
    results = []
    worker.finished.connect(lambda scanned, moods, places: results.append((scanned, moods, places)))
    worker.run()

    # Already existed before the run -- not counted as newly added.
    assert results == [(1, 0, 0)]
    count = session.query(MoodTrackAssociation).filter_by(mood_id=happy.mood_id, track_id=track.track_id).count()
    assert count == 1


# AC12 ------------------------------------------------------------------------
def test_cancellation_stops_further_writes(session, controller):
    _make_track(session, lyrics="happy happy happy sunshine joyful")
    _make_track(session, lyrics="crying tears lonely all night long")

    worker = MoodAutoTagWorker(controller)
    worker.request_cancel()
    worker.run()

    assert session.query(MoodTrackAssociation).count() == 0


# AC11 (batch path) ------------------------------------------------------------
def test_skips_tracks_with_null_or_empty_lyrics(session, controller):
    _make_track(session, lyrics=None)
    _make_track(session, lyrics="   ")

    worker = MoodAutoTagWorker(controller)
    results = []
    worker.finished.connect(lambda scanned, moods, places: results.append((scanned, moods, places)))
    worker.run()

    assert results == [(0, 0, 0)]


def test_creates_missing_mood_row_for_keyword_listed_mood(session, controller):
    # "Sad" is deliberately absent from the DB, unlike the autouse
    # _seed_moods fixture's usual Happy+Sad -- simulates mood_keywords.json
    # naming a mood with no matching `Mood` row yet (hand-edited keyword
    # file, taxonomy drift from db_defaults.py's seed list, etc).
    session.query(Mood).filter_by(mood_name="Sad").delete()
    session.commit()
    track = _make_track(session, lyrics="crying tears lonely crying tears lonely")

    MoodAutoTagWorker(controller).run()

    sad = session.query(Mood).filter_by(mood_name="Sad").one_or_none()
    assert sad is not None
    assoc = session.query(MoodTrackAssociation).filter_by(mood_id=sad.mood_id, track_id=track.track_id).one_or_none()
    assert assoc is not None


# docs/specs/mood_representative_tracks.md -----------------------------------


# AC3 ----------------------------------------------------------------------
def test_worker_writes_match_density_as_score(session, controller):
    lyrics = "happy happy happy sunshine joyful morning vibes"
    track = _make_track(session, lyrics=lyrics)
    MoodAutoTagWorker(controller).run()

    happy = session.query(Mood).filter_by(mood_name="Happy").one()
    assoc = session.query(MoodTrackAssociation).filter_by(mood_id=happy.mood_id, track_id=track.track_id).one()
    expected = mood_scoring.score_moods_detailed(lyrics)["Happy"].density
    assert assoc.score == pytest.approx(expected)
    assert assoc.score > 0


# AC4 ----------------------------------------------------------------------
def test_worker_leaves_existing_row_score_untouched(session, controller):
    track = _make_track(session, lyrics="happy happy happy sunshine joyful")
    happy = session.query(Mood).filter_by(mood_name="Happy").one()
    session.add(MoodTrackAssociation(mood_id=happy.mood_id, track_id=track.track_id, score=0.999))
    session.commit()

    MoodAutoTagWorker(controller).run()

    assoc = session.query(MoodTrackAssociation).filter_by(mood_id=happy.mood_id, track_id=track.track_id).one()
    assert assoc.score == 0.999


# AC5 ----------------------------------------------------------------------
def test_write_path_never_scores_a_row_it_did_not_create(session, controller):
    # Manual tag (no score); worker also matches it -> no duplicate, and the
    # NULL score is left for the startup backfill, not the write path.
    track = _make_track(session, lyrics="happy happy happy sunshine joyful")
    happy = session.query(Mood).filter_by(mood_name="Happy").one()
    session.add(MoodTrackAssociation(mood_id=happy.mood_id, track_id=track.track_id))
    session.commit()

    MoodAutoTagWorker(controller).run()

    assoc = session.query(MoodTrackAssociation).filter_by(mood_id=happy.mood_id, track_id=track.track_id).one()
    assert assoc.score is None


def test_run_releases_db_session_without_error(session, controller, monkeypatch):
    calls = []
    monkeypatch.setattr("src.db.db_engine.Session.remove", lambda: calls.append(True))
    _make_track(session, lyrics="happy happy happy sunshine joyful")

    MoodAutoTagWorker(controller).run()

    assert calls == [True]


# ------------------------------------------------------------------------
# Place association tests
# ------------------------------------------------------------------------


@pytest.fixture
def song_about_type(session):
    t = PlaceAssociationType(type_name="Song About")
    session.add(t)
    session.commit()
    return t


# AC7 -------------------------------------------------------------------------
def test_known_place_with_no_decision_is_queued_not_written(session, controller, song_about_type):
    # Lyric place detection can pick up a common word/name that happens to
    # match a place already in the library without the lyric actually being
    # about it (e.g. "Bath", "England") -- so a fresh place-name match is
    # queued for PlaceSongAboutReviewDialog, never written blind.
    paris = Place(place_name="Paris")
    session.add(paris)
    session.commit()
    track = _make_track(session, lyrics="I left my heart in Paris one cold night")

    worker = MoodAutoTagWorker(controller)
    results = []
    worker.finished.connect(lambda scanned, moods, places: results.append((scanned, moods, places)))
    worker.run()

    assert results == [(1, 0, 0)]
    assert session.query(PlaceAssociation).count() == 0
    queue = place_song_about_store.load_queue()
    assert queue == [{"track_id": track.track_id, "place_name": "Paris", "place_id": paris.place_id}]


def test_approved_place_decision_is_written_by_the_worker(session, controller, song_about_type):
    paris = Place(place_name="Paris")
    session.add(paris)
    session.commit()
    track = _make_track(session, lyrics="I left my heart in Paris one cold night")
    place_song_about_store.save_decision("Paris", place_song_about_store.DECISION_APPROVED)

    worker = MoodAutoTagWorker(controller)
    results = []
    worker.finished.connect(lambda scanned, moods, places: results.append((scanned, moods, places)))
    worker.run()

    assert results == [(1, 0, 1)]
    assoc = session.query(PlaceAssociation).filter_by(place_id=paris.place_id, entity_id=track.track_id, entity_type="Track").one()
    assert assoc.association_type_id == song_about_type.association_type_id
    assert place_song_about_store.load_queue() == []


# AC8 -------------------------------------------------------------------------
def test_place_not_in_db_never_creates_a_place_row(session, controller, song_about_type):
    _make_track(session, lyrics="I left my heart in Jamaica under the sun")

    MoodAutoTagWorker(controller).run()

    assert session.query(Place).count() == 0
    assert session.query(PlaceAssociation).count() == 0


# AC9 (place path) --------------------------------------------------------------
def test_second_place_run_does_not_duplicate_the_queue_entry(session, controller, song_about_type):
    paris = Place(place_name="Paris")
    session.add(paris)
    session.commit()
    _make_track(session, lyrics="I left my heart in Paris one cold night")

    MoodAutoTagWorker(controller).run()
    worker2 = MoodAutoTagWorker(controller)
    results = []
    worker2.finished.connect(lambda scanned, moods, places: results.append((scanned, moods, places)))
    worker2.run()

    assert results == [(0, 0, 0)]
    assert session.query(PlaceAssociation).count() == 0
    assert len(place_song_about_store.load_queue()) == 1


def test_second_place_run_after_approval_does_not_duplicate_the_association(session, controller, song_about_type):
    paris = Place(place_name="Paris")
    session.add(paris)
    session.commit()
    _make_track(session, lyrics="I left my heart in Paris one cold night")
    place_song_about_store.save_decision("Paris", place_song_about_store.DECISION_APPROVED)

    MoodAutoTagWorker(controller).run()
    worker2 = MoodAutoTagWorker(controller)
    results = []
    worker2.finished.connect(lambda scanned, moods, places: results.append((scanned, moods, places)))
    worker2.run()

    assert results == [(0, 0, 0)]
    assert session.query(PlaceAssociation).count() == 1


# ------------------------------------------------------------------------
# Remembered scan state: a re-run only scans what changed
# ------------------------------------------------------------------------


def _run_and_collect(controller):
    worker = MoodAutoTagWorker(controller)
    results = []
    worker.finished.connect(lambda scanned, moods, places: results.append((scanned, moods, places)))
    worker.run()
    return results


def _spy_auto_tag_track(monkeypatch):
    calls = []
    real = mood_tag_worker.auto_tag_track

    def spy(controller, track_id, lyrics, context, *, moods=None, place_names=None):
        calls.append((track_id, None if moods is None else set(moods), None if place_names is None else set(place_names)))
        return real(controller, track_id, lyrics, context, moods=moods, place_names=place_names)

    monkeypatch.setattr(mood_tag_worker, "auto_tag_track", spy)
    return calls


def test_rerun_with_nothing_changed_attempts_no_writes(session, controller, monkeypatch):
    _make_track(session, lyrics="happy happy happy sunshine joyful")
    _make_track(session, lyrics="crying tears lonely all night long")
    MoodAutoTagWorker(controller).run()

    calls = _spy_auto_tag_track(monkeypatch)
    assert _run_and_collect(controller) == [(0, 0, 0)]
    assert calls == []


def test_rerun_fully_scans_only_a_track_whose_lyrics_changed(session, controller, monkeypatch):
    _make_track(session, lyrics="a perfectly unrelated clean lyric line")
    edited = _make_track(session, lyrics="another unrelated clean lyric line")
    MoodAutoTagWorker(controller).run()

    edited.lyrics = "crying tears lonely all night long"
    session.commit()
    calls = _spy_auto_tag_track(monkeypatch)

    assert _run_and_collect(controller) == [(1, 1, 0)]
    assert calls == [(edited.track_id, None, None)]


def test_new_keyword_rescans_old_tracks_for_that_mood_only(session, controller, monkeypatch):
    # "storm" alone can't tag Sad; once it's added to Sad's keyword list,
    # "storm" + "tears" clears the two-distinct-keyword gate.
    track = _make_track(session, lyrics="storm and tears")
    MoodAutoTagWorker(controller).run()
    assert session.query(MoodTrackAssociation).count() == 0

    mood_scoring._KEYWORDS_PATH.write_text(json.dumps({"Happy": ["happy", "sunshine", "joyful"], "Sad": ["crying", "tears", "lonely", "storm"]}))
    mood_scoring._cache["mtime"] = None
    calls = _spy_auto_tag_track(monkeypatch)

    assert _run_and_collect(controller) == [(1, 1, 0)]
    assert calls == [(track.track_id, {"Sad"}, set())]

    # Once covered, the new keyword isn't tested again.
    calls.clear()
    assert _run_and_collect(controller) == [(0, 0, 0)]
    assert calls == []


def test_new_place_rescans_old_tracks_for_that_place_only(session, controller, song_about_type, monkeypatch):
    track = _make_track(session, lyrics="I left my heart in Paris one cold night")
    MoodAutoTagWorker(controller).run()
    assert place_song_about_store.load_queue() == []

    paris = Place(place_name="Paris")
    session.add(paris)
    session.commit()
    calls = _spy_auto_tag_track(monkeypatch)

    _run_and_collect(controller)

    assert calls == [(track.track_id, set(), {"Paris"})]
    assert place_song_about_store.load_queue() == [{"track_id": track.track_id, "place_name": "Paris", "place_id": paris.place_id}]


def test_cancelled_run_leaves_unscanned_tracks_for_the_next_run(session, controller):
    _make_track(session, lyrics="happy happy happy sunshine joyful")
    worker = MoodAutoTagWorker(controller)
    worker.request_cancel()
    worker.run()

    assert _run_and_collect(controller) == [(1, 1, 0)]


def test_failed_write_is_retried_on_next_run(session, controller, monkeypatch):
    track = _make_track(session, lyrics="happy happy happy sunshine joyful")
    real = controller.add.add_entities_with_fallback
    monkeypatch.setattr(controller.add, "add_entities_with_fallback", lambda model, rows: ([], rows))
    MoodAutoTagWorker(controller).run()
    monkeypatch.setattr(controller.add, "add_entities_with_fallback", real)

    calls = _spy_auto_tag_track(monkeypatch)
    assert _run_and_collect(controller) == [(1, 1, 0)]
    assert calls == [(track.track_id, None, None)]
