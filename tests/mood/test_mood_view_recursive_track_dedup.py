"""Regression test: MoodView.get_tracks_for_mood(include_children=True) must
return each physical track once, even when that track is tagged at both a
parent mood and one of its descendant moods. Before the fix the recursive
branch appended ``association.track`` with no cross-mood dedup, so a track
tagged at two levels showed up twice in the mood view's track list.
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


def test_recursive_get_tracks_for_mood_dedupes_multilevel_track(qapp):
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

    # shared_track is tagged with both Happy (parent) and Upbeat (child).
    session.add_all(
        [
            MoodTrackAssociation(mood_id=happy.mood_id, track_id=shared_track.track_id),
            MoodTrackAssociation(mood_id=upbeat.mood_id, track_id=shared_track.track_id),
            MoodTrackAssociation(
                mood_id=upbeat.mood_id, track_id=child_only_track.track_id
            ),
        ]
    )
    session.commit()

    view = MoodView(controller)

    tracks = view.get_tracks_for_mood(happy.mood_id, include_children=True)

    track_ids = [t.track_id for t in tracks]
    # Two unique tracks, each listed once -- not shared_track twice.
    assert sorted(track_ids) == sorted(
        {shared_track.track_id, child_only_track.track_id}
    )
    assert len(track_ids) == len(set(track_ids))

    # Non-recursive path for the child is unaffected.
    child_tracks = view.get_tracks_for_mood(upbeat.mood_id, include_children=False)
    assert sorted(t.track_id for t in child_tracks) == sorted(
        {shared_track.track_id, child_only_track.track_id}
    )

    session.close()
