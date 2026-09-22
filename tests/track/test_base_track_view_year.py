"""Regression test for the BaseTrackView Year column never populating.

BaseTrackView used to hardcode its column key as "year", but Track has no
such attribute -- release year is only reachable via the release_year
association proxy to Album (src/db/db_tables/track.py). BaseTrackView now
builds its column set from the same TRACK_FIELDS spec as TrackView (see
docs/specs/base_track_view_parity.md) and reads values through the shared
TrackViewDataMixin._field_value, which resolves release_year via a
bulk-fetched album lookup cache.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.db_helpers.get import GetFromDB
from src.db.db_tables.album import Album
from src.db.db_tables.base import Base
from src.db.db_tables.track import Track
from src.track.view.base_track_view import BaseTrackView


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    yield s
    s.close()


class _Controller:
    def __init__(self, session):
        self.get = GetFromDB(session)


def test_year_column_populates_from_album_release_year(qapp, session):
    album = Album(album_name="Test Album", release_year=1999)
    track = Track(track_name="Test Track", album=album)
    session.add(track)
    session.commit()

    view = BaseTrackView(_Controller(session), [track])

    assert "release_year" in view.columns
    assert "year" not in view.columns
    assert view._field_value(track, "release_year") == 1999
