"""Regression test: PublisherTreeWidget.calculate_recursive_album_counts must
dedupe albums that are tagged under both a publisher and one of its
descendants, rather than double-counting them via naive integer summation.
See src/genre/genre_view.py's GenreLoaderWorker for the identical bug/fix
pattern on the genre tree.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.db_helpers.get import GetFromDB
from src.db.db_tables.album import Album
from src.db.db_tables.associations import AlbumPublisher
from src.db.db_tables.base import Base
from src.db.db_tables.publisher import Publisher
from src.publisher.publisher_tree import PublisherTreeWidget


class _Controller:
    def __init__(self, session):
        self.get = GetFromDB(session)


def _make_publisher(session, name, parent=None):
    publisher = Publisher(publisher_name=name, parent=parent)
    session.add(publisher)
    session.commit()
    return publisher


def test_recursive_album_count_dedupes_album_tagged_at_multiple_levels(qapp):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    controller = _Controller(session)

    major = _make_publisher(session, "Major Label")
    imprint = _make_publisher(session, "Imprint", parent=major)

    album = Album(album_name="Overlap album")
    session.add(album)
    session.flush()
    # Tagged with both Major Label (parent) and Imprint (child) -- one
    # physical album.
    session.add(AlbumPublisher(album_id=album.album_id, publisher_id=major.publisher_id))
    session.add(AlbumPublisher(album_id=album.album_id, publisher_id=imprint.publisher_id))
    session.commit()

    tree = PublisherTreeWidget(controller)
    totals = tree.calculate_recursive_album_counts([major, imprint])

    # Naive summation would give 2 (1 own + 1 from Imprint); the correct
    # recursive count is 1 unique album.
    assert totals[major.publisher_id] == 1
    assert totals[imprint.publisher_id] == 1

    session.close()
