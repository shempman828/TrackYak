"""Regression test: find_orphan_artists() used to delete selected artists one
at a time (delete_entity(entity_id=...) in a loop), issuing a separate DB
commit per artist with no UI yielding in between -- freezing the app on a
large "Delete Unused Artists" batch. It must now issue a single batched
delete_entity(entity_ids=[...]) call instead of one delete_entity call per
selected artist. See src/artist/artist_view_dedup.py find_orphan_artists().
"""

import pytest
from PySide6.QtWidgets import QDialog, QWidget
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.artist.artist_delete_orphans import OrphanArtistDialog
from src.artist.artist_view_dedup import ArtistDedupMixin
from src.db.db_helpers.delete import DeleteDB
from src.db.db_helpers.get import GetFromDB
from src.db.db_tables.artist import Artist
from src.db.db_tables.base import Base


class _Controller:
    def __init__(self, session):
        self.get = GetFromDB(session)
        self.delete = DeleteDB(session)


class _ArtistDedupHost(QWidget, ArtistDedupMixin):
    def __init__(self, controller):
        super().__init__()
        self.controller = controller
        self.load_calls = 0

    def load_artists(self):
        self.load_calls += 1


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


def _make_orphan_artists(session, count):
    artists = [Artist(artist_name=f"Orphan {i}") for i in range(count)]
    session.add_all(artists)
    session.commit()
    return artists


def test_find_orphan_artists_batches_delete_into_one_commit(qapp, controller, monkeypatch):
    orphans = _make_orphan_artists(controller.get.session, 5)
    host = _ArtistDedupHost(controller)

    monkeypatch.setattr(OrphanArtistDialog, "exec_", lambda self: QDialog.Accepted)
    monkeypatch.setattr(
        OrphanArtistDialog,
        "selected_artist_ids",
        lambda self: [a.artist_id for a in orphans],
    )

    calls = []
    real_delete_entity = controller.delete.delete_entity

    def spying_delete_entity(*args, **kwargs):
        calls.append((args, kwargs))
        return real_delete_entity(*args, **kwargs)

    monkeypatch.setattr(controller.delete, "delete_entity", spying_delete_entity)

    host.find_orphan_artists()

    assert len(calls) == 1, "delete_entity must be called once for the whole batch, not per artist"
    args, kwargs = calls[0]
    assert kwargs.get("entity_ids") == [a.artist_id for a in orphans]
    assert "entity_id" not in kwargs

    remaining = controller.get.get_all_entities("Artist")
    assert remaining == []
    assert host.load_calls == 1
