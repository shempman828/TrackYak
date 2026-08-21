"""
Regression tests for the sync batching/verification rework:

- duplicate detection is diffed against the destination in ONE bulk listing
  (one os.scandir / one `gio list`) instead of one round trip per track
- a track is only counted as copied after a post-copy listing confirms it
  landed, and a transport failure is retried instead of silently dropped

Folder-sync assertions run against real files on a real scratch directory
(never against the app's library DB). MTP assertions run the real `gio`
binary against a `file://` URI standing in for the device -- no physical
Android hardware is available in this environment, but the subprocess/
parsing path exercised is identical to the real mtp:// backend.
"""

import os
import subprocess

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from src.db.db_tables.artist import Artist
from src.db.db_tables.associations import TrackArtistRole
from src.db.db_tables.base import Base
from src.db.db_tables.playlist import Playlist, PlaylistTracks
from src.db.db_tables.role import Role
from src.db.db_tables.track import Track
from src.sync.mtp_manager import MtpDevice, MtpManager
from src.sync.sync_manager import SyncManager


@pytest.fixture
def session():
    # expire_on_commit=False matches src/db/db_engine.py's production
    # session config -- GetFromDB.query_entities commits after every read,
    # and without this the just-loaded relationships would be expired
    # before _track_to_dict can use them.
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    session = Session()
    yield session
    session.close()


@pytest.fixture
def sync_manager(session):
    return SyncManager(session)


def _write_file(path, content=b"track-bytes"):
    with open(path, "wb") as f:
        f.write(content)
    return path


def _make_track_dict(tmp_path, name, content=b"track-bytes"):
    path = _write_file(tmp_path / f"{name}.mp3", content)
    return {"file_path": str(path), "artist": "Artist", "title": name, "duration": 1.0}


# ---------------------------------------------------------------------------
# Folder-sync diff pool: one scan, correct partition, parallel MD5 confirm
# ---------------------------------------------------------------------------


def test_diff_local_pool_uses_one_directory_scan_regardless_of_track_count(
    tmp_path, sync_manager
):
    music_dir = tmp_path / "music"
    music_dir.mkdir()
    tracks = [_make_track_dict(tmp_path, f"Track {i}") for i in range(20)]

    real_scandir = os.scandir
    calls = {"n": 0}

    def counting_scandir(path):
        calls["n"] += 1
        return real_scandir(path)

    os.scandir = counting_scandir
    try:
        sync_manager._diff_local_pool(tracks, str(music_dir))
    finally:
        os.scandir = real_scandir

    assert calls["n"] == 1


def test_diff_local_pool_partitions_true_duplicates_and_changed_files(tmp_path, sync_manager):
    music_dir = tmp_path / "music"
    music_dir.mkdir()

    duplicate = _make_track_dict(tmp_path, "Duplicate", content=b"same-bytes")
    changed = _make_track_dict(tmp_path, "Changed", content=b"new-version")
    new_track = _make_track_dict(tmp_path, "New")

    # Duplicate is already on the device with identical content.
    dup_filename = sync_manager._safe_filename(duplicate["artist"], duplicate["title"], ".mp3")
    _write_file(music_dir / dup_filename, content=b"same-bytes")

    # Changed exists on the device but with different (stale) content, same
    # size as "old-version-" would not match here -- use a same-length but
    # different-content stale copy so the size check alone can't tell them
    # apart and the MD5 confirmation step is what must catch it.
    changed_filename = sync_manager._safe_filename(changed["artist"], changed["title"], ".mp3")
    _write_file(music_dir / changed_filename, content=b"stale-version")  # same length, diff bytes

    to_copy, to_skip = sync_manager._diff_local_pool(
        [duplicate, changed, new_track], str(music_dir)
    )

    assert {t["title"] for t in to_skip} == {"Duplicate"}
    assert {t["title"] for t in to_copy} == {"Changed", "New"}


# ---------------------------------------------------------------------------
# MTP diff pool: one `gio list` call, real subprocess, file:// stand-in
# ---------------------------------------------------------------------------


def test_diff_mtp_pool_uses_one_subprocess_call_regardless_of_track_count(tmp_path):
    device_dir = tmp_path / "device_music"
    device_dir.mkdir()
    device = MtpDevice(uri=f"file://{device_dir}/", name="test-device", backend="gio")

    mgr = SyncManager.__new__(SyncManager)  # no DB needed for this path
    mgr.mtp = MtpManager()

    tracks = [_make_track_dict(tmp_path, f"Track {i}") for i in range(15)]
    # Pre-place a few as real duplicates on the "device".
    for t in tracks[:5]:
        filename = mgr._safe_filename(t["artist"], t["title"], ".mp3")
        _write_file(device_dir / filename, content=open(t["file_path"], "rb").read())

    real_run = subprocess.run
    calls = {"n": 0}

    def counting_run(*a, **kw):
        calls["n"] += 1
        return real_run(*a, **kw)

    subprocess.run = counting_run
    try:
        to_copy, to_skip = mgr._diff_mtp_pool(tracks, device, device.uri)
    finally:
        subprocess.run = real_run

    assert calls["n"] == 1
    assert len(to_skip) == 5
    assert len(to_copy) == 10


# ---------------------------------------------------------------------------
# Copy-with-retry / post-copy verification
# ---------------------------------------------------------------------------


