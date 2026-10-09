"""Tests for PublisherTreeWidget: drop cycle check, inline rename, data cycles, and filtering."""

from types import SimpleNamespace

from PySide6.QtCore import QPointF, Qt
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.db_helpers.get import GetFromDB
from src.db.db_tables.base import Base
from src.db.db_tables.publisher import Publisher
import src.publisher.publisher_tree as publisher_tree_module
from src.publisher.publisher_tree import MBID_LINKED, MBID_NOT_LINKED, PublisherTreeWidget, publisher_name


class _RecordingUpdate:
    def __init__(self):
        self.calls = []

    def update_entity(self, model_name, entity_id, **kwargs):
        self.calls.append((model_name, entity_id, kwargs))


class _Controller:
    def __init__(self, session):
        self.get = GetFromDB(session)
        self.update = _RecordingUpdate()


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _find(tree, name):
    from PySide6.QtWidgets import QTreeWidgetItemIterator

    iterator = QTreeWidgetItemIterator(tree)
    while iterator.value():
        if publisher_name(iterator.value()) == name:
            return iterator.value()
        iterator += 1
    raise AssertionError(name)


def _tree_with(session, monkeypatch):
    monkeypatch.setattr(publisher_tree_module, "show_status_message", lambda *a, **k: None)
    tree = PublisherTreeWidget(_Controller(session))
    tree.load_publishers()
    return tree


def test_drop_parent_onto_own_child_is_blocked(session, monkeypatch, qapp):
    major = Publisher(publisher_name="Major")
    imprint = Publisher(publisher_name="Imprint", parent=major)
    session.add_all([major, imprint])
    session.commit()
    tree = _tree_with(session, monkeypatch)
    try:
        tree.setCurrentItem(_find(tree, "Major"))
        monkeypatch.setattr(tree, "itemAt", lambda _pos: _find(tree, "Imprint"))
        tree.dropEvent(SimpleNamespace(position=lambda: QPointF(0, 0)))
        assert tree.controller.update.calls == []
    finally:
        tree.deleteLater()


def test_drop_grandchild_onto_grandparent_is_allowed(session, monkeypatch, qapp):
    major = Publisher(publisher_name="Major")
    imprint = Publisher(publisher_name="Imprint", parent=major)
    sub = Publisher(publisher_name="Sub", parent=imprint)
    session.add_all([major, imprint, sub])
    session.commit()
    tree = _tree_with(session, monkeypatch)
    try:
        tree.setCurrentItem(_find(tree, "Sub"))
        monkeypatch.setattr(tree, "itemAt", lambda _pos: _find(tree, "Major"))
        tree.dropEvent(SimpleNamespace(position=lambda: QPointF(0, 0)))
        assert tree.controller.update.calls == [("Publisher", sub.publisher_id, {"parent_id": major.publisher_id})]
    finally:
        tree.deleteLater()


def test_linked_marker_is_shown_but_not_saved_on_rename(session, monkeypatch, qapp):
    linked = Publisher(publisher_name="Atlantic", MBID="11111111-1111-1111-1111-111111111111")
    session.add(linked)
    session.commit()
    tree = _tree_with(session, monkeypatch)
    try:
        item = _find(tree, "Atlantic")
        assert item.text(0) == "Atlantic \U0001f517"
        assert item.data(0, Qt.EditRole) == "Atlantic"

        item.setData(0, Qt.EditRole, "  Atlantic Records ")

        assert tree.controller.update.calls == [("Publisher", linked.publisher_id, {"publisher_name": "Atlantic Records"})]
    finally:
        tree.deleteLater()


def test_blank_rename_is_rejected(session, monkeypatch, qapp):
    session.add(Publisher(publisher_name="Atlantic"))
    session.commit()
    tree = _tree_with(session, monkeypatch)
    try:
        _find(tree, "Atlantic").setData(0, Qt.EditRole, "   ")
        assert tree.controller.update.calls == []
    finally:
        tree.deleteLater()


def test_parent_cycle_in_data_keeps_every_publisher_visible(session, monkeypatch, qapp):
    a = Publisher(publisher_name="A")
    b = Publisher(publisher_name="B")
    session.add_all([a, b])
    session.commit()
    a.parent_id, b.parent_id = b.publisher_id, a.publisher_id
    session.commit()
    tree = _tree_with(session, monkeypatch)
    try:
        assert tree.filter_items("") == (2, 2)
        assert tree.calculate_recursive_album_counts([a, b]) == {a.publisher_id: 0, b.publisher_id: 0}
    finally:
        tree.deleteLater()


def test_filter_items_uses_keys_and_returns_counts(session, monkeypatch, qapp):
    major = Publisher(publisher_name="Major", MBID="11111111-1111-1111-1111-111111111111")
    imprint = Publisher(publisher_name="Imprint", parent=major)
    session.add_all([major, imprint, Publisher(publisher_name="Other")])
    session.commit()
    tree = _tree_with(session, monkeypatch)
    try:
        assert tree.filter_items("", MBID_LINKED) == (1, 3)
        # The linked parent stays visible as context for its matching child.
        assert tree.filter_items("", MBID_NOT_LINKED) == (3, 3)
        assert tree.filter_items("imp") == (2, 3)
        assert tree.filter_items("\U0001f517") == (0, 3)
    finally:
        tree.deleteLater()
