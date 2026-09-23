"""Developer mode: immediate file metadata write.

Maps to acceptance criteria in
docs/specs/dev_mode_immediate_metadata_write.md. AC3 (no src/ reference
outside src/dev/) is covered by the existing repo-wide grep test in
test_dev_album_sort.py -- this module's new file lives under src/dev/ so
it's already exempted there. AC4 (Developer-tab checkbox dependency) is
covered in test_dev_settings_tab.py.
"""

import configparser
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.db_helpers import merge as merge_module, track_dirty
from src.db.db_helpers.delete import DeleteDB
from src.db.db_helpers.merge import MergeDB
from src.db.db_helpers.registry import BaseDBHelper
from src.db.db_helpers.update import UpdateDB
from src.db.db_tables.associations import TrackGenre
from src.db.db_tables.base import Base
from src.db.db_tables.genre import Genre
from src.db.db_tables.track import Track
from src.dev import dev_immediate_write, dev_mode as dev_mode_module
from src.metadata.metadata_raw_tags import RawTagExtractor
from src.metadata.writers.metadata_flac_file_writer import FlacFileWriter

_STREAMINFO = b"\x00" * 34


def _make_flac(path) -> None:
    """A minimal but structurally valid FLAC: fLaC + STREAMINFO, no audio
    frames or existing tags -- enough for a Vorbis-comment UPDATE_EXISTING
    write. Same recipe as test_flac_artwork_dedupe.py's _make_flac."""
    writer = FlacFileWriter()
    path.write_bytes(writer._serialize_blocks([(0, _STREAMINFO)], audio_tail=b"", prefix=b""))


class _FakeConfig:
    """Just the ``.config`` configparser handle dev_immediate_write touches."""

    def __init__(self):
        self.config = configparser.ConfigParser()


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    with engine.connect() as conn:
        conn.exec_driver_sql("PRAGMA foreign_keys=ON")
    Base.metadata.create_all(engine)
    sess = sessionmaker(bind=engine)()
    yield sess
    sess.close()


@pytest.fixture(autouse=True)
def _clean_patches():
    dev_immediate_write.unpatch()
    yield
    dev_immediate_write.unpatch()


def _make_track(session, tmp_path, name="song", with_file=True) -> Track:
    track = Track(track_name=name, needs_tag_write=0)
    if with_file:
        path = tmp_path / f"{name}.flac"
        _make_flac(path)
        track.track_file_path = str(path)
    session.add(track)
    session.commit()
    return track


def _flac_title(path) -> list:
    data = Path(path).read_bytes()
    return RawTagExtractor().extract_raw_tags(data, ".flac").get("TITLE", [])


# --------------------------------------------------------------------------- #
# AC1 -- flag default + round-trip
# --------------------------------------------------------------------------- #
def test_flag_defaults_off_and_round_trips():
    cfg = _FakeConfig()
    assert dev_immediate_write.is_enabled(cfg) is False

    dev_immediate_write.set_enabled(cfg, True)
    assert dev_immediate_write.is_enabled(cfg) is True

    dev_immediate_write.set_enabled(cfg, False)
    assert dev_immediate_write.is_enabled(cfg) is False


def test_flag_survives_save_and_reload(tmp_path):
    path = tmp_path / "config.ini"

    cfg = _FakeConfig()
    dev_immediate_write.set_enabled(cfg, True)
    with path.open("w") as fh:
        cfg.config.write(fh)

    reloaded = _FakeConfig()
    reloaded.config.read(path)
    assert dev_immediate_write.is_enabled(reloaded) is True


# --------------------------------------------------------------------------- #
# AC2 -- patch() idempotent
# --------------------------------------------------------------------------- #
def test_patch_idempotent():
    dev_immediate_write.patch()
    wrapped_mark = track_dirty.mark_tracks_dirty
    wrapped_commit = BaseDBHelper._commit

    dev_immediate_write.patch()

    assert track_dirty.mark_tracks_dirty is wrapped_mark
    assert BaseDBHelper._commit is wrapped_commit
    assert merge_module.mark_tracks_dirty is wrapped_mark


