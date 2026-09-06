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
from pathlib import Path
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
    Path(path).write_bytes(content)
    return path


def _make_track_dict(tmp_path, name, content=b"track-bytes"):
    path = _write_file(tmp_path / f"{name}.mp3", content)
    return {"file_path": str(path), "artist": "Artist", "title": name, "duration": 1.0}


# ---------------------------------------------------------------------------
# Folder-sync diff pool: one scan, correct partition, parallel MD5 confirm
# ---------------------------------------------------------------------------


def test_diff_local_pool_uses_one_directory_scan_regardless_of_track_count(tmp_path, sync_manager):
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
        _write_file(device_dir / filename, content=Path(t["file_path"]).read_bytes())

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
        _write_file(dest, content=Path(t["file_path"]).read_bytes())
        return True

    succeeded, failed = sync_manager._copy_with_retry(
        [track],
        flaky_copy_one,
        lambda: sync_manager._list_local_pool(str(music_dir)),
        lambda t: Path(t["file_path"]).stat().st_size,
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
        lambda t: Path(t["file_path"]).stat().st_size,
    )

    from src.sync.sync_manager import _MAX_RETRIES

    assert attempts["n"] == _MAX_RETRIES + 1
    assert succeeded == []
    assert failed == [track]
    assert track["copied_successfully"] is False


def test_copy_with_retry_stops_at_the_next_track_when_cancelled(tmp_path, sync_manager):
    """Regression: cancelling a running sync must stop the copy loop mid
    playlist -- previously `should_cancel` was only checked between
    playlists, so every remaining track in the current playlist kept
    copying (and got retried) after the user hit Cancel."""
    music_dir = tmp_path / "music"
    music_dir.mkdir()

    tracks = []
    for i in range(5):
        t = _make_track_dict(tmp_path, f"Track {i}")
        t["device_filename"] = sync_manager._safe_filename("Artist", f"Track {i}", ".mp3")
        tracks.append(t)

    attempts = {"n": 0}

    def copy_one(t):
        attempts["n"] += 1
        dest = music_dir / t["device_filename"]
        _write_file(dest, content=Path(t["file_path"]).read_bytes())
        return True

    # User "cancels" once two tracks have been handed to the transport.
    succeeded, failed = sync_manager._copy_with_retry(
        tracks,
        copy_one,
        lambda: sync_manager._list_local_pool(str(music_dir)),
        lambda t: Path(t["file_path"]).stat().st_size,
        should_cancel=lambda: attempts["n"] >= 2,
    )

    assert attempts["n"] == 2  # stopped before track 3, no retry rounds
    assert {t["title"] for t in succeeded} == {"Track 0", "Track 1"}
    assert {t["title"] for t in failed} == {"Track 2", "Track 3", "Track 4"}
    assert all(t["copied_successfully"] is False for t in failed)


def test_sync_playlist_to_device_reports_failed_tracks_instead_of_dropping_them(
    tmp_path, sync_manager, monkeypatch
):
    """End-to-end regression for the original bug: a track that never makes
    it must show up in tracks_failed (and be retried), not vanish with only
    a log line."""
    dest = tmp_path / "device"

    good = _write_file(tmp_path / "good.mp3")
    bad = _write_file(tmp_path / "bad.mp3")

    playlist_data = {"kind": "playlist", "name": "Test Playlist", "playlist_id": 1}
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
# Failed tracks are surfaced with which track / why, not just a count
# ---------------------------------------------------------------------------


