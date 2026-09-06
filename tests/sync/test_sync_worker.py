"""
SyncWorker orchestration: after the per-playlist copy loop it runs a prune
pass so the destination stops carrying files for playlists/moods the profile
no longer tracks -- but only when the profile opts in and the run wasn't
cancelled.

run() is driven synchronously here (not via start()), so the worker's signals
fire directly into the connected slots on the test thread.
"""

from unittest.mock import Mock

import pytest

from src.sync.sync_profile import SyncProfile
from src.sync.sync_worker import SyncWorker

pytestmark = pytest.mark.usefixtures("qapp")


def _manager():
    mgr = Mock()
    mgr.sync_playlist_to_device.return_value = {
        "playlist_name": "P",
        "success": True,
        "message": "1 copied, 0 skipped",
        "tracks_copied": 1,
        "tracks_skipped": 0,
        "tracks_failed": 0,
        "tracks_transcoded": 0,
        "total_tracks": 1,
        "failures": [],
    }
    mgr.prune_device.return_value = {
        "removed_tracks": ["Artist - Old.mp3"],
        "removed_playlists": ["Old.m3u"],
        "removed_count": 2,
    }
    return mgr


_ITEMS = [{"kind": "playlist", "name": "P", "playlist_id": 1, "track_count": 1}]


def test_prune_runs_after_copy_loop_when_profile_opts_in():
    mgr = _manager()
    profile = SyncProfile(name="x", path="/dest", prune_untracked=True)
    worker = SyncWorker(mgr, _ITEMS, profile)
    seen = []
    worker.prune_complete.connect(seen.append)

    worker.run()

    mgr.prune_device.assert_called_once()
    called_profile, called_items = mgr.prune_device.call_args[0][:2]
    assert called_profile is profile
    assert called_items is _ITEMS
    assert seen == [mgr.prune_device.return_value]
    assert worker.prune_result["removed_count"] == 2


def test_prune_skipped_when_profile_opts_out():  # AC6
    mgr = _manager()
    worker = SyncWorker(mgr, _ITEMS, SyncProfile(name="x", path="/dest", prune_untracked=False))

    worker.run()

    mgr.prune_device.assert_not_called()
    assert worker.prune_result is None


def test_prune_skipped_when_sync_cancelled():  # AC7
    mgr = _manager()
    worker = SyncWorker(mgr, _ITEMS, SyncProfile(name="x", path="/dest", prune_untracked=True))
    worker.request_cancel()

    worker.run()

    mgr.prune_device.assert_not_called()
    assert worker.prune_result is None
