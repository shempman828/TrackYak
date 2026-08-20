"""Tests for split-alias awareness in the file-tag genre import path
(docs/specs/split_and_merge_aliases.md). A genre name that exactly matches
a GenreSplitAlias rule should attach every target genre to the track
instead of TrackImporter._create_track_genre_relationships recreating/
reusing one combined genre.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.db_helpers.add import AddToDB
from src.db.db_helpers.get import GetFromDB
from src.db.db_tables.base import Base
from src.db.db_tables.genre import Genre, GenreSplitAlias
from src.db.db_tables.track import Track
from src.importing.library_import import TrackImporter


class _Controller:
    def __init__(self, session):
        self.get = GetFromDB(session)
        self.add = AddToDB(session)


@pytest.fixture
def controller():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield _Controller(session)
    session.close()


def _track(session, name="Some Track"):
    track = Track(track_name=name, track_file_path=f"/music/{name}.flac")
    session.add(track)
    session.commit()
    return track


def test_matching_genre_name_attaches_every_target_genre(controller):
    session = controller.get.session
    rock = Genre(genre_name="Rock")
    metal = Genre(genre_name="Metal")
    session.add_all([rock, metal])
    session.commit()
    session.add_all(
        [
            GenreSplitAlias(alias_name="Rock/Metal", genre_id=rock.genre_id, sort_order=0),
            GenreSplitAlias(alias_name="Rock/Metal", genre_id=metal.genre_id, sort_order=1),
        ]
    )
    session.commit()

    track = _track(session)
    importer = TrackImporter(controller)
    importer._create_track_genre_relationships(track, {"genre_name": ["Rock/Metal"]})
    session.commit()

    session.expire_all()
    genre_names = {g.genre_name for g in track.genres}
    assert genre_names == {"Rock", "Metal"}
    combined = session.query(Genre).filter_by(genre_name="Rock/Metal").first()
    assert combined is None


def test_non_matching_genre_name_behaves_as_before(controller):
    """Regression check: a genre name with no split-alias rule still
    resolves through the ordinary single find-or-create path."""
    session = controller.get.session
    track = _track(session)
    importer = TrackImporter(controller)
    importer._create_track_genre_relationships(track, {"genre_name": ["Jazz"]})
    session.commit()

    session.expire_all()
    assert {g.genre_name for g in track.genres} == {"Jazz"}
