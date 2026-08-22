"""Regression coverage for AlbumsTab._resolve_or_create_artist
(src/track/track_edit_album.py): same MBID/name-match ordering as the
other MB-import resolvers -- MBID match first, then a name match backfills
the MBID only if the row has none yet, and a name match whose row already
carries a *different* MBID is ignored (not reused) so a new Artist gets
created instead of merging two people MusicBrainz considers distinct.
"""

from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.db_helpers.add import AddToDB
from src.db.db_helpers.get import GetFromDB
from src.db.db_helpers.update import UpdateDB
from src.db.db_tables.base import Base
from src.track.track_edit_album import AlbumsTab


@pytest.fixture
def controller():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield SimpleNamespace(
        get=GetFromDB(session),
        add=AddToDB(session),
        update=UpdateDB(session),
    )
    session.close()


def _tab(controller):
    tab = AlbumsTab.__new__(AlbumsTab)
    tab.controller = controller
    return tab


def test_mbid_match_is_used_as_is(controller):
    existing = controller.add.add_entity(
        "Artist", artist_name="Some Other Name", MBID="mbid-new"
    )

    artist = _tab(controller)._resolve_or_create_artist("mbid-new", "John Smith")

    assert artist.artist_id == existing.artist_id


def test_name_match_with_no_mbid_backfills(controller):
    existing = controller.add.add_entity("Artist", artist_name="John Smith", MBID=None)

    artist = _tab(controller)._resolve_or_create_artist("mbid-new", "John Smith")

    assert artist.artist_id == existing.artist_id
    assert artist.MBID == "mbid-new"


def test_name_match_with_conflicting_mbid_creates_new_artist(controller):
    conflicting = controller.add.add_entity(
        "Artist", artist_name="John Smith", MBID="mbid-different"
    )

    artist = _tab(controller)._resolve_or_create_artist("mbid-new", "John Smith")

    assert artist.artist_id != conflicting.artist_id
    assert artist.MBID == "mbid-new"
    untouched = controller.get.get_entity_object(
        "Artist", artist_id=conflicting.artist_id
    )
    assert untouched.MBID == "mbid-different"
