"""Mood view and dialog: tree build, recursive track counts and loading,
delete confirmations, and the Remove from This Mood action.
"""

from types import SimpleNamespace

from PySide6.QtWidgets import QMenu, QMessageBox, QWidget
import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from src.db.db_helpers.delete import DeleteDB
from src.db.db_helpers.get import GetFromDB
from src.db.db_tables.base import Base
from src.db.db_tables.mood import Mood, MoodTrackAssociation
from src.db.db_tables.track import Track
from src.mood import mood_tracks
from src.mood.mood_dialog import MoodDialog
from src.mood.mood_track_view_context import add_remove_from_mood_action
from src.mood.mood_tracks import MoodTracksWindow
from src.mood.mood_view import MoodView

# ---------------------------------------------------------------------------
# Regression test: MoodView must support deleting several selected moods at
# ---------------------------------------------------------------------------


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


# ---- test_mood_view_null_name_sort.py ----------------------------------------
# Regression test: build_mood_tree's sort keys must tolerate a Mood row with
# mood_name=None (the column is nullable) instead of crashing the whole view.
class _Controller_nn:
    def __init__(self, session):
        self.get = GetFromDB(session)


def test_build_mood_tree_tolerates_null_mood_name(qapp):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    controller = _Controller_nn(session)

    session.add_all([Mood(mood_name="Happy"), Mood(mood_name=None)])
    session.commit()

    view = MoodView(controller)  # must not raise
    assert view.mood_tree.topLevelItemCount() == 2

    view.flat_view = True
    view.build_mood_tree()  # must not raise in the flat-view sort path either
    assert view.mood_tree.topLevelItemCount() == 2

    session.close()


