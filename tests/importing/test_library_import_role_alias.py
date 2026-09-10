"""Tests for RoleAlias / RoleSplitAlias awareness in the file-tag import
credit path (docs/specs/split_and_merge_aliases.md). A credit whose role
name is recorded in the RoleAlias table (from a merge, or added by hand)
must resolve to that canonical Role instead of
TrackImporter._create_track_artist_relationships spawning a duplicate; a
name recorded as a RoleSplitAlias rule must attach every target role to
the track instead of recreating/reusing one combined role. Same
alias-aware path the genre import and MusicBrainz import credit paths
already use.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.db_helpers.add import AddToDB
from src.db.db_helpers.get import GetFromDB
from src.db.db_tables.artist import Artist
from src.db.db_tables.associations import TrackArtistRole
from src.db.db_tables.base import Base
from src.db.db_tables.role import Role, RoleAlias, RoleSplitAlias
from src.db.db_tables.track import Track
from src.importing.library_import import TrackImporter


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


def _artist(session, name):
    artist = Artist(artist_name=name)
    session.add(artist)
    session.commit()
    return artist


def _track(session, name="Some Track"):
    track = Track(track_name=name, track_file_path=f"/music/{name}.flac")
    session.add(track)
    session.commit()
    return track


def _roles_on(session, track):
    rows = session.query(TrackArtistRole).filter_by(track_id=track.track_id).all()
    return {
        session.get(Role, r.role_id).role_name: session.get(Artist, r.artist_id).artist_name
        for r in rows
    }


def test_aliased_role_name_resolves_to_canonical_role(controller):
    session = controller.get.session
    mixing = Role(role_name="Mixing")
    session.add(mixing)
    session.commit()
    session.add(RoleAlias(alias_name="Mixer", role_id=mixing.role_id))
    session.commit()

    track = _track(session)
    artist = _artist(session, "Some Person")

    TrackImporter(controller)._create_track_artist_relationships(track, {"Mixer": [artist]}, {})
    session.commit()

    assert _roles_on(session, track) == {"Mixing": "Some Person"}
    # No duplicate "Mixer" Role was created.
    assert session.query(Role).filter_by(role_name="Mixer").first() is None


def test_unaliased_role_name_still_created_as_before(controller):
    session = controller.get.session
    track = _track(session)
    artist = _artist(session, "Some Person")

    TrackImporter(controller)._create_track_artist_relationships(track, {"Producer": [artist]}, {})
    session.commit()

    assert _roles_on(session, track) == {"Producer": "Some Person"}
    assert session.query(Role).filter_by(role_name="Producer").one()


def test_split_alias_role_name_attaches_every_target_role(controller):
    session = controller.get.session
    mixing = Role(role_name="Mixing")
    mastering = Role(role_name="Mastering")
    session.add_all([mixing, mastering])
    session.commit()
    session.add_all(
        [
            RoleSplitAlias(alias_name="Mixing & Mastering", role_id=mixing.role_id, sort_order=0),
            RoleSplitAlias(
                alias_name="Mixing & Mastering", role_id=mastering.role_id, sort_order=1
            ),
        ]
    )
    session.commit()

    track = _track(session)
    artist = _artist(session, "Some Person")

    TrackImporter(controller)._create_track_artist_relationships(
        track, {"Mixing & Mastering": [artist]}, {}
    )
    session.commit()

    assert _roles_on(session, track) == {"Mixing": "Some Person", "Mastering": "Some Person"}
    # No combined "Mixing & Mastering" Role was created.
    assert session.query(Role).filter_by(role_name="Mixing & Mastering").first() is None


def test_split_alias_expands_once_per_artist_on_the_credit(controller):
    session = controller.get.session
    viola = Role(role_name="Viola")
    violin = Role(role_name="Violin")
    session.add_all([viola, violin])
    session.commit()
    session.add_all(
        [
            RoleSplitAlias(alias_name="Viola & Violin", role_id=viola.role_id, sort_order=0),
            RoleSplitAlias(alias_name="Viola & Violin", role_id=violin.role_id, sort_order=1),
        ]
    )
    session.commit()

    track = _track(session)
    a1 = _artist(session, "Player One")
    a2 = _artist(session, "Player Two")

    TrackImporter(controller)._create_track_artist_relationships(
        track, {"Viola & Violin": [a1, a2]}, {}
    )
    session.commit()

    rows = session.query(TrackArtistRole).filter_by(track_id=track.track_id).all()
    pairs = {
        (session.get(Artist, r.artist_id).artist_name, session.get(Role, r.role_id).role_name)
        for r in rows
    }
    assert pairs == {
        ("Player One", "Viola"),
        ("Player One", "Violin"),
        ("Player Two", "Viola"),
        ("Player Two", "Violin"),
    }
