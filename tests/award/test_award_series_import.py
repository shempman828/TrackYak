"""
Tests for the reverse-lookup awards importer (src/award/award_series_import.py)
against a scratch in-memory SQLite session -- never music_library.db.
MusicBrainz calls are mocked; no live network access here.
"""

from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker

from src.award.award_series_import import fetch_award_series_relations, import_awards_for_entity, sync_awards
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


def _add_entity(session, entity_type, mbid):
    if entity_type == "Track":
        row = Track(track_name="7 rings", MBID=mbid)
    elif entity_type == "Album":
        row = Album(album_name="Test Album", release_group_MBID=mbid)
    else:
        row = Artist(artist_name="Test Artist", MBID=mbid)
    session.add(row)
    session.commit()
    return row


def _entity_id(row):
    return inspect(row).identity[0]


def _relation(series_id, series_type, series_name, number_value=None):
    rel = {"type": "part of", "series": {"id": series_id, "type": series_type, "name": series_name}}
    if number_value is not None:
        rel["attributes"] = [{"attribute": "number", "value": number_value}]
    return rel


def _explode(*_args, **_kwargs):
    raise AssertionError("musicbrainzngs must not be called")


@pytest.fixture
def no_network():
    with patch.multiple("src.award.award_series_import.musicbrainzngs", get_recording_by_id=_explode, get_release_group_by_id=_explode, get_artist_by_id=_explode):
        yield


def _import(session, row, entity_type, mbid, relations):
    return import_awards_for_entity(session, entity_type, _entity_id(row), mbid, relations=relations)


@pytest.mark.parametrize(
    "entity_type,series_type,series_name,number,expected_assoc",
    [
        ("Track", "Recording award", "Grammy Award: Record of the Year nominees", "2023 winner", "recipient"),
        ("Track", "Recording award", "Grammy Award: Record of the Year nominees", "2020", "nominee"),
        ("Album", "Release group award", "Grammy Award: Best Rap Album nominees", "2001 winner", "recipient"),
        ("Artist", "Artist award", "Grammy Award: Best New Artist nominees", "2015", "nominee"),
        # Chart, streaming-club and playlist series share the "series" taxonomy with awards.
        ("Track", "Recording series", "Billboard Year-End Hot 100 singles of 2019", "2019", None),
        ("Track", "Recording award", "BILLIONS CLUB", "2019", None),
        ("Track", "Work award", "Grammy Award: Best Rock Song nominees", "2019", None),
        ("Track", "Recording award", "Grammy Award: Record of the Year", None, None),
    ],
)
def test_relation_is_classified_and_written(session, no_network, entity_type, series_type, series_name, number, expected_assoc):
    row = _add_entity(session, entity_type, "mbid-1")
    result = _import(session, row, entity_type, "mbid-1", [_relation("series-1", series_type, series_name, number)])

    assert result.lookup_failed is False
    if expected_assoc is None:
        assert result.associations_created == 0
        assert session.query(Award).count() == 0
        return

    assert (result.awards_created, result.associations_created) == (1, 1)
    award = session.query(Award).one()
    assert award.award_name == "Grammy Award"
    assert award.mb_series_id == "series-1"
    assoc = session.query(AwardAssociation).one()
    assert assoc.entity_type == entity_type
    assert assoc.association_type == expected_assoc
    assert assoc.mb_target_mbid == "mbid-1"


def test_award_fields_are_parsed_from_series_name_and_number(session, no_network):
    row = _add_entity(session, "Track", "rec-1")
    _import(session, row, "Track", "rec-1", [_relation("s", "Recording award", "Grammy Award: Record of the Year nominees", "2023 winner")])
    award = session.query(Award).one()
    assert (award.award_category, award.award_year) == ("Record of the Year", 2023)


def test_reimport_is_idempotent_and_heals_stale_winner_type(session, no_network):
    row = _add_entity(session, "Track", "rec-1")
    relations = [_relation("s", "Recording award", "Grammy Award: Record of the Year nominees", "2023 winner")]
    _import(session, row, "Track", "rec-1", relations)

    assoc = session.query(AwardAssociation).one()
    assoc.association_type = "winner"
    session.commit()

    result = _import(session, row, "Track", "rec-1", relations)
    assert (result.awards_created, result.associations_created) == (0, 0)
    assert session.query(Award).count() == 1
    session.refresh(assoc)
    assert assoc.association_type == "recipient"


def test_manually_created_award_untouched_by_import(session, no_network):
    manual_award = Award(award_name="Local Hall of Fame", award_year=2010)
    session.add(manual_award)
    session.commit()
    manual_assoc = AwardAssociation(award_id=manual_award.award_id, entity_type="Artist", entity_id=1, association_type="recipient")
    session.add(manual_assoc)
    session.commit()

    row = _add_entity(session, "Track", "rec-1")
    _import(session, row, "Track", "rec-1", [_relation("s", "Recording award", "Grammy Award: Record of the Year nominees", "2023 winner")])

    session.refresh(manual_award)
    session.refresh(manual_assoc)
    assert manual_award.mb_series_id is None
    assert manual_assoc.mb_target_mbid is None
    assert session.query(Award).count() == 2


def test_relations_none_reports_failure_without_network(session, no_network):
    row = _add_entity(session, "Album", "rg-1")
    result = _import(session, row, "Album", "rg-1", None)
    assert result.lookup_failed is True
    assert session.query(AwardAssociation).count() == 0


def test_fetch_award_series_relations_returns_list_or_none_on_failure():
    relations = [_relation("series-1", "Release group award", "Grammy Award", "2000")]
    with patch("src.award.award_series_import.musicbrainzngs.get_release_group_by_id", return_value={"release-group": {"series-relation-list": relations}}):
        assert fetch_award_series_relations("Album", "rg-mbid-1") == relations

    with patch("src.award.award_series_import.musicbrainzngs.get_release_group_by_id", side_effect=RuntimeError("down")):
        assert fetch_award_series_relations("Album", "rg-mbid-1") is None


def test_cancelled_sync_reports_only_checked_entities(session):
    for i in range(30):
        _add_entity(session, "Artist", f"mbid-{i}")
    progress = []

    with patch("src.award.award_series_import.fetch_award_series_relations", return_value=[]):
        stats = sync_awards(session, progress_callback=lambda c, t: progress.append((c, t)), is_cancelled=lambda: True)

    assert stats.entities_checked == 24
    assert progress[-1] == (24, 30)