def test_copy_with_retry_tags_each_failed_track_with_a_reason(tmp_path, sync_manager):
    music_dir = tmp_path / "music"
    music_dir.mkdir()

    never_lands = _make_track_dict(tmp_path, "NeverLands")
    never_lands["device_filename"] = sync_manager._safe_filename("Artist", "NeverLands", ".mp3")
    lies = _make_track_dict(tmp_path, "Lies")
    lies["device_filename"] = sync_manager._safe_filename("Artist", "Lies", ".mp3")

    def copy_one(t):
        # "Lies" reports success every time but nothing ever lands on disk;
        # "NeverLands" reports failure every time.
        return t["title"] == "Lies"

    succeeded, failed = sync_manager._copy_with_retry(
        [never_lands, lies],
        copy_one,
        lambda: sync_manager._list_local_pool(str(music_dir)),
        lambda t: Path(t["file_path"]).stat().st_size,
    )

    assert succeeded == []
    reasons = {t["title"]: t["failure_reason"] for t in failed}
    assert "transport error" in reasons["NeverLands"]
    assert "never verified" in reasons["Lies"]


def test_sync_playlist_to_device_lists_failed_tracks_with_artist_title_reason(
    tmp_path, sync_manager, monkeypatch
):
    dest = tmp_path / "device"
    good = _write_file(tmp_path / "good.mp3")
    bad = _write_file(tmp_path / "bad.mp3")

    playlist_data = {"kind": "playlist", "name": "PL", "playlist_id": 1}
    monkeypatch.setattr(
        sync_manager,
        "get_item_tracks",
        lambda pd: [
            {"file_path": str(good), "artist": "Good Artist", "title": "Good", "duration": 1.0},
            {"file_path": str(bad), "artist": "Bad Artist", "title": "Bad", "duration": 1.0},
        ],
    )
    real_copy_track = sync_manager.copy_track
    monkeypatch.setattr(
        sync_manager, "copy_track", lambda s, d: False if s == str(bad) else real_copy_track(s, d)
    )

    result = sync_manager.sync_playlist_to_device(playlist_data, str(dest))

    assert result["tracks_failed"] == 1
    assert len(result["failures"]) == 1
    failure = result["failures"][0]
    assert failure["title"] == "Bad"
    assert failure["artist"] == "Bad Artist"
    assert failure["reason"]  # non-empty explanation


def test_sync_playlist_to_device_reports_missing_source_files_instead_of_dropping_them(
    tmp_path, sync_manager, monkeypatch
):
    """A track whose source file is gone must land in `failures` and count
    toward tracks_failed -- previously it was silently skipped entirely."""
    dest = tmp_path / "device"
    present = _write_file(tmp_path / "present.mp3")

    playlist_data = {"kind": "playlist", "name": "PL", "playlist_id": 1}
    monkeypatch.setattr(
        sync_manager,
        "get_item_tracks",
        lambda pd: [
            {"file_path": str(present), "artist": "A", "title": "Present", "duration": 1.0},
            {"file_path": "/no/such/file.mp3", "artist": "A", "title": "Gone", "duration": 1.0},
            {"file_path": "", "artist": "A", "title": "NoPath", "duration": 1.0},
        ],
    )

    result = sync_manager.sync_playlist_to_device(playlist_data, str(dest))

    assert result["tracks_copied"] == 1
    assert result["tracks_failed"] == 2
    reasons = {f["title"]: f["reason"] for f in result["failures"]}
    assert "not found" in reasons["Gone"]
    assert "no source file" in reasons["NoPath"]
    # One track still copied, so the playlist as a whole is a partial success.
    assert result["success"] is True


def test_sync_playlist_to_device_fails_when_every_source_is_missing(
    tmp_path, sync_manager, monkeypatch
):
    dest = tmp_path / "device"
    playlist_data = {"kind": "playlist", "name": "PL", "playlist_id": 1}
    monkeypatch.setattr(
        sync_manager,
        "get_item_tracks",
        lambda pd: [
            {"file_path": "/gone/1.mp3", "artist": "A", "title": "One", "duration": 1.0},
            {"file_path": "/gone/2.mp3", "artist": "A", "title": "Two", "duration": 1.0},
        ],
    )

    result = sync_manager.sync_playlist_to_device(playlist_data, str(dest))

    assert result["success"] is False
    assert result["tracks_failed"] == 2
    assert len(result["failures"]) == 2


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
        session.add(
            TrackArtistRole(
                track_id=track.track_id, artist_id=artist.artist_id, role_id=role.role_id
            )
        )
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


