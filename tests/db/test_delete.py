"""Regression tests for DeleteDB.delete_entity (src/db/db_helpers/delete.py).

Deleting an Artist row must also unlink its managed picture file under
images/artist_images/ -- nothing else in the app ever removes those files.
"""

import pytest

from src.db.db_helpers.delete import DeleteDB
from src.db.db_tables.artist import Artist
from src.foundation import asset_paths


@pytest.fixture
def managed_dirs(tmp_path, monkeypatch):
    artist_dir = tmp_path / "artist_images"
    artist_dir.mkdir()
    monkeypatch.setattr(asset_paths, "ARTIST_IMAGES_DIR", artist_dir)
    return artist_dir


def _artist_with_pic(session, artist_dir, name):
    pic = artist_dir / f"_{name}.jpg"
    pic.write_bytes(b"x")
    artist = Artist(artist_name=name, profile_pic_path=str(pic))
    session.add(artist)
    session.commit()
    # Rename to the id-prefixed deterministic form now that we have the id.
    final = artist_dir / f"{artist.artist_id}_{name}.jpg"
    pic.rename(final)
    artist.profile_pic_path = str(final)
    session.commit()
    return artist, final


def test_single_delete_unlinks_profile_picture(session, managed_dirs):
    artist, pic = _artist_with_pic(session, managed_dirs, "Miles Davis")

    assert DeleteDB(session).delete_entity("Artist", entity_id=artist.artist_id) is True

    assert session.get(Artist, artist.artist_id) is None
    assert not pic.exists()


def test_batch_delete_unlinks_every_picture(session, managed_dirs):
    a1, p1 = _artist_with_pic(session, managed_dirs, "One")
    a2, p2 = _artist_with_pic(session, managed_dirs, "Two")

    ok = DeleteDB(session).delete_entity("Artist", entity_ids=[a1.artist_id, a2.artist_id])

    assert ok is True
    assert not p1.exists() and not p2.exists()


def test_delete_leaves_files_outside_managed_dirs_alone(session, tmp_path, managed_dirs):
    outside = tmp_path / "user_photo.jpg"
    outside.write_bytes(b"x")
    artist = Artist(artist_name="Ext", profile_pic_path=str(outside))
    session.add(artist)
    session.commit()

    assert DeleteDB(session).delete_entity("Artist", entity_id=artist.artist_id) is True
    assert outside.exists()


def test_delete_without_picture_is_fine(session, managed_dirs):
    artist = Artist(artist_name="No Pic")
    session.add(artist)
    session.commit()

    assert DeleteDB(session).delete_entity("Artist", entity_id=artist.artist_id) is True
    assert session.get(Artist, artist.artist_id) is None
