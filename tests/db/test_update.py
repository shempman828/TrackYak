"""Regression tests for UpdateDB.update_entity (src/db/db_helpers/update.py).

Changing an Artist's profile_pic_path or a Publisher's logo_path -- clearing
it, or re-picking with a different extension/name -- must unlink the file it
used to point at from images/artist_images/ or images/publisher_logos/.
"""

import pytest

from src.db.db_helpers.update import UpdateDB
from src.db.db_tables.artist import Artist
from src.db.db_tables.publisher import Publisher
from src.foundation import asset_paths


@pytest.fixture
def managed_dirs(tmp_path, monkeypatch):
    artist_dir = tmp_path / "artist_images"
    publisher_dir = tmp_path / "publisher_logos"
    artist_dir.mkdir()
    publisher_dir.mkdir()
    monkeypatch.setattr(asset_paths, "ARTIST_IMAGES_DIR", artist_dir)
    monkeypatch.setattr(asset_paths, "PUBLISHER_LOGOS_DIR", publisher_dir)
    return artist_dir, publisher_dir


def test_clearing_profile_picture_unlinks_old_file(session, managed_dirs):
    artist_dir, _ = managed_dirs
    old = artist_dir / "3_Chopin.jpg"
    old.write_bytes(b"x")
    artist = Artist(artist_name="Chopin", profile_pic_path=str(old))
    session.add(artist)
    session.commit()

    ok = UpdateDB(session).update_entity("Artist", artist.artist_id, profile_pic_path=None)

    assert ok is True
    assert not old.exists()


def test_repick_with_new_extension_unlinks_previous_file(session, managed_dirs):
    artist_dir, _ = managed_dirs
    old = artist_dir / "3_Chopin.jpg"
    old.write_bytes(b"old")
    new = artist_dir / "3_Chopin.jpeg"
    new.write_bytes(b"new")
    artist = Artist(artist_name="Chopin", profile_pic_path=str(old))
    session.add(artist)
    session.commit()

    ok = UpdateDB(session).update_entity("Artist", artist.artist_id, profile_pic_path=str(new))

    assert ok is True
    assert not old.exists()
    assert new.exists()


def test_repick_same_path_keeps_file(session, managed_dirs):
    artist_dir, _ = managed_dirs
    pic = artist_dir / "3_Chopin.jpg"
    pic.write_bytes(b"x")
    artist = Artist(artist_name="Chopin", profile_pic_path=str(pic))
    session.add(artist)
    session.commit()

    ok = UpdateDB(session).update_entity("Artist", artist.artist_id, profile_pic_path=str(pic))

    assert ok is True
    assert pic.exists()


def test_updating_other_fields_leaves_picture_untouched(session, managed_dirs):
    artist_dir, _ = managed_dirs
    pic = artist_dir / "3_Chopin.jpg"
    pic.write_bytes(b"x")
    artist = Artist(artist_name="Chopin", profile_pic_path=str(pic))
    session.add(artist)
    session.commit()

    ok = UpdateDB(session).update_entity("Artist", artist.artist_id, artist_name="Frederic")

    assert ok is True
    assert pic.exists()


def test_publisher_logo_replacement_unlinks_old_logo(session, managed_dirs):
    _, publisher_dir = managed_dirs
    old = publisher_dir / "8_Label.png"
    old.write_bytes(b"x")
    new = publisher_dir / "8_Label.webp"
    new.write_bytes(b"x")
    pub = Publisher(publisher_name="Label", logo_path=str(old))
    session.add(pub)
    session.commit()

    ok = UpdateDB(session).update_entity("Publisher", pub.publisher_id, logo_path=str(new))

    assert ok is True
    assert not old.exists()
    assert new.exists()


def test_old_file_kept_when_another_row_still_references_it(session, managed_dirs):
    artist_dir, _ = managed_dirs
    shared = artist_dir / "shared.jpg"
    shared.write_bytes(b"x")
    a1 = Artist(artist_name="One", profile_pic_path=str(shared))
    a2 = Artist(artist_name="Two", profile_pic_path=str(shared))
    session.add_all([a1, a2])
    session.commit()

    ok = UpdateDB(session).update_entity("Artist", a1.artist_id, profile_pic_path=None)

    assert ok is True
    assert shared.exists()  # a2 still points at it


def test_update_entity_returns_false_for_nonexistent_id(session):
    """A stale/nonexistent entity_id must report failure instead of silently no-opping."""
    ok = UpdateDB(session).update_entity("Artist", 999, artist_name="Ghost")
    assert ok is False


def test_update_entities_returns_false_when_no_ids_match(session):
    ok = UpdateDB(session).update_entities("Artist", [999, 1000], artist_name="Ghost")
    assert ok is False


def test_update_entity_by_filter_returns_false_when_nothing_matches(session):
    ok = UpdateDB(session).update_entity_by_filter("Artist", {"artist_name": "Nonexistent"}, artist_name="Renamed")
    assert ok is False
