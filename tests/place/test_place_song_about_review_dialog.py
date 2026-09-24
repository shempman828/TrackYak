"""Tests for src/place/place_song_about_review_dialog.py: approve/change/
reject on a queued "Song About" place detection, grouped by place name so
one decision resolves every currently-queued track for it at once, and is
remembered for future detections of the same name.
"""

from PySide6.QtWidgets import QDialog
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.db_helpers.add import AddToDB
from src.db.db_helpers.get import GetFromDB
from src.db.db_tables.base import Base
from src.db.db_tables.place import Place, PlaceAssociation
from src.db.db_tables.place_association_type import PlaceAssociationType
from src.db.db_tables.track import Track
from src.place import place_song_about_review_dialog as dialog_module, place_song_about_store as store
from src.place.place_song_about_review_dialog import PlaceSongAboutReviewDialog


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


def _make_track(session, name="Test Track"):
    track = Track(track_name=name)
    session.add(track)
    session.commit()
    return track


def _make_place(session, name):
    place = Place(place_name=name)
    session.add(place)
    session.commit()
    return place


def _associations_for(session, track_id):
    return session.query(PlaceAssociation).filter_by(entity_id=track_id, entity_type="Track").all()


def test_refresh_groups_queue_entries_by_place_name(qapp, controller):
    store.enqueue([{"track_id": 1, "place_name": "Bath", "place_id": 10}, {"track_id": 2, "place_name": "Bath", "place_id": 10}, {"track_id": 3, "place_name": "Paris", "place_id": 20}])

    dlg = PlaceSongAboutReviewDialog(controller)
    try:
        assert dlg._table.rowCount() == 2
        rows = {dlg._table.item(r, 0).text(): dlg._table.item(r, 2).text() for r in range(dlg._table.rowCount())}
        assert rows == {"Bath": "2", "Paris": "1"}
    finally:
        dlg.deleteLater()


def test_refresh_flags_an_ambiguous_place_name_with_a_live_count(qapp, session, controller):
    """Two places sharing a name (e.g. "Greene County" in two states) must
    surface a count in the Match column -- computed fresh from the library
    at dialog-open time, not frozen at detection time."""
    ohio_greene = _make_place(session, "Greene County")
    _make_place(session, "Greene County")  # second, unrelated county, same name
    track = _make_track(session)
    store.enqueue([{"track_id": track.track_id, "place_name": "Greene County", "place_id": ohio_greene.place_id}])

    dlg = PlaceSongAboutReviewDialog(controller)
    try:
        match_text = dlg._table.item(0, 1).text()
        assert "2" in match_text
    finally:
        dlg.deleteLater()


def test_refresh_shows_no_warning_for_an_unambiguous_place_name(qapp, session, controller):
    place = _make_place(session, "Bath")
    track = _make_track(session)
    store.enqueue([{"track_id": track.track_id, "place_name": "Bath", "place_id": place.place_id}])

    dlg = PlaceSongAboutReviewDialog(controller)
    try:
        match_text = dlg._table.item(0, 1).text()
        assert "⚠" not in match_text
    finally:
        dlg.deleteLater()


def test_approve_writes_association_for_every_queued_track_and_saves_decision(qapp, session, controller):
    place = _make_place(session, "Bath")
    t1 = _make_track(session, "Track 1")
    t2 = _make_track(session, "Track 2")
    store.enqueue([{"track_id": t1.track_id, "place_name": "Bath", "place_id": place.place_id}, {"track_id": t2.track_id, "place_name": "Bath", "place_id": place.place_id}])

    dlg = PlaceSongAboutReviewDialog(controller)
    try:
        dlg._approve("Bath")

        assert len(_associations_for(session, t1.track_id)) == 1
        assert len(_associations_for(session, t2.track_id)) == 1
        assert store.load_decisions()["Bath"] == {"decision": store.DECISION_APPROVED}
        assert store.load_queue() == []
        assert dlg._table.rowCount() == 0
    finally:
        dlg.deleteLater()


