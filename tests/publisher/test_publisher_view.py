"""Tests for PublisherView: delete reparenting and the duplicate-scan cancel path."""

from types import SimpleNamespace

from PySide6.QtCore import QObject, Signal
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.db_helpers.get import GetFromDB
from src.db.db_tables.base import Base
from src.db.db_tables.publisher import Publisher
import src.publisher.publisher_view as publisher_view_module
from src.publisher.publisher_view import PublisherView, plan_child_reparenting


def _pub(publisher_id, parent_id=None):
    return SimpleNamespace(publisher_id=publisher_id, parent_id=parent_id)


def test_children_of_deleted_publisher_move_to_its_parent():
    publishers = [_pub(1), _pub(2, 1), _pub(3, 2), _pub(4, 2)]
    assert plan_child_reparenting(publishers, {2}) == {3: 1, 4: 1}


def test_children_skip_ancestors_that_are_also_deleted():
    publishers = [_pub(1), _pub(2, 1), _pub(3, 2), _pub(4, 3)]
    assert plan_child_reparenting(publishers, {2, 3}) == {4: 1}


def test_children_become_roots_when_whole_chain_is_deleted():
    publishers = [_pub(1), _pub(2, 1)]
    assert plan_child_reparenting(publishers, {1}) == {2: None}


def test_deleted_cycle_does_not_loop():
    publishers = [_pub(1, 2), _pub(2, 1), _pub(3, 1)]
    assert plan_child_reparenting(publishers, {1, 2}) == {3: None}


class _StubScanWorker(QObject):
    """Stands in for PublisherFuzzyScanWorker; start() finishes after a cancel."""

    progress = Signal(int, int)
    finished = Signal(list)
    error = Signal(str)

    def __init__(self, entries, threshold):
        super().__init__()
        self.entries = entries
        self.is_cancelled = False

    def request_cancel(self):
        self.is_cancelled = True

    def isRunning(self):
        return False

    def start(self):
        self.request_cancel()
        self.finished.emit([(self.entries[0][0], self.entries[1][0], 95)])


@pytest.fixture
def view(monkeypatch, qapp):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add_all([Publisher(publisher_name="Sony"), Publisher(publisher_name="Sonny")])
    session.commit()
    view = PublisherView(SimpleNamespace(get=GetFromDB(session)))
    yield view
    view.deleteLater()
    session.close()


def test_cancelled_scan_does_not_open_review_dialog(view, monkeypatch):
    opened = []
    monkeypatch.setattr(publisher_view_module, "PublisherFuzzyScanWorker", _StubScanWorker)
    monkeypatch.setattr(publisher_view_module, "PublisherFuzzyMatchDialog", lambda *a, **k: opened.append(a))

    view.find_fuzzy_matches()

    assert opened == []


def test_search_filter_is_debounced(view):
    view.search_bar.setText("Sonn")
    assert view.count_label.text() == "2 publishers"
    view._search_timer.timeout.emit()
    assert view.count_label.text() == "1 of 2 publishers"
