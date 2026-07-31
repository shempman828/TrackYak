"""Regression test for bug #252: merging artists could silently drop the
losing artist's album_role_association rows instead of re-pointing them to
the surviving artist.

MergeDB.merge_entities() migrates FK rows with a raw SQL UPDATE, then does
`session.delete(source_entity)` so ORM cascade rules apply to any tables it
didn't explicitly touch. But Artist.album_roles is
`cascade="all, delete-orphan"` with `passive_deletes=True`, which only skips
*loading* the collection on delete -- it does not stop cascade-delete from
acting on a collection that was already loaded earlier in the session (e.g.
by viewing the artist's detail panel, which reads `artist.albums` ->
`artist.album_roles`). In that case cascade-delete walked the stale
in-memory list and deleted the just-migrated rows by primary key instead of
leaving them re-pointed to the target artist.

Covers MergeDB.merge_entities (src/db/db_helpers/merge.py) against a real
in-memory SQLite session so the ORM cascade path actually executes.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.db_helpers.merge import MergeDB
from src.db.db_tables.album import Album
from src.db.db_tables.artist import Artist
from src.db.db_tables.associations import AlbumRoleAssociation
from src.db.db_tables.base import Base
from src.db.db_tables.role import Role


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    with engine.connect() as conn:
        conn.exec_driver_sql("PRAGMA foreign_keys=ON")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def _make_artists_album_and_role(session):
    source = Artist(artist_name="Source Artist")
    target = Artist(artist_name="Target Artist")
    album = Album(album_name="Some Album")
    role = Role(role_name="Album Artist")
    session.add_all([source, target, album, role])
    session.commit()

    session.add(
        AlbumRoleAssociation(
            album_id=album.album_id, artist_id=source.artist_id, role_id=role.role_id
        )
    )
    session.commit()
    return source, target, album, role


def test_merge_preserves_album_role_when_collection_preloaded(session):
    source, target, album, role = _make_artists_album_and_role(session)

    # Simulate viewing the source artist's detail panel before merging,
    # which loads `artist.album_roles` into the session's identity map.
    assert len(source.album_roles) == 1

    merger = MergeDB(session)
    result = merger.merge_entities("Artist", source.artist_id, target.artist_id)

    assert result is True

    remaining = (
        session.query(AlbumRoleAssociation).filter_by(album_id=album.album_id).all()
    )
    assert len(remaining) == 1
    assert remaining[0].artist_id == target.artist_id


def test_merge_preserves_album_role_when_collection_not_preloaded(session):
    source, target, album, role = _make_artists_album_and_role(session)

    merger = MergeDB(session)
    result = merger.merge_entities("Artist", source.artist_id, target.artist_id)

    assert result is True

    remaining = (
        session.query(AlbumRoleAssociation).filter_by(album_id=album.album_id).all()
    )
    assert len(remaining) == 1
    assert remaining[0].artist_id == target.artist_id
