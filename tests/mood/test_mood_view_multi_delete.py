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


class _Controller:
    def __init__(self, session):
        self.get = GetFromDB(session)
        self.delete = DeleteDB(session)


def test_delete_selected_moods_removes_all_selected(qapp, monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    controller = _Controller(session)

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
