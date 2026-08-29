"""
Tests for the reverse-lookup awards importer (src/awards/award_series_import.py)
against a scratch in-memory SQLite session -- never music_library.db.
MusicBrainz calls are mocked; no live network access here (see
docs/specs/awards_import.md's Phase 3 plan for the live smoke test).
"""

from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.awards.award_series_import import (
    fetch_award_series_relations,
    import_awards_for_entity,
    sync_awards,
)
from src.db.db_tables.album import Album
from src.db.db_tables.artist import Artist
from src.db.db_tables.award import Award, AwardAssociation
from src.db.db_tables.base import Base
from src.db.db_tables.track import Track


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def _add_track(session, mbid, name="7 rings"):
    track = Track(track_name=name, MBID=mbid)
    session.add(track)
    session.commit()
    return track


def _add_album(session, release_group_mbid, name="Test Album"):
    album = Album(album_name=name, release_group_MBID=release_group_mbid)
    session.add(album)
    session.commit()
    return album


def _add_artist(session, mbid, name="Test Artist"):
    artist = Artist(artist_name=name, MBID=mbid)
    session.add(artist)
    session.commit()
    return artist


def _relation(series_id, series_type, series_name, number_value=None):
    rel = {"type": "part of", "series": {"id": series_id, "type": series_type, "name": series_name}}
    if number_value is not None:
        rel["attributes"] = [{"attribute": "number", "value": number_value}]
    return rel


def _mock_lookups(recording=None, release_group=None, artist=None):
    """Patches all three musicbrainzngs lookup functions. Each arg, if
    given, is a list of series-relation-list dicts (via _relation) to
    return for any MBID passed to that entity type's endpoint."""

    def _response(key, relations):
        def _fn(mbid, includes=None):
            return {key: {"id": mbid, "series-relation-list": relations or []}}

        return _fn

    return patch.multiple(
        "src.awards.award_series_import.musicbrainzngs",
        get_recording_by_id=_response("recording", recording),
        get_release_group_by_id=_response("release-group", release_group),
        get_artist_by_id=_response("artist", artist),
    )


def test_genuine_award_series_creates_award_and_winner_association(session):
    _add_track(session, "rec-mbid-1")
    relations = [
        _relation(
            "series-1",
            "Recording award",
            "Grammy Award: Record of the Year nominees",
            "2023 winner",
        )
    ]
    with _mock_lookups(recording=relations, release_group=[], artist=[]):
        stats = sync_awards(session)

    assert stats.awards_created == 1
    assert stats.associations_created == 1

    award = session.query(Award).one()
    assert award.award_name == "Grammy Award"
    assert award.award_category == "Record of the Year"
    assert award.award_year == 2023
    assert award.mb_series_id == "series-1"

    assoc = session.query(AwardAssociation).one()
    assert assoc.entity_type == "Track"
    assert assoc.association_type == "winner"
    assert assoc.mb_target_mbid == "rec-mbid-1"


def test_nominee_without_winner_suffix_is_recorded_as_nominee(session):
    _add_track(session, "rec-mbid-1")
    relations = [
        _relation(
            "series-1", "Recording award", "Grammy Award: Record of the Year nominees", "2020"
        )
    ]
    with _mock_lookups(recording=relations, release_group=[], artist=[]):
        sync_awards(session)

    assoc = session.query(AwardAssociation).one()
    assert assoc.association_type == "nominee"


@pytest.mark.parametrize(
    "series_type,series_name",
    [
        ("Recording series", "Billboard Year-End Hot 100 singles of 2019"),
        ("Recording award", "BILLIONS CLUB"),
        ("Recording series", "Spotify: Top Hits of 2019"),
        ("Work award", "Grammy Award: Best Rock Song nominees"),
    ],
)
def test_non_award_or_work_typed_relations_produce_nothing(session, series_type, series_name):
    """Live-observed noise on a real MB-matched recording: a chart, a
    streaming-milestone club, and a playlist series all share the "series"
    taxonomy with genuine awards. Work-typed series can't actually appear
    via this module's endpoints, but the type filter rejects it anyway as a
    defensive check."""
    _add_track(session, "rec-mbid-1")
    relations = [_relation("series-x", series_type, series_name, "2019")]
    with _mock_lookups(recording=relations, release_group=[], artist=[]):
        stats = sync_awards(session)

    assert stats.awards_created == 0
    assert stats.associations_created == 0
    assert session.query(Award).count() == 0


def test_relation_with_no_number_attribute_is_skipped(session):
    _add_track(session, "rec-mbid-1")
    relations = [_relation("series-1", "Recording award", "Grammy Award: Record of the Year")]
    with _mock_lookups(recording=relations, release_group=[], artist=[]):
        stats = sync_awards(session)

    assert stats.awards_created == 0
    assert stats.associations_created == 0


def test_album_release_group_mbid_used_for_release_group_series(session):
    _add_album(session, "rg-mbid-1")
    relations = [
        _relation(
            "series-2",
            "Release group award",
            "Grammy Award: Best Rap Album nominees",
            "2001 winner",
        )
    ]
    with _mock_lookups(recording=[], release_group=relations, artist=[]):
        stats = sync_awards(session)

    assert stats.associations_created == 1
    assoc = session.query(AwardAssociation).one()
    assert assoc.entity_type == "Album"
    assert assoc.mb_target_mbid == "rg-mbid-1"


