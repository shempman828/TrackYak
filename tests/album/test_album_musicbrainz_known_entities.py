"""Tests for known_publisher_mbids/known_place_mbids -- the read-only DB
lookups fetch_release_detail uses to skip a redundant MusicBrainz
enrichment call for a Publisher/Place already on file locally by MBID.
"""

from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.album.album_musicbrainz_known_entities import (
    known_place_mbids,
    known_publisher_mbids,
)
from src.db.db_helpers.get import GetFromDB
from src.db.db_tables.base import Base
from src.db.db_tables.place import Place
from src.db.db_tables.publisher import Publisher


@pytest.fixture
def controller():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield SimpleNamespace(get=GetFromDB(session))
    session.close()


def test_known_publisher_mbids_returns_only_linked_publishers(controller):
    session = controller.get.session
    session.add_all(
        [
            Publisher(publisher_name="Atlantic Records", MBID="atlantic-mbid"),
            Publisher(publisher_name="Some Indie Label", MBID=None),
        ]
    )
    session.commit()

    assert known_publisher_mbids(controller) == frozenset({"atlantic-mbid"})


def test_known_place_mbids_returns_only_linked_places(controller):
    session = controller.get.session
    session.add_all(
        [
            Place(place_name="Church of the Holy Trinity", MBID="church-mbid"),
            Place(place_name="Unlinked Stub", MBID=None),
        ]
    )
    session.commit()

    assert known_place_mbids(controller) == frozenset({"church-mbid"})


def test_empty_database_returns_empty_sets(controller):
    assert known_publisher_mbids(controller) == frozenset()
    assert known_place_mbids(controller) == frozenset()