def test_approve_uses_song_about_association_type(qapp, session, controller):
    place = _make_place(session, "Bath")
    track = _make_track(session)
    store.enqueue([{"track_id": track.track_id, "place_name": "Bath", "place_id": place.place_id}])

    dlg = PlaceSongAboutReviewDialog(controller)
    try:
        dlg._approve("Bath")

        assoc = _associations_for(session, track.track_id)[0]
        song_about = session.query(PlaceAssociationType).filter_by(type_name="Song About").one()
        assert assoc.association_type_id == song_about.association_type_id
    finally:
        dlg.deleteLater()


def test_approve_does_not_duplicate_an_existing_association(qapp, session, controller):
    place = _make_place(session, "Bath")
    track = _make_track(session)
    session.add(PlaceAssociation(place_id=place.place_id, entity_id=track.track_id, entity_type="Track"))
    session.commit()
    store.enqueue([{"track_id": track.track_id, "place_name": "Bath", "place_id": place.place_id}])

    dlg = PlaceSongAboutReviewDialog(controller)
    try:
        dlg._approve("Bath")

        assert len(_associations_for(session, track.track_id)) == 1
    finally:
        dlg.deleteLater()


def test_reject_removes_from_queue_without_writing_and_saves_decision(qapp, session, controller):
    place = _make_place(session, "England")
    track = _make_track(session)
    store.enqueue([{"track_id": track.track_id, "place_name": "England", "place_id": place.place_id}])

    dlg = PlaceSongAboutReviewDialog(controller)
    try:
        dlg._reject("England")

        assert _associations_for(session, track.track_id) == []
        assert store.load_decisions()["England"] == {"decision": store.DECISION_REJECTED}
        assert store.load_queue() == []
    finally:
        dlg.deleteLater()


class _StubChangeDialog:
    """Stand-in for _ChangePlaceDialog: skips the real modal popup and
    hands back a pre-picked replacement place."""

    def __init__(self, replacement_place):
        self._replacement_place = replacement_place

    def exec_(self):
        return QDialog.Accepted

    def resolve_place(self):
        return self._replacement_place


def test_change_writes_to_the_replacement_place_and_saves_remap_decision(qapp, session, controller, monkeypatch):
    _make_place(session, "Kingston")
    jamaica_kingston = _make_place(session, "Kingston, Jamaica")
    track = _make_track(session)
    store.enqueue([{"track_id": track.track_id, "place_name": "Kingston", "place_id": 999}])

    monkeypatch.setattr(dialog_module, "_ChangePlaceDialog", lambda controller, name, parent: _StubChangeDialog(jamaica_kingston))

    dlg = PlaceSongAboutReviewDialog(controller)
    try:
        dlg._change("Kingston")

        assocs = _associations_for(session, track.track_id)
        assert len(assocs) == 1
        assert assocs[0].place_id == jamaica_kingston.place_id
        decision = store.load_decisions()["Kingston"]
        assert decision["decision"] == store.DECISION_REMAPPED
        assert decision["place_id"] == jamaica_kingston.place_id
        assert store.load_queue() == []
    finally:
        dlg.deleteLater()


def test_change_cancelled_leaves_queue_and_decisions_untouched(qapp, session, controller, monkeypatch):
    _make_place(session, "Kingston")
    track = _make_track(session)
    store.enqueue([{"track_id": track.track_id, "place_name": "Kingston", "place_id": 999}])

    class _CancelledDialog:
        def exec_(self):
            return QDialog.Rejected

        def resolve_place(self):
            raise AssertionError("resolve_place() must not be called when the dialog is cancelled")

    monkeypatch.setattr(dialog_module, "_ChangePlaceDialog", lambda controller, name, parent: _CancelledDialog())

    dlg = PlaceSongAboutReviewDialog(controller)
    try:
        dlg._change("Kingston")

        assert _associations_for(session, track.track_id) == []
        assert store.load_decisions() == {}
        assert len(store.load_queue()) == 1
    finally:
        dlg.deleteLater()
