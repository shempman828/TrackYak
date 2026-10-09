"""Tests for PublisherEditDialog: staged founders, create-mode retry, and field validation."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.db_helpers.add import AddToDB
from src.db.db_helpers.delete import DeleteDB
from src.db.db_helpers.get import GetFromDB
from src.db.db_helpers.update import UpdateDB
from src.db.db_tables.base import Base
import src.publisher.publisher_edit_dialog as edit_module
from src.publisher.publisher_edit_dialog import PublisherEditDialog


class _Controller:
    def __init__(self, session):
        self.get = GetFromDB(session)
        self.add = AddToDB(session)
        self.update = UpdateDB(session)
        self.delete = DeleteDB(session)


@pytest.fixture
def controller():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield _Controller(session)
    session.close()


@pytest.fixture
def warnings(monkeypatch):
    shown = []
    monkeypatch.setattr(edit_module.QMessageBox, "warning", lambda *a, **k: shown.append(a[2]))
    return shown


def test_new_founder_is_created_only_on_save(controller, warnings, qapp):
    dialog = PublisherEditDialog(controller)
    try:
        dialog.founder_edit.setText("Brand New Artist")
        dialog._add_founder()
        assert controller.get.get_all_entities("Artist") == []
        assert dialog.founders_list.item(0).text() == "Brand New Artist (new)"

        dialog.name_input.setText("Atlantic")
        dialog.validate()

        artists = controller.get.get_all_entities("Artist")
        assert [a.artist_name for a in artists] == ["Brand New Artist"]
        links = controller.get.get_all_entities("PublisherFounder", publisher_id=dialog.result_publisher.publisher_id)
        assert [link.artist_id for link in links] == [artists[0].artist_id]
    finally:
        dialog.deleteLater()


def test_known_artist_matches_case_insensitively(controller, warnings, qapp):
    controller.add.add_entity("Artist", artist_name="Ahmet Ertegun")
    dialog = PublisherEditDialog(controller)
    try:
        dialog.founder_edit.setText("ahmet ertegun")
        dialog._add_founder()
        assert dialog._founder_ids[0][1] == "Ahmet Ertegun"
        assert dialog._founder_ids[0][0] is not None
    finally:
        dialog.deleteLater()


def test_create_mode_retry_after_partial_failure_updates_instead_of_duplicating(controller, warnings, monkeypatch, qapp):
    from sqlalchemy.exc import SQLAlchemyError

    monkeypatch.setattr(edit_module.QMessageBox, "critical", lambda *a, **k: None)
    dialog = PublisherEditDialog(controller)
    try:
        dialog.name_input.setText("Atlantic")
        original = dialog._save_founders

        def _fail_once(publisher_id):
            dialog._save_founders = original
            raise SQLAlchemyError("boom")

        dialog._save_founders = _fail_once
        dialog.validate()
        dialog.validate()

        assert warnings == []
        assert [p.publisher_name for p in controller.get.get_all_entities("Publisher")] == ["Atlantic"]
    finally:
        dialog.deleteLater()


def test_founded_after_defunct_is_rejected(controller, warnings, qapp):
    dialog = PublisherEditDialog(controller)
    try:
        dialog.name_input.setText("Atlantic")
        dialog.begin_year_edit.setText("2000")
        dialog.end_year_edit.setText("1990")
        dialog.validate()
        assert len(warnings) == 1
        assert controller.get.get_all_entities("Publisher") == []
    finally:
        dialog.deleteLater()


def test_malformed_mbid_is_rejected(controller, warnings, qapp):
    dialog = PublisherEditDialog(controller)
    try:
        dialog.name_input.setText("Atlantic")
        dialog.mbid_input.setText("not-an-mbid")
        dialog.validate()
        assert len(warnings) == 1
        assert controller.get.get_all_entities("Publisher") == []

        dialog.mbid_input.setText("11111111-1111-1111-1111-111111111111")
        dialog.validate()
        assert controller.get.get_entity_object("Publisher", publisher_name="Atlantic").MBID == "11111111-1111-1111-1111-111111111111"
    finally:
        dialog.deleteLater()
