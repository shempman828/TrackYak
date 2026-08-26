"""Regression test: _delete_empty_albums() used to delete empty albums one at
a time (delete_entity(album.album_id) in a loop), issuing a separate DB
commit per album with no UI yielding in between -- freezing the app on a
large "Delete Empty Albums" batch. It must now issue a single batched
delete_entity(entity_ids=[...]) call instead of one delete_entity call per
empty album. See src/album/album_context_menu.py _delete_empty_albums().
"""

import pytest
from PySide6.QtWidgets import QDialog, QWidget
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.album.album_context_menu import AlbumContextMenuMixin
from src.album.album_delete_dialog import DeleteEmptyAlbumsDialog
from src.db.db_helpers.delete import DeleteDB
from src.db.db_helpers.get import GetFromDB
from src.db.db_tables.album import Album
from src.db.db_tables.base import Base


class _Controller:
    def __init__(self, session):
        self.get = GetFromDB(session)
        self.delete = DeleteDB(session)


class _AlbumDeleteHost(QWidget, AlbumContextMenuMixin):
    def __init__(self, controller):
        super().__init__()
        self.controller = controller
        self.load_calls = 0

    def load_albums(self):
        self.load_calls += 1

    @staticmethod
    def _get_track_count(album):
        return len(album.tracks) if album.tracks else 0


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


@pytest.fixture
def controller(session):
    return _Controller(session)


def _make_empty_albums(session, count):
    albums = [Album(album_name=f"Empty Album {i}") for i in range(count)]
    session.add_all(albums)
    session.commit()
    return albums


def test_delete_empty_albums_batches_delete_into_one_commit(qapp, controller, monkeypatch):
    albums = _make_empty_albums(controller.get.session, 5)
    host = _AlbumDeleteHost(controller)

    monkeypatch.setattr(DeleteEmptyAlbumsDialog, "exec_", lambda self: QDialog.Accepted)

    calls = []
    real_delete_entity = controller.delete.delete_entity

    def spying_delete_entity(*args, **kwargs):
        calls.append((args, kwargs))
        return real_delete_entity(*args, **kwargs)

    monkeypatch.setattr(controller.delete, "delete_entity", spying_delete_entity)

    host._delete_empty_albums()

    assert len(calls) == 1, "delete_entity must be called once for the whole batch, not per album"
    args, kwargs = calls[0]
    assert kwargs.get("entity_ids") == [a.album_id for a in albums]
    assert "entity_id" not in kwargs

    remaining = controller.get.get_all_entities("Album")
    assert remaining == []
    assert host.load_calls == 1