# --------------------------------------------------------------------------- #
# AC5 -- both flags on, tag field changed, file exists -> written immediately
# --------------------------------------------------------------------------- #
def test_immediate_write_on_tag_field_update(session, tmp_path, dev_config):
    dev_mode_module.set_enabled(dev_config, True)
    dev_immediate_write.set_enabled(dev_config, True)
    dev_immediate_write.patch()

    track = _make_track(session, tmp_path)
    update = UpdateDB(session)

    update.update_entity("Track", track.track_id, track_name="New Title")

    assert _flac_title(track.track_file_path) == ["New Title"]
    session.refresh(track)
    assert track.needs_tag_write == 0


# --------------------------------------------------------------------------- #
# AC6 -- master flag off -> no immediate write
# --------------------------------------------------------------------------- #
def test_no_immediate_write_when_master_off(session, tmp_path, dev_config):
    dev_mode_module.set_enabled(dev_config, False)
    dev_immediate_write.set_enabled(dev_config, True)
    dev_immediate_write.patch()

    track = _make_track(session, tmp_path)
    update = UpdateDB(session)

    update.update_entity("Track", track.track_id, track_name="New Title")

    assert _flac_title(track.track_file_path) == []
    session.refresh(track)
    assert track.needs_tag_write == 1


# --------------------------------------------------------------------------- #
# AC7 -- master on, immediate-write flag off -> no immediate write
# --------------------------------------------------------------------------- #
def test_no_immediate_write_when_sub_flag_off(session, tmp_path, dev_config):
    dev_mode_module.set_enabled(dev_config, True)
    dev_immediate_write.set_enabled(dev_config, False)
    dev_immediate_write.patch()

    track = _make_track(session, tmp_path)
    update = UpdateDB(session)

    update.update_entity("Track", track.track_id, track_name="New Title")

    assert _flac_title(track.track_file_path) == []
    session.refresh(track)
    assert track.needs_tag_write == 1


# --------------------------------------------------------------------------- #
# AC8 -- no file / missing file path -> no crash, stays dirty
# --------------------------------------------------------------------------- #
def test_no_file_path_does_not_crash(session, dev_config):
    dev_mode_module.set_enabled(dev_config, True)
    dev_immediate_write.set_enabled(dev_config, True)
    dev_immediate_write.patch()

    track = Track(track_name="no file", needs_tag_write=0)
    session.add(track)
    session.commit()
    update = UpdateDB(session)

    update.update_entity("Track", track.track_id, track_name="New Title")

    session.refresh(track)
    assert track.needs_tag_write == 1


# --------------------------------------------------------------------------- #
# AC9 -- non-tag field change never triggers a write attempt
# --------------------------------------------------------------------------- #
def test_non_tag_field_update_does_not_write(session, tmp_path, dev_config, monkeypatch):
    dev_mode_module.set_enabled(dev_config, True)
    dev_immediate_write.set_enabled(dev_config, True)
    dev_immediate_write.patch()

    calls = []
    from src.metadata.metadata_writer import MetadataWriter

    monkeypatch.setattr(MetadataWriter, "write_metadata_to_track", lambda self, tid, mode: calls.append(tid) or True)

    track = _make_track(session, tmp_path)
    update = UpdateDB(session)

    # needs_tag_write is outside TRACK_TAG_FIELDS -- editing it directly must
    # not itself be mistaken for a dirtying change.
    update.update_entity("Track", track.track_id, needs_tag_write=1)

    assert calls == []


# --------------------------------------------------------------------------- #
# AC10 -- one failing write in a batch does not block the others / the caller
# --------------------------------------------------------------------------- #
def test_batch_write_failure_is_contained(session, tmp_path, dev_config):
    dev_mode_module.set_enabled(dev_config, True)
    dev_immediate_write.set_enabled(dev_config, True)
    dev_immediate_write.patch()

    ok_track = _make_track(session, tmp_path, name="ok")
    bad_track = _make_track(session, tmp_path, name="bad", with_file=False)
    genre = Genre(genre_name="Dev Test Genre")
    session.add(genre)
    session.commit()

    from src.db.db_helpers.add import AddToDB

    add = AddToDB(session)
    result = add.add_entities("TrackGenre", [{"track_id": ok_track.track_id, "genre_id": genre.genre_id}, {"track_id": bad_track.track_id, "genre_id": genre.genre_id}])

    assert len(result) == 2  # the batch call itself completed normally
    assert _flac_title(ok_track.track_file_path) == ["ok"]
    session.refresh(ok_track)
    session.refresh(bad_track)
    assert ok_track.needs_tag_write == 0
    assert bad_track.needs_tag_write == 1  # no file -> left dirty, not crashed