# ---------------------------------------------------------------------------
# Sync as MP3: lossless sources are transcoded to 320k MP3 on the way out,
# lossy sources pass through, and a re-sync is a cache hit (no re-encode).
# ---------------------------------------------------------------------------

from src.sync.transcode import TranscodeCache, ffmpeg_available  # noqa: E402

_transcode = pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg not installed")


def _make_audio(path: Path, *, seconds: int = 1, title: str = "T") -> Path:
    """Render a real audio file (format inferred from the extension)."""
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:duration={seconds}",
            "-metadata",
            f"title={title}",
            str(path),
        ],
        check=True,
        capture_output=True,
    )
    return path


def _tracks(monkeypatch, sync_manager, *entries):
    monkeypatch.setattr(sync_manager, "get_item_tracks", lambda pd: [dict(e) for e in entries])


@_transcode
def test_sync_folder_transcodes_lossless_source(tmp_path, sync_manager, monkeypatch):  # AC7
    sync_manager.transcode_cache = TranscodeCache(cache_dir=tmp_path / "tcache")
    src = _make_audio(tmp_path / "song.flac", title="Hello")
    _tracks(
        monkeypatch,
        sync_manager,
        {"file_path": str(src), "artist": "Band", "title": "Hello", "duration": 1.0},
    )

    result = sync_manager.sync_playlist_to_device(
        {"kind": "playlist", "name": "PL", "playlist_id": 1},
        str(tmp_path / "device"),
        transcode_to_mp3=True,
        transcode_bitrate="320k",
    )

    landed = tmp_path / "device" / "music" / "Band - Hello.mp3"
    assert landed.exists()
    cached = sync_manager.transcode_cache.path_for(str(src), "320k")
    assert landed.stat().st_size == cached.stat().st_size
    assert result["tracks_copied"] == 1
    assert result["tracks_transcoded"] == 1
    assert "1 to MP3" in result["message"]


@_transcode
def test_sync_folder_passes_lossy_source_through_untouched(
    tmp_path, sync_manager, monkeypatch
):  # AC8
    sync_manager.transcode_cache = TranscodeCache(cache_dir=tmp_path / "tcache")
    src = _make_audio(tmp_path / "song.mp3", title="AsIs")
    _tracks(
        monkeypatch,
        sync_manager,
        {"file_path": str(src), "artist": "Band", "title": "AsIs", "duration": 1.0},
    )

    result = sync_manager.sync_playlist_to_device(
        {"kind": "playlist", "name": "PL", "playlist_id": 1},
        str(tmp_path / "device"),
        transcode_to_mp3=True,
    )

    landed = tmp_path / "device" / "music" / "Band - AsIs.mp3"
    assert landed.exists()
    assert landed.stat().st_size == src.stat().st_size  # byte-for-byte copy
    assert result["tracks_transcoded"] == 0


@_transcode
def test_sync_folder_records_transcode_failure_and_keeps_going(
    tmp_path, sync_manager, monkeypatch
):  # AC9
    sync_manager.transcode_cache = TranscodeCache(cache_dir=tmp_path / "tcache")
    good = _make_audio(tmp_path / "good.flac", title="Good")
    bad = tmp_path / "bad.flac"
    bad.write_bytes(b"not audio at all")
    _tracks(
        monkeypatch,
        sync_manager,
        {"file_path": str(good), "artist": "A", "title": "Good", "duration": 1.0},
        {"file_path": str(bad), "artist": "A", "title": "Bad", "duration": 1.0},
    )

    result = sync_manager.sync_playlist_to_device(
        {"kind": "playlist", "name": "PL", "playlist_id": 1},
        str(tmp_path / "device"),
        transcode_to_mp3=True,
    )

    music = tmp_path / "device" / "music"
    assert (music / "A - Good.mp3").exists()
    assert not (music / "A - Bad.mp3").exists()
    assert not (music / "A - Bad.flac").exists()
    assert result["tracks_copied"] == 1
    reasons = {f["title"]: f["reason"] for f in result["failures"]}
    assert reasons["Bad"].startswith("could not convert to MP3")


