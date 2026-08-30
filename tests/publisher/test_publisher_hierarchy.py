"""Tests for src/publisher/publisher_hierarchy.py."""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.db_helpers.get import GetFromDB
from src.db.db_tables.album import Album
from src.db.db_tables.associations import AlbumPublisher
from src.db.db_tables.base import Base
from src.db.db_tables.publisher import Publisher
from src.publisher.publisher_hierarchy import get_publisher_albums


class _Controller:
    def __init__(self, session):
        self.get = GetFromDB(session)


def _add_album(session, name, publisher_id, year=None, month=None, day=None):
    album = Album(album_name=name, release_year=year, release_month=month, release_day=day)
    session.add(album)
    session.flush()
    session.add(AlbumPublisher(album_id=album.album_id, publisher_id=publisher_id))
    return album


def test_get_publisher_albums_orders_chronologically_with_unknown_dates_last():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    controller = _Controller(session)

    major = Publisher(publisher_name="Major Label")
    imprint = Publisher(publisher_name="Imprint", parent=major)
    session.add_all([major, imprint])
    session.commit()

    # Deliberately inserted out of chronological order, spanning parent + child.
    _add_album(session, "No date", major.publisher_id)
    _add_album(session, "1999-05-01", imprint.publisher_id, 1999, 5, 1)
    _add_album(session, "1999-01-15", major.publisher_id, 1999, 1, 15)
    _add_album(session, "1980", major.publisher_id, 1980)
    _add_album(session, "2010-12-31", imprint.publisher_id, 2010, 12, 31)
    session.commit()

    albums = get_publisher_albums(controller, major.publisher_id)

    assert [a.album_name for a in albums] == [
        "1980",
        "1999-01-15",
        "1999-05-01",
        "2010-12-31",
        "No date",
    ]

    session.close()
