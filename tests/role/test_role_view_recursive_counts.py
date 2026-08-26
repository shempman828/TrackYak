"""Regression test: the role tree must roll up recursive (own + descendant)
counts and display them with the genre/playlist "own · recursive"
convention, instead of only ever showing each role's direct assignment
count. See src/role/role_view.py RoleLoaderWorker / RoleView._make_role_item.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.db_helpers.get import GetFromDB
from src.db.db_tables.album import Album
from src.db.db_tables.artist import Artist
from src.db.db_tables.associations import AlbumRoleAssociation, TrackArtistRole
from src.db.db_tables.base import Base
from src.db.db_tables.role import Role
from src.db.db_tables.track import Track
from src.role.role_view import RoleLoaderWorker, RoleView


class _Controller:
    def __init__(self, session):
        self.get = GetFromDB(session)


def _make_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _run_worker(controller):
    """Runs RoleLoaderWorker.run() synchronously and captures its emit."""
    worker = RoleLoaderWorker(controller)
    payload = {}
    worker.finished.connect(
        lambda roles, albums, tracks, recursive: payload.update(
            all_roles=roles,
            album_counts=albums,
            track_counts=tracks,
            recursive_counts=recursive,
        )
    )
    worker.run()
    return payload


def test_recursive_counts_roll_up_without_double_counting():
    session = _make_session()
    controller = _Controller(session)

    parent = Role(role_name="String Instruments")
    child = Role(role_name="Guitar", parent=parent)
    session.add_all([parent, child])
    session.commit()

    album = Album(album_name="Test Album")
    artist_a = Artist(artist_name="Artist A")
    artist_b = Artist(artist_name="Artist B")
    track = Track(track_name="Test Track", album=album)
    session.add_all([album, artist_a, artist_b, track])
    session.commit()

    # One album credit directly on the parent role...
    session.add(
        AlbumRoleAssociation(
            album_id=album.album_id, artist_id=artist_a.artist_id, role_id=parent.role_id
        )
    )
    # ...and one track credit directly on the child role.
    session.add(
        TrackArtistRole(
            track_id=track.track_id, artist_id=artist_b.artist_id, role_id=child.role_id
        )
    )
    session.commit()

    payload = _run_worker(controller)

    # Own counts stay exactly as before: one direct assignment each.
    assert payload["album_counts"].get(parent.role_id, 0) == 1
    assert payload["track_counts"].get(child.role_id, 0) == 1

    # Recursive counts roll the child's credit up into the parent...
    assert payload["recursive_counts"][parent.role_id] == 2
    # ...but the child's own recursive count is unaffected by its parent.
    assert payload["recursive_counts"][child.role_id] == 1

    session.close()


def test_recursive_counts_do_not_dedupe_distinct_role_credits():
    """The same artist can hold two different roles on the same track (e.g.
    both a sibling "Guitar" and "Bass" role under "String Instruments") --
    those are two distinct credits and must both still be counted, not
    collapsed by an over-eager track/artist-only dedup key."""
    session = _make_session()
    controller = _Controller(session)

    parent = Role(role_name="String Instruments")
    guitar = Role(role_name="Guitar", parent=parent)
    bass = Role(role_name="Bass", parent=parent)
    session.add_all([parent, guitar, bass])
    session.commit()

    artist = Artist(artist_name="Multi-Instrumentalist")
    album = Album(album_name="Test Album")
    track = Track(track_name="Test Track", album=album)
    session.add_all([artist, album, track])
    session.commit()

    session.add_all(
        [
            TrackArtistRole(
                track_id=track.track_id, artist_id=artist.artist_id, role_id=guitar.role_id
            ),
            TrackArtistRole(
                track_id=track.track_id, artist_id=artist.artist_id, role_id=bass.role_id
            ),
        ]
    )
    session.commit()

    payload = _run_worker(controller)

    assert payload["recursive_counts"][guitar.role_id] == 1
    assert payload["recursive_counts"][bass.role_id] == 1
    # Both sibling credits must roll up -- not dedupe to 1.
    assert payload["recursive_counts"][parent.role_id] == 2

    session.close()


def test_make_role_item_uses_own_recursive_display_convention(qapp):
    """Mirrors GenreView/PlaylistView's "own · recursive" tree label, per
    the "use genre and playlist tree count method and number display
    convention for roles too" request."""
    assert RoleView._format_role_count(1, 2) == "1 · 2"
    assert RoleView._format_role_count(3, 3) == "3"