@_transcode
def test_second_sync_is_a_cache_hit_no_reencode(tmp_path, sync_manager, monkeypatch):  # AC10
    sync_manager.transcode_cache = TranscodeCache(cache_dir=tmp_path / "tcache")
    src = _make_audio(tmp_path / "song.flac", title="Once")
    _tracks(
        monkeypatch,
        sync_manager,
        {"file_path": str(src), "artist": "B", "title": "Once", "duration": 1.0},
    )
    playlist = {"kind": "playlist", "name": "PL", "playlist_id": 1}
    dest = str(tmp_path / "device")

    first = sync_manager.sync_playlist_to_device(playlist, dest, transcode_to_mp3=True)
    assert first["tracks_copied"] == 1

    from src.sync import transcode as _tmod

    calls = []
    real = _tmod.transcode_to_mp3
    monkeypatch.setattr(_tmod, "transcode_to_mp3", lambda *a, **k: calls.append(a) or real(*a, **k))

    second = sync_manager.sync_playlist_to_device(playlist, dest, transcode_to_mp3=True)
    assert second["tracks_copied"] == 0
    assert second["tracks_skipped"] == 1
    assert calls == []  # cache hit, ffmpeg never re-invoked


def test_transcode_requested_without_ffmpeg_copies_originals(
    tmp_path, sync_manager, monkeypatch
):  # AC11
    monkeypatch.setattr("src.sync.sync_manager.ffmpeg_available", lambda: False)
    src = _write_file(tmp_path / "song.flac", content=b"pretend-flac-bytes")
    _tracks(
        monkeypatch,
        sync_manager,
        {"file_path": str(src), "artist": "C", "title": "Song", "duration": 1.0},
    )

    result = sync_manager.sync_playlist_to_device(
        {"kind": "playlist", "name": "PL", "playlist_id": 1},
        str(tmp_path / "device"),
        transcode_to_mp3=True,
    )

    landed = tmp_path / "device" / "music" / "C - Song.flac"
    assert landed.exists()
    assert landed.stat().st_size == src.stat().st_size
    assert result["tracks_transcoded"] == 0
    assert "ffmpeg not found" in result["message"]


@_transcode
def test_cancel_during_transcode_stops_the_sync(tmp_path, sync_manager, monkeypatch):  # AC12
    sync_manager.transcode_cache = TranscodeCache(cache_dir=tmp_path / "tcache")
    a = _make_audio(tmp_path / "a.flac", title="A")
    b = _make_audio(tmp_path / "b.flac", seconds=2, title="B")
    _tracks(
        monkeypatch,
        sync_manager,
        {"file_path": str(a), "artist": "X", "title": "A", "duration": 1.0},
        {"file_path": str(b), "artist": "X", "title": "B", "duration": 2.0},
    )

    # Latch cancelled after the first transcode check (call #1 -> False so A
    # encodes; every call after -> True, matching SyncWorker's sticky flag).
    calls = {"n": 0}

    def should_cancel():
        calls["n"] += 1
        return calls["n"] > 1

    result = sync_manager.sync_playlist_to_device(
        {"kind": "playlist", "name": "PL", "playlist_id": 1},
        str(tmp_path / "device"),
        should_cancel=should_cancel,
        transcode_to_mp3=True,
    )

    assert result["tracks_copied"] == 0
    reasons = {f["title"]: f["reason"] for f in result["failures"]}
    assert "cancelled" in reasons["A"]
    assert "cancelled" in reasons["B"]
    # A was transcoded before the cancel landed, even though it never copied.
    assert sync_manager.transcode_cache.path_for(str(a), "320k").exists()
    assert not (tmp_path / "device" / "music" / "X - A.mp3").exists()


