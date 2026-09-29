"""Regression tests for src/db/db_session_sync.py.

Bug: `Session` is a scoped_session, so a worker thread commits through its own
session and `_commit()`'s `expire_all()` never reaches the main thread's one.
Objects the main thread already held (views keep strong references) kept their
pre-write values -- after File Organization moved files on a worker, artwork
reconcile wrote to the old, missing paths ("not writable").
"""

import threading

import pytest
from sqlalchemy import Column, Integer, String, create_engine, select, text
from sqlalchemy.orm import declarative_base, scoped_session, sessionmaker

from src.db import db_session_sync

_Base = declarative_base()


class _Row(_Base):
    __tablename__ = "rows"
    row_id = Column(Integer, primary_key=True)
    value = Column(String)


@pytest.fixture
def registry(tmp_path, monkeypatch):
    # A file database (not :memory:) so each thread gets its own pooled
    # connection, like the real app engine.
    engine = create_engine(f"sqlite:///{tmp_path / 'sync.db'}", connect_args={"check_same_thread": False})
    _Base.metadata.create_all(engine)
    registry = scoped_session(sessionmaker(bind=engine, expire_on_commit=False))
    registry.add(_Row(row_id=1, value="old"))
    registry.commit()

    # install() rebinds module state that the app engine also uses -- restore it.
    for name in ("_session_registry", "_stale", "_notifier"):
        monkeypatch.setattr(db_session_sync, name, getattr(db_session_sync, name))
    monkeypatch.setattr(db_session_sync, "_stale", False)
    db_session_sync.install(engine, registry)
    yield registry
    registry.remove()
    engine.dispose()


@pytest.fixture
def notified(monkeypatch):
    calls = []
    monkeypatch.setattr(db_session_sync, "_notifier", lambda: calls.append(threading.current_thread()))
    return calls


def _in_worker(registry, fn):
    def body():
        try:
            fn(registry())
        finally:
            registry.remove()

    t = threading.Thread(target=body)
    t.start()
    t.join()


def _set_value(value):
    def write(session):
        session.get(_Row, 1).value = value
        session.commit()

    return write


def test_worker_write_refreshes_objects_held_by_main_thread(registry, notified):
    held = registry.get(_Row, 1)

    _in_worker(registry, _set_value("new"))

    assert db_session_sync.is_main_session_stale()
    assert len(notified) == 1
    assert notified[0] is not threading.main_thread()
    # Still stale until the main thread consumes the flag -- the original bug.
    assert registry.scalars(select(_Row)).one().value == "old"

    assert db_session_sync.expire_main_session_if_stale() is True

    assert held.value == "new"
    assert not db_session_sync.is_main_session_stale()


def test_raw_sql_commit_on_worker_is_detected(registry, notified):
    """Writes outside BaseDBHelper._commit() (plain session.commit() after raw SQL) must count too."""
    held = registry.get(_Row, 1)

    def raw_write(session):
        session.execute(text("UPDATE rows SET value = 'raw' WHERE row_id = 1"))
        session.commit()

    _in_worker(registry, raw_write)
    db_session_sync.expire_main_session_if_stale()

    assert len(notified) == 1
    assert held.value == "raw"


def test_read_only_worker_commit_does_not_mark_stale(registry, notified):
    """get.py's read helpers commit after every fetch; those must not expire the main session."""

    def read(session):
        session.get(_Row, 1)
        session.commit()

    _in_worker(registry, read)

    assert not db_session_sync.is_main_session_stale()
    assert notified == []
    assert db_session_sync.expire_main_session_if_stale() is False


def test_rolled_back_worker_write_does_not_mark_stale(registry, notified):
    def rolled_back(session):
        session.get(_Row, 1).value = "discarded"
        session.flush()
        session.rollback()
        session.get(_Row, 1)
        session.commit()

    _in_worker(registry, rolled_back)

    assert not db_session_sync.is_main_session_stale()
    assert notified == []


def test_main_thread_write_does_not_mark_stale(registry, notified):
    registry.get(_Row, 1).value = "main"
    registry.commit()

    assert not db_session_sync.is_main_session_stale()
    assert notified == []


def test_batched_worker_commits_notify_once(registry, notified):
    def batches(session):
        for value in ("a", "b", "c"):
            _set_value(value)(session)

    _in_worker(registry, batches)

    assert len(notified) == 1


def test_expire_keeps_uncommitted_main_thread_edits(registry, notified):
    other = _Row(row_id=2, value="other")
    registry.add(other)
    registry.commit()
    held = registry.get(_Row, 1)

    def write_other(session):
        session.get(_Row, 2).value = "other-new"
        session.commit()

    _in_worker(registry, write_other)
    held.value = "editing"  # pending, not yet committed on the main thread

    db_session_sync.expire_main_session_if_stale()

    assert held.value == "editing"
    assert other.value == "other-new"


def test_expire_refuses_to_run_off_the_main_thread(registry):
    errors = []

    def call():
        try:
            db_session_sync.expire_main_session_if_stale()
        except RuntimeError as e:
            errors.append(e)

    t = threading.Thread(target=call)
    t.start()
    t.join()

    assert len(errors) == 1
