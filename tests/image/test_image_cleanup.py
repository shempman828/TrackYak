"""Unit tests for src/image/image_cleanup.py -- the single place that
unlinks or renames files under images/artist_images/ and
images/publisher_logos/ when their owning row goes away.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.core import asset_paths
from src.db.db_tables.artist import Artist
from src.db.db_tables.base import Base
from src.db.db_tables.publisher import Publisher
from src.image import image_cleanup
from src.image.image_cleanup import (
    delete_managed_image,
    prune_orphaned_images,
    rename_managed_image,
)


@pytest.fixture
def managed_dirs(tmp_path, monkeypatch):
    artist_dir = tmp_path / "artist_images"
    publisher_dir = tmp_path / "publisher_logos"
    artist_dir.mkdir()
    publisher_dir.mkdir()
    monkeypatch.setattr(asset_paths, "ARTIST_IMAGES_DIR", artist_dir)
    monkeypatch.setattr(asset_paths, "PUBLISHER_LOGOS_DIR", publisher_dir)
    return artist_dir, publisher_dir


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


# ---- delete_managed_image -------------------------------------------------


def test_delete_managed_image_removes_file_inside_managed_dir(managed_dirs):
    artist_dir, _ = managed_dirs
    f = artist_dir / "5_Miles Davis.jpg"
    f.write_bytes(b"x")

    assert delete_managed_image(str(f)) is True
    assert not f.exists()


def test_delete_managed_image_refuses_path_outside_managed_dirs(tmp_path, managed_dirs):
    outside = tmp_path / "somewhere_else.jpg"
    outside.write_bytes(b"x")

    assert delete_managed_image(str(outside)) is False
    assert outside.exists()


@pytest.mark.parametrize("value", [None, "", "/no/such/managed/file.jpg"])
def test_delete_managed_image_noops_on_empty_or_missing(value, managed_dirs):
    assert delete_managed_image(value) is False


# ---- rename_managed_image ----------------------------------------------------


def test_rename_managed_image_renames_source_named_file_to_target(managed_dirs):
    artist_dir, _ = managed_dirs
    old = artist_dir / "5_Source Name.jpg"
    old.write_bytes(b"x")

    new_path = rename_managed_image(str(old), 9, "Target Name")

    assert new_path == str(artist_dir / "9_Target Name.jpg")
    assert not old.exists()
    assert (artist_dir / "9_Target Name.jpg").read_bytes() == b"x"


def test_rename_managed_image_noop_when_already_correctly_named(managed_dirs):
    artist_dir, _ = managed_dirs
    f = artist_dir / "9_Target Name.jpg"
    f.write_bytes(b"x")

    assert rename_managed_image(str(f), 9, "Target Name") is None
    assert f.exists()


def test_rename_managed_image_overwrites_existing_target_file(managed_dirs):
    artist_dir, _ = managed_dirs
    old = artist_dir / "5_Source.png"
    old.write_bytes(b"new")
    clash = artist_dir / "9_Target.png"
    clash.write_bytes(b"stale")

    new_path = rename_managed_image(str(old), 9, "Target")

    assert new_path == str(clash)
    assert clash.read_bytes() == b"new"
    assert not old.exists()


# ---- prune_orphaned_images -------------------------------------------------


def test_prune_removes_unreferenced_keeps_referenced(managed_dirs, session):
    artist_dir, publisher_dir = managed_dirs
    kept = artist_dir / "1_Kept.jpg"
    kept.write_bytes(b"x")
    orphan = artist_dir / "2_Orphan.jpg"
    orphan.write_bytes(b"x")
    kept_logo = publisher_dir / "3_Label.png"
    kept_logo.write_bytes(b"x")
    orphan_logo = publisher_dir / "4_Gone.png"
    orphan_logo.write_bytes(b"x")

    session.add(Artist(artist_name="Kept", profile_pic_path=str(kept)))
    session.add(Publisher(publisher_name="Label", logo_path=str(kept_logo)))
    session.commit()

    result = prune_orphaned_images(session)

    assert kept.exists() and kept_logo.exists()
    assert not orphan.exists() and not orphan_logo.exists()
    assert set(result["removed"]) == {str(orphan), str(orphan_logo)}


def test_prune_guard_skips_dir_when_no_references_but_files_present(managed_dirs, session):
    artist_dir, _ = managed_dirs
    f = artist_dir / "1_Nobody.jpg"
    f.write_bytes(b"x")

    result = prune_orphaned_images(session)

    assert f.exists()
    assert result["removed"] == []


def test_prune_reports_reference_to_missing_file(managed_dirs, session):
    artist_dir, _ = managed_dirs
    present = artist_dir / "1_Here.jpg"
    present.write_bytes(b"x")
    session.add(Artist(artist_name="Here", profile_pic_path=str(present)))
    session.add(Artist(artist_name="Ghost", profile_pic_path=str(artist_dir / "2_Ghost.jpg")))
    session.commit()

    result = prune_orphaned_images(session)

    assert present.exists()
    assert result["missing_refs"] == ["2_Ghost.jpg"]