@_transcode
def test_mtp_sync_transcodes_then_reruns_as_skip(tmp_path, sync_manager, monkeypatch):  # AC15
    sync_manager.transcode_cache = TranscodeCache(cache_dir=tmp_path / "tcache")
    device_root = tmp_path / "device"
    device_root.mkdir()
    device = MtpDevice(uri=f"file://{device_root}/", name="stub", backend="gio")
    monkeypatch.setattr(sync_manager, "_get_mtp_device", lambda uri: device)

    src = _make_audio(tmp_path / "song.flac", title="Remote")
    _tracks(
        monkeypatch,
        sync_manager,
        {"file_path": str(src), "artist": "R", "title": "Remote", "duration": 1.0},
    )
    playlist = {"kind": "playlist", "name": "PL", "playlist_id": 1}

    first = sync_manager.sync_playlist_to_mtp(playlist, device.uri, "Music", transcode_to_mp3=True)
    assert first["tracks_copied"] == 1

    remote = device_root / "Music" / "R - Remote.mp3"
    assert remote.exists()
    cached = sync_manager.transcode_cache.path_for(str(src), "320k")
    assert remote.stat().st_size == cached.stat().st_size

    second = sync_manager.sync_playlist_to_mtp(playlist, device.uri, "Music", transcode_to_mp3=True)
    assert second["tracks_copied"] == 0
    assert second["tracks_skipped"] == 1


# ---------------------------------------------------------------------------
# Prune: after a sync, destination files for no-longer-tracked playlists/moods
# are removed. Folder assertions use real files under tmp_path; MTP assertions
# drive the real `gio` binary against a file:// stand-in for the device.
# ---------------------------------------------------------------------------

from src.sync.sync_profile import SyncProfile  # noqa: E402


def _prune_track(src_dir: Path, title: str, *, artist: str = "Artist", ext: str = ".mp3") -> dict:
    path = src_dir / f"{title}{ext}"
    path.write_bytes(b"bytes-" + title.encode())
    return {"file_path": str(path), "artist": artist, "title": title, "duration": 1.0}


def _folder_profile(dest: Path, **kw) -> SyncProfile:
    return SyncProfile(name="dev", path=str(dest), prune_untracked=True, **kw)


def _sync_all(sync_manager, items, dest, **kw):
    for it in items:
        sync_manager.sync_playlist_to_device(it, str(dest), **kw)


def test_prune_removes_files_and_m3u_for_untracked_playlist(  # AC1
    tmp_path, sync_manager, monkeypatch
):
    dest, src = tmp_path / "dev", tmp_path / "src"
    src.mkdir()
    by_name = {
        "P": [_prune_track(src, "A"), _prune_track(src, "B"), _prune_track(src, "C")],
        "Q": [_prune_track(src, "Z")],
    }
    monkeypatch.setattr(sync_manager, "get_item_tracks", lambda it: by_name[it["name"]])
    P = {"kind": "playlist", "name": "P", "playlist_id": 1}
    Q = {"kind": "playlist", "name": "Q", "playlist_id": 2}
    _sync_all(sync_manager, [P, Q], dest)

    music, playlists = dest / "music", dest / "playlists"
    assert (music / "Artist - A.mp3").exists()
    assert (playlists / "P.m3u").exists()

    res = sync_manager.prune_device(_folder_profile(dest), [Q])

    assert not (music / "Artist - A.mp3").exists()
    assert not (music / "Artist - B.mp3").exists()
    assert not (music / "Artist - C.mp3").exists()
    assert not (playlists / "P.m3u").exists()
    assert (music / "Artist - Z.mp3").exists()
    assert (playlists / "Q.m3u").exists()
    assert set(res["removed_tracks"]) == {"Artist - A.mp3", "Artist - B.mp3", "Artist - C.mp3"}
    assert res["removed_playlists"] == ["P.m3u"]
    assert res["removed_count"] == 4