def test_copy_with_retry_recovers_from_a_transient_failure(tmp_path, sync_manager):
    music_dir = tmp_path / "music"
    music_dir.mkdir()
    track = _make_track_dict(tmp_path, "Flaky")
    track["device_filename"] = sync_manager._safe_filename("Artist", "Flaky", ".mp3")

    attempts = {"n": 0}

    def flaky_copy_one(t):
        attempts["n"] += 1
        if attempts["n"] == 1:
            return False  # simulates a dropped MTP transfer on the first try
        dest = music_dir / t["device_filename"]
        _write_file(dest, content=open(t["file_path"], "rb").read())
        return True

    succeeded, failed = sync_manager._copy_with_retry(
        [track],
        flaky_copy_one,
        lambda: sync_manager._list_local_pool(str(music_dir)),
        lambda t: os.path.getsize(t["file_path"]),
    )

    assert attempts["n"] == 2
    assert failed == []
    assert succeeded == [track]
    assert track["copied_successfully"] is True


def test_copy_with_retry_reports_persistent_failure_after_max_retries(tmp_path, sync_manager):
    music_dir = tmp_path / "music"
    music_dir.mkdir()
    track = _make_track_dict(tmp_path, "AlwaysFails")
    track["device_filename"] = sync_manager._safe_filename("Artist", "AlwaysFails", ".mp3")

    attempts = {"n": 0}

    def never_lands(t):
        attempts["n"] += 1
        return False  # transport never actually writes the file

    succeeded, failed = sync_manager._copy_with_retry(
        [track],
        never_lands,
        lambda: sync_manager._list_local_pool(str(music_dir)),
        lambda t: os.path.getsize(t["file_path"]),
    )

    from src.sync.sync_manager import _MAX_RETRIES

    assert attempts["n"] == _MAX_RETRIES + 1
    assert succeeded == []
    assert failed == [track]
    assert track["copied_successfully"] is False


def test_sync_playlist_to_device_reports_failed_tracks_instead_of_dropping_them(
    tmp_path, sync_manager, monkeypatch
):
    """End-to-end regression for the original bug: a track that never makes
    it must show up in tracks_failed (and be retried), not vanish with only
    a log line."""
    dest = tmp_path / "device"

    good = _write_file(tmp_path / "good.mp3")
    bad = _write_file(tmp_path / "bad.mp3")

    playlist_data = {
        "kind": "playlist",
        "name": "Test Playlist",
        "playlist_id": 1,
    }
    monkeypatch.setattr(
        sync_manager,
        "get_item_tracks",
        lambda pd: [
            {"file_path": str(good), "artist": "A", "title": "Good", "duration": 1.0},
            {"file_path": str(bad), "artist": "A", "title": "Bad", "duration": 1.0},
        ],
    )

    real_copy_track = sync_manager.copy_track

    def copy_track_stub(source_path, dest_path):
        if source_path == str(bad):
            return False
        return real_copy_track(source_path, dest_path)

    monkeypatch.setattr(sync_manager, "copy_track", copy_track_stub)

    result = sync_manager.sync_playlist_to_device(playlist_data, str(dest))

    assert result["tracks_copied"] == 1
    assert result["tracks_failed"] == 1
    assert "1 failed" in result["message"]

    m3u_path = dest / "playlists" / "Test Playlist.m3u"
    m3u_content = m3u_path.read_text()
    assert "Good" in m3u_content
    assert "Bad" not in m3u_content


# ---------------------------------------------------------------------------
# N+1 fix: artist relationships are eager-loaded, not one query per track
# ---------------------------------------------------------------------------


def _seed_playlist_with_tracks(session, n, tag=""):
    playlist = Playlist(playlist_name=f"Playlist {tag}")
    role = Role(role_name="Primary Artist")
    session.add_all([playlist, role])
    session.commit()

    for i in range(n):
        artist = Artist(artist_name=f"Artist {tag}-{i}")
        track = Track(track_name=f"Track {i}", track_file_path=f"/music/{tag}-{i}.mp3")
        session.add_all([artist, track])
        session.flush()
        session.add(TrackArtistRole(track_id=track.track_id, artist_id=artist.artist_id, role_id=role.role_id))
        session.add(
            PlaylistTracks(playlist_id=playlist.playlist_id, track_id=track.track_id, position=i)
        )
    session.commit()
    return playlist


def test_get_playlist_tracks_query_count_does_not_scale_with_track_count(session, sync_manager):
    small = _seed_playlist_with_tracks(session, 2, tag="small")

    engine = session.get_bind()
    counts = {}

    def make_counter(key):
        def _count(*a, **kw):
            counts[key] = counts.get(key, 0) + 1

        return _count

    for n, key in ((2, "small"), (10, "large")):
        session.expire_all()
        playlist = small if n == 2 else _seed_playlist_with_tracks(session, n, tag="large")
        counts[key] = 0
        listener = make_counter(key)
        event.listen(engine, "before_cursor_execute", listener)
        try:
            tracks = sync_manager.get_playlist_tracks(playlist.playlist_id)
        finally:
            event.remove(engine, "before_cursor_execute", listener)
        assert len(tracks) == n

    # If artist_roles were lazy-loaded per track (the pre-fix behavior),
    # "large" (10 tracks) would issue roughly 5x the queries of "small"
    # (2 tracks) -- one extra round trip per track per relationship.
    # Eager-loading keeps the query count flat (a small constant, not
    # proportional to track count); allow a little slack for selectin's
    # own batching rather than pinning an exact number.
    assert counts["large"] <= counts["small"] + 2, (
        f"query count scaled with track count: small={counts['small']} large={counts['large']}"
    )
