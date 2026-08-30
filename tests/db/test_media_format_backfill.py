"""Tests for the Album.media_format column and
scripts/backfill_album_media_format.py::backfill_media_format() -- the
one-time catch-up that fills the carrier (CD / Vinyl / ...) for every album
that already has an MBID, per docs/specs/media-format-type.md.

In-memory SQLite, same shape as tests/db/test_mood_score_backfill.py. The
backfill is exercised through a minimal fake controller so no network or
db_helpers wiring is involved -- the ACs here are about which rows the pass
touches and how it degrades, not the ORM write path.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from scripts.backfill_album_media_format import backfill_media_format
from src.db.db_tables.album import Album
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
    """Just enough surface for backfill_media_format: get.get_all_entities
    and update.update_entity, both against one plain Session."""

    def __init__(self, session):
        self._session = session
        self.get = self
        self.update = self

    def get_all_entities(self, model_name):
        assert model_name == "Album"
        return self._session.query(Album).all()

    def update_entity(self, model_name, entity_id, **kwargs):
        assert model_name == "Album"
        album = self._session.get(Album, entity_id)
        for key, value in kwargs.items():
            setattr(album, key, value)
        self._session.commit()
        return True


def _add(session, **kwargs):
    album = Album(**kwargs)
    session.add(album)
    session.commit()
    return album


# AC2 -- schema round-trip ---------------------------------------------------
def test_media_format_column_round_trips(session):
    album = _add(session, album_name="Kind of Blue", media_format='12" Vinyl')
    album_id = album.album_id
    session.expunge_all()

    reloaded = session.get(Album, album_id)
    assert reloaded.media_format == '12" Vinyl'


def test_media_format_defaults_to_null(session):
    album = _add(session, album_name="No Format Yet")
    assert album.media_format is None


# AC8 -- backfill fills NULLs ----------------------------------------------
def test_backfill_fills_null_media_format_for_albums_with_mbid(session):
    a = _add(session, album_name="A", MBID="mbid-a")
    b = _add(session, album_name="B", MBID="mbid-b")
    controller = _FakeController(session)

    formats = {"mbid-a": "CD", "mbid-b": "CD/DVD-Video"}
    filled, unavailable, failed = backfill_media_format(
        controller, fetch=lambda mbid: formats[mbid]
    )

    assert sorted(filled) == ["A -> CD", "B -> CD/DVD-Video"]
    assert (unavailable, failed) == ([], [])
    session.refresh(a)
    session.refresh(b)
    assert a.media_format == "CD"
    assert b.media_format == "CD/DVD-Video"


# AC9 -- skips populated / no-MBID, and is re-run safe ---------------------
def test_backfill_skips_populated_and_mbidless_rows(session):
    populated = _add(session, album_name="Has Format", MBID="mbid-x", media_format="Vinyl")
    no_mbid = _add(session, album_name="No MBID", media_format=None)
    controller = _FakeController(session)

    def _fetch(mbid):
        raise AssertionError(f"should not have been fetched: {mbid}")

    filled, unavailable, failed = backfill_media_format(controller, fetch=_fetch)

    assert (filled, unavailable, failed) == ([], [], [])
    session.refresh(populated)
    session.refresh(no_mbid)
    assert populated.media_format == "Vinyl"
    assert no_mbid.media_format is None


def test_second_run_is_a_noop(session):
    _add(session, album_name="A", MBID="mbid-a")
    controller = _FakeController(session)

    backfill_media_format(controller, fetch=lambda mbid: "CD")
    calls = []
    filled, unavailable, failed = backfill_media_format(
        controller, fetch=lambda mbid: calls.append(mbid) or "CD"
    )

    assert calls == []  # nothing left with a NULL media_format to fetch
    assert (filled, unavailable, failed) == ([], [], [])


# AC10 -- degrades gracefully --------------------------------------------
def test_backfill_degrades_on_lookup_failure_and_missing_format(session):
    boom = _add(session, album_name="Boom", MBID="mbid-boom")
    empty = _add(session, album_name="Empty", MBID="mbid-empty")
    ok = _add(session, album_name="Ok", MBID="mbid-ok")
    controller = _FakeController(session)

    def _fetch(mbid):
        if mbid == "mbid-boom":
            raise MusicBrainzLookupError("404")
        if mbid == "mbid-empty":
            return None
        return "CD"

    filled, unavailable, failed = backfill_media_format(controller, fetch=_fetch)

    assert filled == ["Ok -> CD"]
    assert unavailable == ["Empty"]
    assert failed == ["Boom"]
    session.refresh(boom)
    session.refresh(empty)
    session.refresh(ok)
    assert boom.media_format is None
    assert empty.media_format is None
    assert ok.media_format == "CD"