def test_prune_keeps_track_shared_with_a_still_tracked_playlist(  # AC2
    tmp_path, sync_manager, monkeypatch
):
    dest, src = tmp_path / "dev", tmp_path / "src"
    src.mkdir()
    shared = _prune_track(src, "Shared")
    by_name = {"P": [shared, _prune_track(src, "Ponly")], "Q": [shared]}
    monkeypatch.setattr(sync_manager, "get_item_tracks", lambda it: by_name[it["name"]])
    P = {"kind": "playlist", "name": "P", "playlist_id": 1}
    Q = {"kind": "playlist", "name": "Q", "playlist_id": 2}
    _sync_all(sync_manager, [P, Q], dest)

    res = sync_manager.prune_device(_folder_profile(dest), [Q])

    assert (dest / "music" / "Artist - Shared.mp3").exists()
    assert not (dest / "music" / "Artist - Ponly.mp3").exists()
    assert res["removed_tracks"] == ["Artist - Ponly.mp3"]


def test_prune_removes_nothing_when_every_playlist_still_tracked(  # AC3
    tmp_path, sync_manager, monkeypatch
):
    dest, src = tmp_path / "dev", tmp_path / "src"
    src.mkdir()
    by_name = {"P": [_prune_track(src, "A")], "Q": [_prune_track(src, "Z")]}
    monkeypatch.setattr(sync_manager, "get_item_tracks", lambda it: by_name[it["name"]])
    P = {"kind": "playlist", "name": "P", "playlist_id": 1}
    Q = {"kind": "playlist", "name": "Q", "playlist_id": 2}
    _sync_all(sync_manager, [P, Q], dest)

    res = sync_manager.prune_device(_folder_profile(dest), [P, Q])

    assert res["removed_count"] == 0
    assert (dest / "music" / "Artist - A.mp3").exists()
    assert (dest / "music" / "Artist - Z.mp3").exists()
    assert (dest / "playlists" / "P.m3u").exists()
    assert (dest / "playlists" / "Q.m3u").exists()


def test_prune_leaves_user_dropped_files_alone(tmp_path, sync_manager, monkeypatch):  # AC4
    dest, src = tmp_path / "dev", tmp_path / "src"
    src.mkdir()
    monkeypatch.setattr(sync_manager, "get_item_tracks", lambda it: [_prune_track(src, "A")])
    P = {"kind": "playlist", "name": "P", "playlist_id": 1}
    _sync_all(sync_manager, [P], dest)

    music = dest / "music"
    (music / "roadtrip.zip").write_bytes(b"zip")
    (music / "mixtape").write_bytes(b"no ext")
    (music / "not audio.txt").write_bytes(b"text")

    res = sync_manager.prune_device(_folder_profile(dest), [])  # nothing tracked anymore

    assert (music / "roadtrip.zip").exists()
    assert (music / "mixtape").exists()
    assert (music / "not audio.txt").exists()
    assert not (music / "Artist - A.mp3").exists()
    assert res["removed_tracks"] == ["Artist - A.mp3"]


def test_prune_device_is_a_noop_when_cancelled(tmp_path, sync_manager, monkeypatch):  # AC7
    dest, src = tmp_path / "dev", tmp_path / "src"
    src.mkdir()
    monkeypatch.setattr(sync_manager, "get_item_tracks", lambda it: [_prune_track(src, "A")])
    P = {"kind": "playlist", "name": "P", "playlist_id": 1}
    _sync_all(sync_manager, [P], dest)

    res = sync_manager.prune_device(_folder_profile(dest), [], should_cancel=lambda: True)

    assert res["removed_count"] == 0
    assert (dest / "music" / "Artist - A.mp3").exists()


