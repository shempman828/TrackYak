"""Track credits must be written for manually matched tracks, not only for
auto-matched ones (src/album/musicbrainz/album_musicbrainz_review_import.py,
_ReviewAcceptWorker._run)."""

from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.album.musicbrainz import album_musicbrainz_review_import
from src.album.musicbrainz.album_musicbrainz_review_import import _ReviewAcceptWorker
from src.db.db_helpers.add import AddToDB
from src.db.db_helpers.get import GetFromDB
from src.db.db_helpers.update import UpdateDB
from src.db.db_tables.album import Album
from src.db.db_tables.associations import TrackArtistRole
from src.db.db_tables.base import Base
from src.db.db_tables.track import Track
from src.musicbrainz.musicbrainz_release import MBReleaseDetail, MBReleaseTrack, MBTrackCredit


@pytest.fixture
def controller():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield SimpleNamespace(get=GetFromDB(session), add=AddToDB(session), update=UpdateDB(session))
    session.close()


def _mb_track(number, title, artist):
    return MBReleaseTrack(
        disc_number=1,
        disc_title=None,
        track_number=number,
        side=None,
        title=title,
        recording_mbid=f"rec-{number}",
        credits=[MBTrackCredit(artist_mbid=f"mbid-{artist}", artist_name=artist, role_name="Composer", canonical_name=artist)],
    )


def test_manual_match_gets_track_credits(qapp, controller, monkeypatch):
    # New artists carry an MBID, which would otherwise start a network award lookup.
    monkeypatch.setattr(album_musicbrainz_review_import, "import_awards_for_entity", lambda *a, **k: None)
    session = controller.get.session
    album = Album(album_name="Album")
    session.add(album)
    session.commit()
    auto_local = Track(track_name="Auto", album_id=album.album_id, track_number=1)
    manual_local = Track(track_name="Local Title", album_id=album.album_id, track_number=7)
    session.add_all([auto_local, manual_local])
    session.commit()

    auto_mb = _mb_track(1, "Auto", "Auto Composer")
    manual_mb = _mb_track(2, "MB Title", "Manual Composer")
    detail = MBReleaseDetail(release_group_mbid=None, tracks=[auto_mb, manual_mb])

    worker = _ReviewAcceptWorker(
        controller,
        album.album_id,
        detail,
        {id(auto_mb): auto_local.track_id},
        {id(manual_mb): manual_local.track_id},
        [],
        [],
        [],
        [(id(mbt), credit) for mbt in detail.tracks for credit in mbt.credits],
        [],
    )
    worker._run()

    credited = {row.track_id for row in session.query(TrackArtistRole).all()}
    assert credited == {auto_local.track_id, manual_local.track_id}
