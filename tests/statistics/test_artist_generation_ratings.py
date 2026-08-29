"""Bug: "average rating by generation - expand generation list".

ArtistStats._generation_ratings iterates a hardcoded GENERATIONS tuple.
It used to cover only Boomer..Gen Z (begin_year 1946-2012), so an artist
born in the Silent Generation (e.g. 1930) or Gen Alpha (e.g. 2015) was
silently dropped from the breakdown no matter how many rated tracks they
had. GENERATIONS now spans Progressive Generation..Gen Beta.

Runs against a real in-memory SQLite session so the subquery/join chain
in _generation_ratings actually executes.
"""

from itertools import pairwise

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.db_tables import Artist, Role, Track, TrackArtistRole
from src.db.db_tables.base import Base
from src.statistics.stats.artists import GENERATIONS, RATING_BUCKET_MIN_N, ArtistStats


@pytest.fixture
def session_factory():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine)


def _seed_generation(session, artist_name, begin_year, rating, n_tracks):
    if session.get(Role, 1) is None:
        session.add(Role(role_id=1, role_name="Primary Artist"))
        session.flush()
    artist = Artist(artist_name=artist_name, begin_year=begin_year)
    session.add(artist)
    session.flush()
    for _ in range(n_tracks):
        track = Track(track_name=f"{artist_name} track", user_rating=rating)
        session.add(track)
        session.flush()
        session.add(TrackArtistRole(track_id=track.track_id, artist_id=artist.artist_id, role_id=1))


def test_generations_tuple_is_contiguous_and_non_overlapping():
    for (_, _, prev_end), (_, next_start, _) in pairwise(GENERATIONS):
        assert next_start == prev_end + 1


def test_pre_boomer_and_post_gen_z_cohorts_are_reported(session_factory):
    session = session_factory()
    n = RATING_BUCKET_MIN_N + 2
    _seed_generation(session, "Silent Artist", 1930, 7.0, n)
    _seed_generation(session, "Boomer Artist", 1950, 6.0, n)
    _seed_generation(session, "Alpha Artist", 2015, 8.0, n)
    session.commit()
    session.close()

    stats = ArtistStats(session_factory)
    rows = stats.get_comprehensive_artist_stats()["generation_ratings"]
    by_label = {label: (avg, count) for label, avg, count in rows}

    assert by_label["Silent Generation"] == (7.0, n)
    assert by_label["Boomer"] == (6.0, n)
    assert by_label["Gen Alpha"] == (8.0, n)


def test_sparse_new_cohort_is_still_suppressed(session_factory):
    session = session_factory()
    _seed_generation(session, "Alpha Artist", 2015, 8.0, RATING_BUCKET_MIN_N - 1)
    session.commit()
    session.close()

    stats = ArtistStats(session_factory)
    rows = stats.get_comprehensive_artist_stats()["generation_ratings"]

    assert "Gen Alpha" not in {label for label, _avg, _n in rows}
