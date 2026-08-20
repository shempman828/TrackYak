"""Tests for split-alias awareness in the MusicBrainz import credit-planning
path (docs/specs/split_and_merge_aliases.md). A credit whose role name or
artist name exactly matches a split-alias rule should expand into one
association row per resolved entity instead of the single find-or-create
path recreating/reusing one combined Role/Artist.
"""

from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.album.album_musicbrainz_review_import import (
    _plan_album_credit,
    _plan_track_credit,
)
from src.db.db_helpers.add import AddToDB
from src.db.db_helpers.get import GetFromDB
from src.db.db_helpers.update import UpdateDB
from src.db.db_tables.artist import Artist, ArtistSplitAlias
from src.db.db_tables.base import Base
from src.db.db_tables.role import Role, RoleSplitAlias
from src.musicbrainz.musicbrainz_release import MBTrackCredit


@pytest.fixture
def controller():
    engine = create_engine("sqlite:///:memory:")
    with engine.connect() as conn:
        conn.exec_driver_sql("PRAGMA foreign_keys=ON")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield SimpleNamespace(
        get=GetFromDB(session),
        add=AddToDB(session),
        update=UpdateDB(session),
    )
    session.close()


def _track(track_id=1, artist_roles=None):
    return SimpleNamespace(track_id=track_id, artist_roles=artist_roles or [])


def _album(album_id=1, album_roles=None):
    return SimpleNamespace(album_id=album_id, album_roles=album_roles or [])


def _credit(artist_name="Multi Instrumentalist", role_name="Viola & Violin"):
    return MBTrackCredit(
        artist_mbid="artist-mbid-1",
        artist_name=artist_name,
        role_name=role_name,
        canonical_name=artist_name,
    )


class TestRoleSplitAliasExpandsTrackCredit:
    def test_matching_role_name_creates_one_row_per_target_role(self, controller):
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

        rows = _plan_track_credit(
            controller, _track(), _credit(), known_roles=[], planned_by_track={}
        )

        assert len(rows) == 2
        role_ids = {r["role_id"] for r in rows}
        assert role_ids == {viola.role_id, violin.role_id}
        assert all(r["track_id"] == 1 for r in rows)

        # No new "Viola & Violin" Role was created.
        combined = session.query(Role).filter_by(role_name="Viola & Violin").first()
        assert combined is None

    def test_non_matching_role_name_behaves_as_before(self, controller):
        """Regression check: a role name with no split-alias rule still
        resolves through the ordinary single find-or-create path."""
        rows = _plan_track_credit(
            controller,
            _track(),
            _credit(role_name="Piano"),
            known_roles=[],
            planned_by_track={},
        )

        assert len(rows) == 1
        session = controller.get.session
        role = session.query(Role).filter_by(role_name="Piano").one()
        assert rows[0]["role_id"] == role.role_id


class TestArtistSplitAliasExpandsTrackCredit:
    def test_matching_artist_name_creates_one_row_per_target_artist(self, controller):
        session = controller.get.session
        simon = Artist(artist_name="Paul Simon")
        garfunkel = Artist(artist_name="Art Garfunkel")
        session.add_all([simon, garfunkel])
        session.commit()
        session.add_all(
            [
                ArtistSplitAlias(
                    alias_name="Simon & Garfunkel", artist_id=simon.artist_id, sort_order=0
                ),
                ArtistSplitAlias(
                    alias_name="Simon & Garfunkel", artist_id=garfunkel.artist_id, sort_order=1
                ),
            ]
        )
        session.commit()

        rows = _plan_track_credit(
            controller,
            _track(),
            _credit(artist_name="Simon & Garfunkel", role_name="Vocals"),
            known_roles=[],
            planned_by_track={},
        )

        assert len(rows) == 2
        artist_ids = {r["artist_id"] for r in rows}
        assert artist_ids == {simon.artist_id, garfunkel.artist_id}


class TestRoleSplitAliasExpandsAlbumCredit:
    def test_matching_role_name_creates_one_row_per_target_role_with_sort_order(
        self, controller
    ):
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

        rows = _plan_album_credit(
            controller,
            _album(),
            _credit(),
            known_roles=[],
            next_sort_order_by_role={},
            planned_pairs=set(),
        )

        assert len(rows) == 2
        role_ids = {r["role_id"] for r in rows}
        assert role_ids == {viola.role_id, violin.role_id}
        assert all(r["album_id"] == 1 for r in rows)
        assert all(r["sort_order"] == 0 for r in rows)  # different roles, each first