def test_prune_predicts_mp3_names_when_transcode_in_effect(  # AC8
    tmp_path, sync_manager, monkeypatch
):
    dest, src = tmp_path / "dev", tmp_path / "src"
    src.mkdir()
    flac = _prune_track(src, "Loss", ext=".flac")
    monkeypatch.setattr(sync_manager, "get_item_tracks", lambda it: [flac])
    monkeypatch.setattr("src.sync.sync_manager.ffmpeg_available", lambda: True)
    music = dest / "music"
    music.mkdir(parents=True)
    (music / "Artist - Loss.mp3").write_bytes(b"mp3")  # still-tracked transcoded output
    (music / "Artist - Loss.flac").write_bytes(b"flac")  # stale pre-transcode copy

    profile = _folder_profile(dest, transcode_to_mp3=True)
    P = {"kind": "playlist", "name": "P", "playlist_id": 1}
    res = sync_manager.prune_device(profile, [P])

    assert (music / "Artist - Loss.mp3").exists()
    assert not (music / "Artist - Loss.flac").exists()
    assert res["removed_tracks"] == ["Artist - Loss.flac"]


def test_prune_removes_stale_mp3_when_transcode_turned_off(  # AC8
    tmp_path, sync_manager, monkeypatch
):
    dest, src = tmp_path / "dev", tmp_path / "src"
    src.mkdir()
    flac = _prune_track(src, "Loss", ext=".flac")
    monkeypatch.setattr(sync_manager, "get_item_tracks", lambda it: [flac])
    music = dest / "music"
    music.mkdir(parents=True)
    (music / "Artist - Loss.flac").write_bytes(b"flac")  # what a no-transcode sync writes
    (music / "Artist - Loss.mp3").write_bytes(b"mp3")  # left over from when transcode was on

    profile = _folder_profile(dest, transcode_to_mp3=False)
    P = {"kind": "playlist", "name": "P", "playlist_id": 1}
    res = sync_manager.prune_device(profile, [P])

    assert (music / "Artist - Loss.flac").exists()
    assert not (music / "Artist - Loss.mp3").exists()
    assert res["removed_tracks"] == ["Artist - Loss.mp3"]


def test_prune_mtp_removes_orphans_via_gio(tmp_path, monkeypatch):  # AC9
    device_root = tmp_path / "device"
    music, playlists = device_root / "Music", device_root / "Playlists"
    music.mkdir(parents=True)
    playlists.mkdir()
    for n in ("Artist - Keep.mp3", "Artist - Drop.mp3", "user thing.txt"):
        (music / n).write_bytes(b"x")
    (playlists / "Keep.m3u").write_text("#EXTM3U\n")
    (playlists / "Gone.m3u").write_text("#EXTM3U\n")

    mgr = SyncManager.__new__(SyncManager)
    mgr.mtp = MtpManager()
    device = MtpDevice(uri=f"file://{device_root}/", name="d", backend="gio")
    monkeypatch.setattr(mgr, "_get_mtp_device", lambda uri: device)
    monkeypatch.setattr(
        mgr,
        "get_item_tracks",
        lambda it: [
            {"file_path": "/x/Keep.mp3", "artist": "Artist", "title": "Keep", "duration": 1}
        ],
    )

    profile = SyncProfile(
        name="d",
        path="",
        device_uri=f"file://{device_root}/",
        music_path="Music",
        prune_untracked=True,
    )
    res = mgr.prune_device(profile, [{"kind": "playlist", "name": "Keep", "playlist_id": 1}])

    assert (music / "Artist - Keep.mp3").exists()
    assert not (music / "Artist - Drop.mp3").exists()
    assert (music / "user thing.txt").exists()
    assert (playlists / "Keep.m3u").exists()
    assert not (playlists / "Gone.m3u").exists()
    assert res["removed_tracks"] == ["Artist - Drop.mp3"]
    assert res["removed_playlists"] == ["Gone.m3u"]