# --------------------------------------------------------------------------- #
# AC11 -- the internal needs_tag_write=0 clear does not trigger a second write
# --------------------------------------------------------------------------- #
def test_no_recursive_double_write(session, tmp_path, dev_config, monkeypatch):
    dev_mode_module.set_enabled(dev_config, True)
    dev_immediate_write.set_enabled(dev_config, True)
    dev_immediate_write.patch()

    from src.metadata.metadata_writer import MetadataWriter

    calls = []
    original = MetadataWriter.write_metadata_to_track

    def _spy(self, track_id, mode):
        calls.append(track_id)
        return original(self, track_id, mode)

    monkeypatch.setattr(MetadataWriter, "write_metadata_to_track", _spy)

    track = _make_track(session, tmp_path)
    update = UpdateDB(session)
    update.update_entity("Track", track.track_id, track_name="New Title")

    assert calls == [track.track_id]


# --------------------------------------------------------------------------- #
# AC12 -- cascading dirty paths (delete of an association row, merge) also
# trigger immediate writes
# --------------------------------------------------------------------------- #
def test_delete_cascade_triggers_immediate_write(session, tmp_path, dev_config):
    dev_mode_module.set_enabled(dev_config, True)
    dev_immediate_write.set_enabled(dev_config, True)
    dev_immediate_write.patch()

    track = _make_track(session, tmp_path)
    genre = Genre(genre_name="Dev Test Genre")
    session.add(genre)
    session.commit()
    session.add(TrackGenre(track_id=track.track_id, genre_id=genre.genre_id))
    session.commit()

    delete = DeleteDB(session)
    delete.delete_entity("TrackGenre", track_id=track.track_id, genre_id=genre.genre_id)

    assert _flac_title(track.track_file_path) == [track.track_name]
    session.refresh(track)
    assert track.needs_tag_write == 0


def test_merge_cascade_triggers_immediate_write(session, tmp_path, dev_config):
    dev_mode_module.set_enabled(dev_config, True)
    dev_immediate_write.set_enabled(dev_config, True)
    dev_immediate_write.patch()

    source_track = _make_track(session, tmp_path, name="source-track")
    target_track = _make_track(session, tmp_path, name="target-track")
    source_genre = Genre(genre_name="Source Genre")
    target_genre = Genre(genre_name="Target Genre")
    session.add_all([source_genre, target_genre])
    session.commit()
    session.add(TrackGenre(track_id=source_track.track_id, genre_id=source_genre.genre_id))
    session.add(TrackGenre(track_id=target_track.track_id, genre_id=target_genre.genre_id))
    session.commit()

    merge = MergeDB(session)
    ok = merge.merge_entities("Genre", source_genre.genre_id, target_genre.genre_id)

    assert ok is True
    # Both tracks credit the surviving genre after the merge, and both were
    # resolved as dirty by CASCADE_RESOLVERS -- via merge.py's own rebound
    # mark_tracks_dirty, not just track_dirty's module attribute.
    assert _flac_title(source_track.track_file_path) == [source_track.track_name]
    assert _flac_title(target_track.track_file_path) == [target_track.track_name]
    session.refresh(source_track)
    session.refresh(target_track)
    assert source_track.needs_tag_write == 0
    assert target_track.needs_tag_write == 0


# --------------------------------------------------------------------------- #
# AC13 -- teardown restores originals
# --------------------------------------------------------------------------- #
def test_unpatch_restores_originals():
    orig_mark = track_dirty.mark_tracks_dirty
    orig_commit = BaseDBHelper._commit

    dev_immediate_write.patch()
    assert track_dirty.mark_tracks_dirty is not orig_mark
    assert BaseDBHelper._commit is not orig_commit

    dev_immediate_write.unpatch()

    assert track_dirty.mark_tracks_dirty is orig_mark
    assert BaseDBHelper._commit is orig_commit
    assert merge_module.mark_tracks_dirty is orig_mark
    assert dev_immediate_write._pending() == set()
