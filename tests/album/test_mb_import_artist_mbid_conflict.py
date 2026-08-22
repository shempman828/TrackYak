"""Regression coverage for _resolve_artist's MBID/name-match ordering
(src/album/album_musicbrainz_review_import.py): MBID match first, then a
name match backfills the MBID only if the matched row has none yet, and a
name match whose row already carries a *different* MBID is not a real
match at all -- it must be ignored and a new Artist created instead of
merging two people MusicBrainz itself considers distinct.
"""

from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.album.album_musicbrainz_review_import import _resolve_artist
from src.db.db_helpers.add import AddToDB
from src.db.db_helpers.get import GetFromDB
from src.db.db_helpers.update import UpdateDB
from src.db.db_tables.base import Base
from src.musicbrainz.musicbrainz_release import MBTrackCredit


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


def _credit(artist_name="John Smith", artist_mbid="mbid-new", canonical_name=None):
    return MBTrackCredit(
        artist_mbid=artist_mbid,
        artist_name=artist_name,
        role_name="Album Artist",
        canonical_name=canonical_name if canonical_name is not None else artist_name,
    )


def test_mbid_match_is_used_as_is(controller):
    existing = controller.add.add_entity(
        "Artist", artist_name="Some Other Name", MBID="mbid-new"
    )

    artist = _resolve_artist(controller, _credit())

    assert artist.artist_id == existing.artist_id


def test_name_match_with_no_mbid_backfills(controller):
    existing = controller.add.add_entity("Artist", artist_name="John Smith", MBID=None)

    artist = _resolve_artist(controller, _credit())

    assert artist.artist_id == existing.artist_id
    assert artist.MBID == "mbid-new"


def test_name_match_with_conflicting_mbid_creates_new_artist(controller):
    conflicting = controller.add.add_entity(
        "Artist", artist_name="John Smith", MBID="mbid-different"
    )

    artist = _resolve_artist(controller, _credit())

    assert artist.artist_id != conflicting.artist_id
    assert artist.MBID == "mbid-new"
    untouched = controller.get.get_entity_object(
        "Artist", artist_id=conflicting.artist_id
    )
    assert untouched.MBID == "mbid-different"
    all_artists = controller.get.get_all_entities("Artist")
    assert len(all_artists) == 2


def test_canonical_name_match_with_conflicting_mbid_creates_new_artist(controller):
    # As-credited name doesn't match anything locally, but the canonical
    # MB name does -- and that row already belongs to a different MBID.
    conflicting = controller.add.add_entity(
        "Artist", artist_name="John Q. Smith", MBID="mbid-different"
    )

    artist = _resolve_artist(
        controller,
        _credit(artist_name="J. Smith", canonical_name="John Q. Smith"),
    )

    assert artist.artist_id != conflicting.artist_id
    assert artist.MBID == "mbid-new"
