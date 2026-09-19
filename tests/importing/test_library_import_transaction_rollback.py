"""Regression test: TrackImporter.add_track must roll back and return
FAILED for *any* exception raised inside its per-track transaction, not
just SQLAlchemyError/RuntimeError. Before the fix, an exception of another
type (e.g. TypeError from malformed metadata) skipped session.rollback()
and propagated out of add_track, leaving the shared session dirty for
every later track in the same import batch.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.db_helpers.add import AddToDB
from src.db.db_helpers.get import GetFromDB
from src.db.db_tables.base import Base
from src.importing import library_import
from src.importing.library_import import ImportResult, TrackImporter


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


def _metadata(track_name):
    return {
        "track_name": track_name,
        "album_album_name": "Some Album",
        "artist_name": "Some Artist",
    }


def test_non_sqlalchemy_exception_rolls_back_and_does_not_break_later_tracks(
    controller, monkeypatch, tmp_path
):
    files = [tmp_path / f"t{i}.mp3" for i in range(3)]
    for f in files:
        f.touch()

    metadatas = iter([_metadata("Track 1"), _metadata("Track 2"), _metadata("Track 3")])

    class _StubExtractor:
        def extract_metadata(self, _fp):
            return next(metadatas)

    monkeypatch.setattr(library_import, "MetadataExtractor", _StubExtractor)

    importer = TrackImporter(controller)

    assert importer.add_track(str(files[0])) is ImportResult.IMPORTED

    original_genre_fn = TrackImporter._create_track_genre_relationships
    monkeypatch.setattr(
        TrackImporter,
        "_create_track_genre_relationships",
        lambda *a, **k: (_ for _ in ()).throw(TypeError("boom")),
    )
    assert importer.add_track(str(files[1])) is ImportResult.FAILED

    monkeypatch.setattr(TrackImporter, "_create_track_genre_relationships", original_genre_fn)
    assert importer.add_track(str(files[2])) is ImportResult.IMPORTED


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
