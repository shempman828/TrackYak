"""Alias-aware resolution for the track editor's manual "Add role" field
(docs/specs/split_and_merge_aliases.md's Non-goals section flagged
RolesTab._resolve_artist as exact-match-only, no ArtistAlias fallback).
Typing an aliased name should resolve to the canonical artist instead of
creating a duplicate, same as the file-tag import path
(tests/artist/test_artist_resolution.py).

RolesTab._resolve_artist only touches self.controller/self._artist_search,
so it's exercised directly on a lightweight stand-in rather than
constructing the real Qt widget (same technique as
tests/track/test_track_edit_genres_split_alias.py).
"""

from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.db_helpers.add import AddToDB
from src.db.db_helpers.get import GetFromDB
from src.db.db_tables.artist import Artist, ArtistAlias
from src.db.db_tables.base import Base
from src.track.track_edit_roles import RolesTab


class _Controller:
    def __init__(self, session):
        self.get = GetFromDB(session)
        self.add = AddToDB(session)


@pytest.fixture
def controller():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield _Controller(session)
    session.close()


class _StubArtistSearch:
    def __init__(self):
        self.indexed = []

    def add_to_index(self, name, artist_id):
        self.indexed.append((name, artist_id))


def _tab_stand_in(controller):
    return SimpleNamespace(controller=controller, _artist_search=_StubArtistSearch())


def test_resolve_artist_uses_alias_instead_of_creating_duplicate(controller):
    session = controller.get.session
    canonical = Artist(artist_name="Ahmet Ertegun")
    session.add(canonical)
    session.commit()
    session.add(ArtistAlias(artist_id=canonical.artist_id, alias_name="A. Ertegun"))
    session.commit()

    tab = _tab_stand_in(controller)
    artist = RolesTab._resolve_artist(tab, "A. Ertegun")

    assert artist.artist_id == canonical.artist_id
    assert session.query(Artist).count() == 1
    assert tab._artist_search.indexed == [("Ahmet Ertegun", canonical.artist_id)]


def test_resolve_artist_creates_new_artist_when_no_match(controller):
    session = controller.get.session

    tab = _tab_stand_in(controller)
    artist = RolesTab._resolve_artist(tab, "Brand New Artist")

    assert artist is not None
    assert artist.artist_name == "Brand New Artist"
    assert session.query(Artist).count() == 1


def test_resolve_artist_with_matched_id_bypasses_name_lookup(controller):
    session = controller.get.session
    existing = Artist(artist_name="Some Artist")
    session.add(existing)
    session.commit()

    tab = _tab_stand_in(controller)
    artist = RolesTab._resolve_artist(tab, "irrelevant text", matched_id=existing.artist_id)

    assert artist.artist_id == existing.artist_id
