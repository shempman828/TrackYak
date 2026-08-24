"""Regression test for the BaseTrackView Year column never populating.

BaseTrackView hardcoded its column key as "year", but Track has no such
attribute -- release year is only reachable via the release_year
association proxy to Album (src/db/db_tables/track.py). _get_track_value's
generic getattr fallback therefore always returned "" for that column.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.db_tables.album import Album
from src.db.db_tables.base import Base
from src.db.db_tables.track import Track
from src.track.base_track_view import BaseTrackView


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    yield s
    s.close()


class _Controller:
    """BaseTrackView only stores this; load_data() doesn't call into it."""


def test_year_column_populates_from_album_release_year(qapp, session):
    album = Album(album_name="Test Album", release_year=1999)
    track = Track(track_name="Test Track", album=album)
    session.add(track)
    session.commit()

    view = BaseTrackView(_Controller(), [track])

    assert "release_year" in view.columns
    assert "year" not in view.columns
    assert view._get_track_value(track, "release_year") == 1999