def test_artist_typed_award_matches_via_artist_mbid(session):
    _add_artist(session, "artist-mbid-1")
    relations = [
        _relation("series-3", "Artist award", "Grammy Award: Best New Artist nominees", "2015")
    ]
    with _mock_lookups(recording=[], release_group=[], artist=relations):
        stats = sync_awards(session)

    assert stats.associations_created == 1
    assoc = session.query(AwardAssociation).one()
    assert assoc.entity_type == "Artist"


def test_resync_is_idempotent(session):
    _add_track(session, "rec-mbid-1")
    relations = [
        _relation(
            "series-1",
            "Recording award",
            "Grammy Award: Record of the Year nominees",
            "2023 winner",
        )
    ]
    with _mock_lookups(recording=relations, release_group=[], artist=[]):
        sync_awards(session)
        stats = sync_awards(session)

    assert stats.awards_created == 0
    assert stats.associations_created == 0
    assert session.query(Award).count() == 1
    assert session.query(AwardAssociation).count() == 1


def test_manually_created_award_untouched_by_sync(session):
    manual_award = Award(award_name="Local Hall of Fame", award_year=2010)
    session.add(manual_award)
    session.commit()
    manual_assoc = AwardAssociation(
        award_id=manual_award.award_id,
        entity_type="Artist",
        entity_id=1,
        association_type="recipient",
    )
    session.add(manual_assoc)
    session.commit()

    _add_track(session, "rec-mbid-1")
    relations = [
        _relation(
            "series-1",
            "Recording award",
            "Grammy Award: Record of the Year nominees",
            "2023 winner",
        )
    ]
    with _mock_lookups(recording=relations, release_group=[], artist=[]):
        sync_awards(session)

    session.refresh(manual_award)
    session.refresh(manual_assoc)
    assert manual_award.mb_series_id is None
    assert manual_assoc.mb_target_mbid is None
    assert manual_assoc.association_type == "recipient"
    assert session.query(Award).count() == 2


def test_lookup_failure_on_one_entity_does_not_lose_prior_progress(session):
    _add_track(session, "rec-mbid-1")
    _add_track(session, "rec-mbid-2", name="Second Track")
    relations = [
        _relation(
            "series-1",
            "Recording award",
            "Grammy Award: Record of the Year nominees",
            "2023 winner",
        )
    ]

    def flaky_get_recording(mbid, includes=None):
        if mbid == "rec-mbid-2":
            raise RuntimeError("simulated network failure")
        return {"recording": {"id": mbid, "series-relation-list": relations}}

    with patch.multiple(
        "src.awards.award_series_import.musicbrainzngs",
        get_recording_by_id=flaky_get_recording,
        get_release_group_by_id=lambda mbid, includes=None: {
            "release-group": {"series-relation-list": []}
        },
        get_artist_by_id=lambda mbid, includes=None: {"artist": {"series-relation-list": []}},
    ):
        stats = sync_awards(session)

    assert stats.lookup_failures == 1
    assert stats.associations_created == 1
    assert session.query(AwardAssociation).count() == 1


# ---------------------------------------------------------------------------
# fetch/write split -- lets a Qt caller run the network half on a worker
# thread and pass the relations straight to import_awards_for_entity(), so
# a stuck musicbrainzngs request (up to 8 retries at a 30s socket timeout)
# never blocks the UI thread. See album_musicbrainz_mixin._fetch_all.
# ---------------------------------------------------------------------------


def _explode(*_args, **_kwargs):
    raise AssertionError("musicbrainzngs must not be called")


def test_import_with_prefetched_relations_skips_network(session):
    album = _add_album(session, "rg-mbid-1")
    relations = [
        _relation(
            "series-9",
            "Release group award",
            "Grammy Award: Album of the Year nominees",
            "2004 winner",
        )
    ]
    with patch.multiple(
        "src.awards.award_series_import.musicbrainzngs",
        get_recording_by_id=_explode,
        get_release_group_by_id=_explode,
        get_artist_by_id=_explode,
    ):
        result = import_awards_for_entity(
            session, "Album", album.album_id, "rg-mbid-1", relations=relations
        )

    assert result.lookup_failed is False
    assert result.awards_created == 1
    assert result.associations_created == 1
    assoc = session.query(AwardAssociation).one()
    assert assoc.entity_type == "Album"
    assert assoc.association_type == "winner"
    assert assoc.mb_target_mbid == "rg-mbid-1"


def test_import_with_relations_none_reports_failure_without_network(session):
    album = _add_album(session, "rg-mbid-1")
    with patch.multiple(
        "src.awards.award_series_import.musicbrainzngs",
        get_recording_by_id=_explode,
        get_release_group_by_id=_explode,
        get_artist_by_id=_explode,
    ):
        result = import_awards_for_entity(
            session, "Album", album.album_id, "rg-mbid-1", relations=None
        )

    assert result.lookup_failed is True
    assert result.awards_created == 0
    assert result.associations_created == 0
    assert session.query(AwardAssociation).count() == 0


def test_fetch_award_series_relations_returns_list_on_success():
    relations = [_relation("series-1", "Release group award", "Grammy Award", "2000")]
    with _mock_lookups(recording=[], release_group=relations, artist=[]):
        fetched = fetch_award_series_relations("Album", "rg-mbid-1")
    assert fetched == relations


def test_fetch_award_series_relations_returns_none_on_lookup_failure():
    def boom(mbid, includes=None):
        raise RuntimeError("simulated network failure")

    with patch.multiple(
        "src.awards.award_series_import.musicbrainzngs", get_release_group_by_id=boom
    ):
        assert fetch_award_series_relations("Album", "rg-mbid-1") is None
