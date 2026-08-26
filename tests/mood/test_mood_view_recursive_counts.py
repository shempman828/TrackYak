"""Regression test: MoodView.get_track_counts_for_all_moods must return a
deduped recursive (own + descendants) count alongside the own/direct count,
rather than a single flat direct-only count with no rollup for sub-moods.
See src/genre/genre_view.py's GenreLoaderWorker for the identical
own/recursive dedup pattern this mirrors.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.db_helpers.get import GetFromDB
from src.db.db_tables.base import Base
from src.db.db_tables.mood import Mood, MoodTrackAssociation
from src.db.db_tables.track import Track
from src.mood.moods_view import MoodView


class _Controller:
    def __init__(self, session):
        self.get = GetFromDB(session)


def test_recursive_track_count_dedupes_track_tagged_at_multiple_levels(qapp):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    controller = _Controller(session)

    happy = Mood(mood_name="Happy")
    upbeat = Mood(mood_name="Upbeat", parent=happy)
    session.add_all([happy, upbeat])
    session.flush()

    shared_track = Track(track_name="Shared track")
    child_only_track = Track(track_name="Child-only track")
    session.add_all([shared_track, child_only_track])
    session.flush()

    # Shared track is tagged with both Happy (parent) and Upbeat (child) --
    # one physical track. Naive summation would give Happy a count of 2.
    session.add_all(
        [
            MoodTrackAssociation(mood_id=happy.mood_id, track_id=shared_track.track_id),
            MoodTrackAssociation(
                mood_id=upbeat.mood_id, track_id=shared_track.track_id
            ),
            MoodTrackAssociation(
                mood_id=upbeat.mood_id, track_id=child_only_track.track_id
            ),
        ]
    )
    session.commit()

    view = MoodView(controller)

    own_counts, recursive_counts = view.get_track_counts_for_all_moods()

    assert own_counts[happy.mood_id] == 1
    assert own_counts[upbeat.mood_id] == 2

    # Correct recursive count for Happy is 2 unique tracks (shared_track +
    # child_only_track), not 3 (1 own + 2 from Upbeat).
    assert recursive_counts[happy.mood_id] == 2
    assert recursive_counts[upbeat.mood_id] == 2

    session.close()
