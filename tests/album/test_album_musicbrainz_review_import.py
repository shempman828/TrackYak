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
from src.album.album_musicbrainz_review_import import (
    _plan_album_credit,
    _plan_track_credit,
)
from src.db.db_tables.artist import Artist, ArtistSplitAlias
from src.db.db_tables.role import Role, RoleSplitAlias

# ---- test_mb_import_artist_mbid_conflict.py ----------------------------------
@pytest.fixture
def controller_amc():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield SimpleNamespace(
        get=GetFromDB(session),
        add=AddToDB(session),
        update=UpdateDB(session),
    )
    session.close()

def _credit_amc(artist_name="John Smith", artist_mbid="mbid-new", canonical_name=None):
    return MBTrackCredit(
        artist_mbid=artist_mbid,
        artist_name=artist_name,
        role_name="Album Artist",
        canonical_name=canonical_name if canonical_name is not None else artist_name,
    )

def test_mbid_match_is_used_as_is(controller_amc):
    existing = controller_amc.add.add_entity(
        "Artist", artist_name="Some Other Name", MBID="mbid-new"
    )

    artist = _resolve_artist(controller_amc, _credit_amc())

    assert artist.artist_id == existing.artist_id

def test_name_match_with_no_mbid_backfills(controller_amc):
    existing = controller_amc.add.add_entity("Artist", artist_name="John Smith", MBID=None)

    artist = _resolve_artist(controller_amc, _credit_amc())

    assert artist.artist_id == existing.artist_id
    assert artist.MBID == "mbid-new"

def test_name_match_with_conflicting_mbid_creates_new_artist(controller_amc):
    conflicting = controller_amc.add.add_entity(
        "Artist", artist_name="John Smith", MBID="mbid-different"
    )

    artist = _resolve_artist(controller_amc, _credit_amc())

    assert artist.artist_id != conflicting.artist_id
    assert artist.MBID == "mbid-new"
    untouched = controller_amc.get.get_entity_object(
        "Artist", artist_id=conflicting.artist_id
    )
    assert untouched.MBID == "mbid-different"
    all_artists = controller_amc.get.get_all_entities("Artist")
    assert len(all_artists) == 2

def test_canonical_name_match_with_conflicting_mbid_creates_new_artist(controller_amc):
    # As-credited name doesn't match anything locally, but the canonical
    # MB name does -- and that row already belongs to a different MBID.
    conflicting = controller_amc.add.add_entity(
        "Artist", artist_name="John Q. Smith", MBID="mbid-different"
    )

    artist = _resolve_artist(
        controller_amc,
        _credit_amc(artist_name="J. Smith", canonical_name="John Q. Smith"),
    )

    assert artist.artist_id != conflicting.artist_id
    assert artist.MBID == "mbid-new"

# ---- test_mb_import_split_alias.py -------------------------------------------
# Tests for split-alias awareness in the MusicBrainz import credit-planning
# path (docs/specs/split_and_merge_aliases.md). A credit whose role name or
# artist name exactly matches a split-alias rule should expand into one
# association row per resolved entity instead of the single find-or-create
# path recreating/reusing one combined Role/Artist.
@pytest.fixture
def controller_sa():
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

def _credit_sa(artist_name="Multi Instrumentalist", role_name="Viola & Violin"):
    return MBTrackCredit(
        artist_mbid="artist-mbid-1",
        artist_name=artist_name,
        role_name=role_name,
        canonical_name=artist_name,
    )

class TestRoleSplitAliasExpandsTrackCredit:
    def test_matching_role_name_creates_one_row_per_target_role(self, controller_sa):
        session = controller_sa.get.session
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
            controller_sa, _track(), _credit_sa(), known_roles=[], planned_by_track={}
        )

        assert len(rows) == 2
        role_ids = {r["role_id"] for r in rows}
        assert role_ids == {viola.role_id, violin.role_id}
        assert all(r["track_id"] == 1 for r in rows)

        # No new "Viola & Violin" Role was created.
        combined = session.query(Role).filter_by(role_name="Viola & Violin").first()
        assert combined is None

    def test_non_matching_role_name_behaves_as_before(self, controller_sa):
        """Regression check: a role name with no split-alias rule still
        resolves through the ordinary single find-or-create path."""
        rows = _plan_track_credit(
            controller_sa,
            _track(),
            _credit_sa(role_name="Piano"),
            known_roles=[],
            planned_by_track={},
        )

        assert len(rows) == 1
        session = controller_sa.get.session
        role = session.query(Role).filter_by(role_name="Piano").one()
        assert rows[0]["role_id"] == role.role_id

class TestArtistSplitAliasExpandsTrackCredit:
    def test_matching_artist_name_creates_one_row_per_target_artist(self, controller_sa):
        session = controller_sa.get.session
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
            controller_sa,
            _track(),
            _credit_sa(artist_name="Simon & Garfunkel", role_name="Vocals"),
            known_roles=[],
            planned_by_track={},
        )

        assert len(rows) == 2
        artist_ids = {r["artist_id"] for r in rows}
        assert artist_ids == {simon.artist_id, garfunkel.artist_id}

class TestRoleSplitAliasExpandsAlbumCredit:
    def test_matching_role_name_creates_one_row_per_target_role_with_sort_order(
        self, controller_sa
    ):
        session = controller_sa.get.session
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
            controller_sa,
            _album(),
            _credit_sa(),
            known_roles=[],
            next_sort_order_by_role={},
            planned_pairs=set(),
        )

        assert len(rows) == 2
        role_ids = {r["role_id"] for r in rows}
        assert role_ids == {viola.role_id, violin.role_id}
        assert all(r["album_id"] == 1 for r in rows)
        assert all(r["sort_order"] == 0 for r in rows)  # different roles, each first