# ---- test_mood_view_delete_confirm_default.py --------------------------------
# Regression test: the delete-mood confirmations must default to No, matching
# the equivalent confirmation in mood_dialog.py, so pressing Enter doesn't
# accidentally trigger the destructive delete.
def test_delete_selected_mood_confirmation_defaults_to_no(qapp, monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    controller = _Controller_md(session)

    mood = Mood(mood_name="Happy")
    session.add(mood)
    session.commit()

    view = MoodView(controller)
    view.current_mood_id = mood.mood_id

    captured = {}

    def fake_question(*args, **_kwargs):
        captured["default"] = args[4]
        return QMessageBox.No

    monkeypatch.setattr(QMessageBox, "question", fake_question)

    view.delete_selected_mood()

    assert captured["default"] == QMessageBox.No
    # Declined delete -- mood must still exist.
    assert session.query(Mood).count() == 1

    session.close()


def test_delete_selected_moods_confirmation_defaults_to_no(qapp, monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    controller = _Controller_md(session)

    happy = Mood(mood_name="Happy")
    sad = Mood(mood_name="Sad")
    session.add_all([happy, sad])
    session.commit()

    view = MoodView(controller)

    captured = {}

    def fake_question(*args, **_kwargs):
        captured["default"] = args[4]
        return QMessageBox.No

    monkeypatch.setattr(QMessageBox, "question", fake_question)

    items = [it for it in _tree_items(view) if it.data(0, 0x0100) in (happy.mood_id, sad.mood_id)]
    view.delete_selected_moods(items)

    assert captured["default"] == QMessageBox.No
    # Declined delete -- both moods must still exist.
    assert session.query(Mood).count() == 2

    session.close()


# ---------------------------------------------------------------------------
# Tests for MoodTracksWindow.load_tracks (src/mood/mood_tracks.py): direct
# ---------------------------------------------------------------------------


class _Controller:
    def __init__(self, session):
        self.get = GetFromDB(session)


class _StubTrackView(QWidget):
    def __init__(self, controller, tracks, title):
        super().__init__()
        self.loaded = None

    def load_data(self, tracks):
        self.loaded = tracks


@pytest.fixture
def setup(qapp, monkeypatch):
    monkeypatch.setattr(mood_tracks, "BaseTrackView", _StubTrackView)
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    # Match the app session: query_entities commits after each read.
    session = sessionmaker(bind=engine, expire_on_commit=False)()

    happy = Mood(mood_name="Happy")
    session.add(happy)
    session.flush()
    joyful = Mood(mood_name="Joyful", parent_id=happy.mood_id)
    session.add(joyful)
    session.flush()
    ecstatic = Mood(mood_name="Ecstatic", parent_id=joyful.mood_id)
    sad = Mood(mood_name="Sad")
    session.add_all([ecstatic, sad])
    session.flush()

    tracks = {name: Track(track_name=name) for name in ("own", "child", "grandchild", "shared", "unrelated")}
    session.add_all(tracks.values())
    session.flush()
    for mood, name in [(happy, "own"), (joyful, "child"), (ecstatic, "grandchild"), (happy, "shared"), (ecstatic, "shared"), (sad, "unrelated")]:
        session.add(MoodTrackAssociation(mood_id=mood.mood_id, track_id=tracks[name].track_id))
    session.commit()

    selects = []
    event.listen(engine, "before_cursor_execute", lambda _conn, _cur, stmt, *_a: selects.append(stmt) if stmt.lstrip().upper().startswith("SELECT") else None)
    return _Controller(session), happy, selects


def _names(window):
    return sorted(t.track_name for t in window.base_track_view.loaded)


def test_direct_mode_loads_only_own_tracks_in_one_query(setup):
    controller, happy, selects = setup

    window = MoodTracksWindow(controller, happy)

    assert _names(window) == ["own", "shared"]
    assert len(selects) == 1
    assert window.track_count_label.text() == "Found 2 tracks"


def test_recursive_mode_includes_all_descendants_deduped_in_two_queries(setup):
    controller, happy, selects = setup
    window = MoodTracksWindow(controller, happy)
    selects.clear()

    window.toggle_recursive()

    assert _names(window) == ["child", "grandchild", "own", "shared"]
    assert len(selects) == 2  # one Mood query + one Track query
    assert window.track_count_label.text() == "Found 4 tracks (including all sub-moods)"


# ---------------------------------------------------------------------------
# Tests for src/mood/mood_dialog.py.
# ---------------------------------------------------------------------------


class _Track:
    def __init__(self, track_id):
        self.track_id = track_id
        self.track_name = f"Track {track_id}"
        # BaseTrackView now renders the full TRACK_FIELDS column set (shared
        # with TrackView) rather than a hardcoded few, so a stand-in track
        # needs the attributes those columns read even when unset.
        self.album_id = None
        self.disc_id = None
        self.artist_roles = []


class _Association:
    def __init__(self, track):
        self.track = track


class _StubGet:
    def __init__(self, associations):
        self._associations = associations

    def get_all_entities(self, model_name, **kwargs):
        if model_name == "MoodTrackAssociation":
            return self._associations
        return []


class _StubController:
    def __init__(self, associations=()):
        self.get = _StubGet(list(associations))


def _mood(mood_id=1, name="Happy"):
    return SimpleNamespace(mood_id=mood_id, mood_name=name, mood_description="")


def test_load_associated_tracks_shows_success_label_not_error(qapp):
    """Regression: load_associated_tracks used to always overwrite the
    success label with "Error loading tracks" because that line sat
    outside the try/except block, so it ran even when loading succeeded.
    """
    tracks = [_Track(1), _Track(2)]
    controller = _StubController([_Association(t) for t in tracks])

    dlg = MoodDialog(mood_data=_mood(), controller=controller)
    try:
        assert dlg.track_view.info_label.text() == "Showing 2 tracks"
    finally:
        dlg.deleteLater()


def test_remove_from_mood_action_added_once_across_reloads(qapp):
    """Regression: enhance_track_view_context_menu runs on every tracks
    reload (init plus each recursive-mode toggle); the action must not be
    duplicated on the shared context menu."""
    tracks = [_Track(1)]
    controller = _StubController([_Association(t) for t in tracks])

    dlg = MoodDialog(mood_data=_mood(), controller=controller)
    try:
        dlg.toggle_recursive_mode()
        dlg.toggle_recursive_mode()

        actions = [action.text() for action in dlg.track_view.context_menu.actions()]
        assert actions.count("Remove from This Mood") == 1
    finally:
        dlg.deleteLater()


def test_ok_button_disabled_until_name_entered(qapp):
    controller = _StubController()
    dlg = MoodDialog(controller=controller)
    try:
        ok_button = dlg._ok_button
        assert ok_button.isEnabled() is False

        dlg.mood_name_edit.setText("   ")
        assert ok_button.isEnabled() is False

        dlg.mood_name_edit.setText("Chill")
        assert ok_button.isEnabled() is True

        dlg.mood_name_edit.setText("")
        assert ok_button.isEnabled() is False
    finally:
        dlg.deleteLater()


# ---------------------------------------------------------------------------
# Tests for src/mood/mood_track_view_context.py.
# ---------------------------------------------------------------------------


def test_add_remove_from_mood_action_is_added_once_and_wired(qapp):
    # MoodDialog calls this on every tracks reload, so repeated calls must not add duplicates.
    add_remove_from_mood_action(SimpleNamespace(), lambda: None)  # no context menu: no-op

    menu = QMenu()
    track_view = SimpleNamespace(context_menu=menu)
    calls = []
    for _ in range(3):
        add_remove_from_mood_action(track_view, lambda: calls.append(1))

    assert [action.text() for action in menu.actions()].count("Remove from This Mood") == 1
    menu.actions()[-1].trigger()
    assert calls == [1]
