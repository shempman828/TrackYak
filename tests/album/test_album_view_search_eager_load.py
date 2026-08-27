"""Regression: searching / non-default sorting the album view must not fire a
lazy-load per album.

AlbumView.load_albums() eager-loads (via _ALBUM_LIST_LOAD_OPTIONS) the
relationships that the search predicate (album_filtering._album_matches_filters)
and the sort keys (album_sorting._sort_key) walk for every album. Without that,
a single search over a large library issues 1 + 2N SELECTs on the Qt main
thread and freezes the UI for seconds -- reported as "thread lock while album
searching in album view".
"""

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from src.album.album_view import _ALBUM_LIST_LOAD_OPTIONS, AlbumView
from src.db.db_helpers.get import GetFromDB
from src.db.db_tables import Album, Artist, Role, Track
from src.db.db_tables.associations import AlbumRoleAssociation
from src.db.db_tables.base import Base


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    # Match the real app engine (src/db/db_engine.py): a plain commit must not
    # expire already-loaded attributes/collections, or the eager loading this
    # test checks would be undone by get_all_entities()' own read-txn commit().
    s = sessionmaker(bind=engine, expire_on_commit=False)()
    yield s
    s.close()


def _populate(session, n_albums=20, artists_per=2, tracks_per=5):
    role = Role(role_name="Album Artist")
    session.add(role)
    session.flush()
    for i in range(n_albums):
        album = Album(album_name=f"Album {i:03d}", release_year=1990 + i)
        session.add(album)
        session.flush()
        for j in range(artists_per):
            artist = Artist(artist_name=f"Artist {i:03d}-{j}")
            session.add(artist)
            session.flush()
            session.add(
                AlbumRoleAssociation(
                    album_id=album.album_id,
                    artist_id=artist.artist_id,
                    role_id=role.role_id,
                    sort_order=j,
                )
            )
        for t in range(tracks_per):
            session.add(Track(track_name=f"T{t}", album_id=album.album_id))
    session.commit()


def _simulate_search_and_sort(albums):
    """Touch exactly what _album_matches_filters (text search) and _sort_key
    (Artist / Track Count sort) read from each album."""
    for album in albums:
        # -> album.album_roles, assoc.artist, assoc.role.role_name
        AlbumView._get_artist_names(album)
        # -> album.tracks
        AlbumView._get_track_count(album)
        _ = (album.album_name, album.release_year)


def _count_sql(engine, fn):
    counter = {"n": 0}

    def _before(*_args, **_kwargs):
        counter["n"] += 1

    event.listen(engine, "before_cursor_execute", _before)
    try:
        fn()
    finally:
        event.remove(engine, "before_cursor_execute", _before)
    return counter["n"]


def test_eager_load_options_eliminate_per_album_queries(session):
    _populate(session)
    getter = GetFromDB(session)

    albums = getter.get_all_entities("Album", load_options=_ALBUM_LIST_LOAD_OPTIONS)
    assert len(albums) == 20

    n = _count_sql(
        session.bind, lambda: _simulate_search_and_sort(albums)
    )
    assert n == 0, f"search+sort pass issued {n} lazy queries; expected 0"


def test_without_eager_load_options_the_pass_storms(session):
    """Guard for the test above: the same pass without the load options really
    does fire a per-album lazy-load storm, so the assertion has teeth."""
    _populate(session)
    getter = GetFromDB(session)

    albums = getter.get_all_entities("Album")  # no load_options

    n = _count_sql(
        session.bind, lambda: _simulate_search_and_sort(albums)
    )
    assert n > 20, f"expected an N+1 storm without eager loading, got {n} queries"
