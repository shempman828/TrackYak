"""Regression test: MoodView must support deleting several selected moods at
once. Previously the context menu only offered a delete action for a single
selection, and MoodView.delete_selected_mood only removed self.current_mood_id,
so a multi-selection could not be deleted from the moods view.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.db_helpers.delete import DeleteDB
from src.db.db_helpers.get import GetFromDB
from src.db.db_tables.base import Base
from src.db.db_tables.mood import Mood, MoodTrackAssociation
from src.db.db_tables.track import Track
from src.mood.moods_view import MoodView


# ---- test_mood_view_multi_delete.py ------------------------------------------
class _Controller_md:
    def __init__(self, session):
        self.get = GetFromDB(session)
        self.delete = DeleteDB(session)


def test_delete_selected_moods_removes_all_selected(qapp, monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    controller = _Controller_md(session)

    happy = Mood(mood_name="Happy")
    sad = Mood(mood_name="Sad")
    calm = Mood(mood_name="Calm")
    session.add_all([happy, sad, calm])
    session.flush()

    track = Track(track_name="Song")
    session.add(track)
    session.flush()
    session.add(MoodTrackAssociation(mood_id=happy.mood_id, track_id=track.track_id))
    session.commit()

    happy_id, sad_id, calm_id = happy.mood_id, sad.mood_id, calm.mood_id

    view = MoodView(controller)

    # Auto-confirm the QMessageBox prompt.
    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)

    emitted = []
    view.mood_deleted.connect(emitted.append)

    items = [it for it in _tree_items(view) if it.data(0, 0x0100) in (happy_id, sad_id)]
    assert len(items) == 2
    view.delete_selected_moods(items)

    remaining = {m.mood_id for m in session.query(Mood).all()}
    assert remaining == {calm_id}
    assert sorted(emitted) == sorted([happy_id, sad_id])
    # Association for the deleted mood is gone too.
    assert session.query(MoodTrackAssociation).count() == 0


def _tree_items(view):
    """Yield every QTreeWidgetItem in the mood tree."""
    stack = [view.mood_tree.topLevelItem(i) for i in range(view.mood_tree.topLevelItemCount())]
    while stack:
        item = stack.pop()
        yield item
        for i in range(item.childCount()):
            stack.append(item.child(i))


# ---- test_mood_view_recursive_counts.py --------------------------------------
# Regression test: MoodView.get_track_counts_for_all_moods must return a
# deduped recursive (own + descendants) count alongside the own/direct count,
# rather than a single flat direct-only count with no rollup for sub-moods.
# See src/genre/genre_view.py's GenreLoaderWorker for the identical
# own/recursive dedup pattern this mirrors.
class _Controller_rc:
    def __init__(self, session):
        self.get = GetFromDB(session)


def test_recursive_track_count_dedupes_track_tagged_at_multiple_levels(qapp):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    controller = _Controller_rc(session)

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
            MoodTrackAssociation(mood_id=upbeat.mood_id, track_id=shared_track.track_id),
            MoodTrackAssociation(mood_id=upbeat.mood_id, track_id=child_only_track.track_id),
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


# ---- test_mood_view_recursive_track_dedup.py ---------------------------------
# Regression test: MoodView.get_tracks_for_mood(include_children=True) must
# return each physical track once, even when that track is tagged at both a
# parent mood and one of its descendant moods. Before the fix the recursive
# branch appended ``association.track`` with no cross-mood dedup, so a track
# tagged at two levels showed up twice in the mood view's track list.
class _Controller_rtd:
    def __init__(self, session):
        self.get = GetFromDB(session)


def test_recursive_get_tracks_for_mood_dedupes_multilevel_track(qapp):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    controller = _Controller_rtd(session)

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
            MoodTrackAssociation(mood_id=upbeat.mood_id, track_id=child_only_track.track_id),
        ]
    )
    session.commit()

    view = MoodView(controller)

    tracks = view.get_tracks_for_mood(happy.mood_id, include_children=True)

    track_ids = [t.track_id for t in tracks]
    # Two unique tracks, each listed once -- not shared_track twice.
    assert sorted(track_ids) == sorted({shared_track.track_id, child_only_track.track_id})
    assert len(track_ids) == len(set(track_ids))

    # Non-recursive path for the child is unaffected.
    child_tracks = view.get_tracks_for_mood(upbeat.mood_id, include_children=False)
    assert sorted(t.track_id for t in child_tracks) == sorted(
        {shared_track.track_id, child_only_track.track_id}
    )

    session.close()
