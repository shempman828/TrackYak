"""Tests for the role parse-ignore list in the file-tag import path
(docs/specs/role_parse_ignore_list.md). A credit whose display role name
is on Config.get_excluded_roles() must never produce a Role or
TrackArtistRole row; other credits on the same track are unaffected.

Each test maps to a numbered acceptance criterion in that spec.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.db_helpers.add import AddToDB
from src.db.db_helpers.get import GetFromDB
from src.db.db_tables.artist import Artist
from src.db.db_tables.associations import TrackArtistRole
from src.db.db_tables.base import Base
from src.db.db_tables.role import Role
from src.db.db_tables.track import Track
from src.importing import library_import
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


@pytest.fixture
def set_excluded_roles(monkeypatch):
    def _set(names):
        monkeypatch.setattr(library_import.app_config, "get_excluded_roles", lambda: list(names))

    return _set


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


def test_excluded_role_creates_no_role_or_junction_row(controller, set_excluded_roles):
    session = controller.get.session
    set_excluded_roles(["Engineer"])
    track = _track(session)
    eng = _artist(session, "Some Engineer")

    TrackImporter(controller)._create_track_artist_relationships(track, {"Engineer": [eng]}, {})
    session.commit()

    assert _roles_on(session, track) == {}
    assert session.query(Role).filter_by(role_name="Engineer").first() is None


def test_non_excluded_sibling_credit_still_created(controller, set_excluded_roles):
    session = controller.get.session
    set_excluded_roles(["Engineer"])
    track = _track(session)
    eng = _artist(session, "Some Engineer")
    comp = _artist(session, "Some Composer")

    TrackImporter(controller)._create_track_artist_relationships(
        track, {"Engineer": [eng], "Composer": [comp]}, {}
    )
    session.commit()

    assert _roles_on(session, track) == {"Composer": "Some Composer"}


def test_role_name_match_is_case_insensitive(controller, set_excluded_roles):
    session = controller.get.session
    set_excluded_roles(["engineer"])
    track = _track(session)
    eng = _artist(session, "Some Engineer")

    TrackImporter(controller)._create_track_artist_relationships(track, {"Engineer": [eng]}, {})
    session.commit()

    assert _roles_on(session, track) == {}


def test_empty_exclusion_list_changes_nothing(controller, set_excluded_roles):
    session = controller.get.session
    set_excluded_roles([])
    track = _track(session)
    eng = _artist(session, "Some Engineer")
    comp = _artist(session, "Some Composer")

    TrackImporter(controller)._create_track_artist_relationships(
        track, {"Engineer": [eng], "Composer": [comp]}, {}
    )
    session.commit()

    assert _roles_on(session, track) == {"Engineer": "Some Engineer", "Composer": "Some Composer"}
