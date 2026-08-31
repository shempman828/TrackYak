"""Tests for the Artist.sort_name column and
scripts/backfill_artist_sort_name.py::backfill_sort_name() -- the one-time
catch-up that fills the MusicBrainz filing name for every artist that
already has an MBID, per docs/specs/artist_sort_name.md.

In-memory SQLite, same shape as tests/db/test_media_format_backfill.py.
The backfill is exercised through a minimal fake controller so no network
or db_helpers wiring is involved.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from scripts.backfill_artist_sort_name import backfill_sort_name
from src.db.db_tables.artist import Artist
from src.db.db_tables.base import Base
from src.musicbrainz.musicbrainz_core import MusicBrainzLookupError


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


class _FakeController:
    """Just enough surface for backfill_sort_name: get.get_all_entities and
    update.update_entity, both against one plain Session."""

    def __init__(self, session):
        self._session = session
        self.get = self
        self.update = self

    def get_all_entities(self, model_name):
        assert model_name == "Artist"
        return self._session.query(Artist).all()

    def update_entity(self, model_name, entity_id, **kwargs):
        assert model_name == "Artist"
        artist = self._session.get(Artist, entity_id)
        for key, value in kwargs.items():
            setattr(artist, key, value)
        self._session.commit()
        return True


def _add(session, **kwargs):
    artist = Artist(**kwargs)
    session.add(artist)
    session.commit()
    return artist


# AC1 / AC2 -- schema round-trip ------------------------------------------
def test_sort_name_column_round_trips(session):
    artist = _add(session, artist_name="Miles Davis", sort_name="Davis, Miles")
    artist_id = artist.artist_id
    session.expunge_all()

    reloaded = session.get(Artist, artist_id)
    assert reloaded.sort_name == "Davis, Miles"

    reloaded.sort_name = None
    session.commit()
    session.expunge_all()
    assert session.get(Artist, artist_id).sort_name is None


def test_sort_name_defaults_to_null(session):
    artist = _add(session, artist_name="No Sort Name Yet")
    assert artist.sort_name is None


# AC7 -- backfill fills only MBID-bearing, empty rows --------------------
def test_backfill_fills_null_sort_name_for_artists_with_mbid(session):
    a = _add(session, artist_name="The Beatles", MBID="mbid-a")
    b = _add(session, artist_name="Sigur Ros", MBID="mbid-b")
    controller = _FakeController(session)

    names = {"mbid-a": "Beatles, The", "mbid-b": "Sigur Rós"}
    filled, unavailable, failed = backfill_sort_name(controller, fetch=lambda mbid: names[mbid])

    assert sorted(filled) == ["Sigur Ros -> Sigur Rós", "The Beatles -> Beatles, The"]
    assert (unavailable, failed) == ([], [])
    session.refresh(a)
    session.refresh(b)
    assert a.sort_name == "Beatles, The"
    assert b.sort_name == "Sigur Rós"


def test_backfill_skips_populated_and_mbidless_rows(session):
    populated = _add(session, artist_name="Has Sort", MBID="mbid-x", sort_name="Sort, Has")
    no_mbid = _add(session, artist_name="No MBID", sort_name=None)
    controller = _FakeController(session)

    def _fetch(mbid):
        raise AssertionError(f"should not have been fetched: {mbid}")

    filled, unavailable, failed = backfill_sort_name(controller, fetch=_fetch)

    assert (filled, unavailable, failed) == ([], [], [])
    session.refresh(populated)
    session.refresh(no_mbid)
    assert populated.sort_name == "Sort, Has"
    assert no_mbid.sort_name is None


# AC8 -- backfill is idempotent ----------------------------------------
def test_second_run_is_a_noop(session):
    _add(session, artist_name="The Beatles", MBID="mbid-a")
    controller = _FakeController(session)

    backfill_sort_name(controller, fetch=lambda mbid: "Beatles, The")
    calls = []
    filled, unavailable, failed = backfill_sort_name(
        controller, fetch=lambda mbid: calls.append(mbid) or "Beatles, The"
    )

    assert calls == []
    assert (filled, unavailable, failed) == ([], [], [])


# degradation --------------------------------------------------------------
def test_backfill_degrades_on_lookup_failure_and_missing_sort_name(session):
    boom = _add(session, artist_name="Boom", MBID="mbid-boom")
    empty = _add(session, artist_name="Empty", MBID="mbid-empty")
    ok = _add(session, artist_name="Ok", MBID="mbid-ok")
    controller = _FakeController(session)

    def _fetch(mbid):
        if mbid == "mbid-boom":
            raise MusicBrainzLookupError("404")
        if mbid == "mbid-empty":
            return None
        return "Ok, The"

    filled, unavailable, failed = backfill_sort_name(controller, fetch=_fetch)

    assert filled == ["Ok -> Ok, The"]
    assert unavailable == ["Empty"]
    assert failed == ["Boom"]
    session.refresh(boom)
    session.refresh(empty)
    session.refresh(ok)
    assert boom.sort_name is None
    assert empty.sort_name is None
    assert ok.sort_name == "Ok, The"
