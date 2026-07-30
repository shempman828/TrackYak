"""Tests for bug #244: adding a genre already on some of the selected
tracks to *all* of them logged a "Failed to tag N track(s)" warning and
popped a "some tracks not updated" dialog for the tracks that already had
it -- a no-op re-tag, not a failure.

add_entities_with_fallback() assumed a short result from add_entities()
always meant a real commit failure, but for composite-PK association rows
(TrackGenre's track_id+genre_id *is* its primary key) add_entities()
silently dedupes existing rows instead of raising, so the row count
legitimately comes up short. The fallback then retried those "missing"
rows one at a time, hit the same duplicate again, and reported it as
dropped.

Covers AddToDB.add_entities_with_fallback (src/db/db_helpers/add.py)
against a real in-memory SQLite session, so the composite primary key
dedup path actually executes (unlike the tag_association_tab tests, which
stub out add.py entirely).
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.db_helpers.add import AddToDB
from src.db.db_tables.associations import TrackGenre
from src.db.db_tables.base import Base
from src.db.db_tables.genre import Genre
from src.db.db_tables.track import Track


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def _make_tracks_and_genre(session, num_tracks=9):
    genre = Genre(genre_name="Metal")
    session.add(genre)
    tracks = [Track(track_name=f"Track {i}") for i in range(num_tracks)]
    session.add_all(tracks)
    session.commit()
    return tracks, genre


def test_fallback_skips_existing_rows_without_reporting_failure(session):
    tracks, genre = _make_tracks_and_genre(session)
    # Two of the nine tracks already have "Metal" tagged, as in bug #244.
    session.add_all(
        [
            TrackGenre(track_id=tracks[0].track_id, genre_id=genre.genre_id),
            TrackGenre(track_id=tracks[1].track_id, genre_id=genre.genre_id),
        ]
    )
    session.commit()

    add = AddToDB(session)
    rows = [
        {"track_id": t.track_id, "genre_id": genre.genre_id} for t in tracks
    ]
    succeeded, failed = add.add_entities_with_fallback("TrackGenre", rows)

    assert failed == []
    assert len(succeeded) == 7

    all_tagged = (
        session.query(TrackGenre).filter_by(genre_id=genre.genre_id).count()
    )
    assert all_tagged == 9


def test_fallback_reports_a_genuinely_bad_row_as_failed(session):
    tracks, genre = _make_tracks_and_genre(session, num_tracks=3)
    stale_track_id = max(t.track_id for t in tracks) + 1000

    add = AddToDB(session)
    rows = [
        {"track_id": tracks[0].track_id, "genre_id": genre.genre_id},
        {"track_id": tracks[1].track_id, "genre_id": genre.genre_id},
        {"track_id": stale_track_id, "genre_id": genre.genre_id},
    ]
    succeeded, failed = add.add_entities_with_fallback("TrackGenre", rows)

    assert failed == [{"track_id": stale_track_id, "genre_id": genre.genre_id}]
    assert len(succeeded) == 2


def test_fallback_no_op_when_every_row_already_exists(session):
    tracks, genre = _make_tracks_and_genre(session, num_tracks=2)
    session.add_all(
        [TrackGenre(track_id=t.track_id, genre_id=genre.genre_id) for t in tracks]
    )
    session.commit()

    add = AddToDB(session)
    rows = [{"track_id": t.track_id, "genre_id": genre.genre_id} for t in tracks]
    succeeded, failed = add.add_entities_with_fallback("TrackGenre", rows)

    assert succeeded == []
    assert failed == []
