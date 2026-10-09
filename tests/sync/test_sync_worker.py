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
    mgr.prune_device.return_value = {"removed_tracks": ["Artist - Old.mp3"], "removed_playlists": ["Old.m3u"], "removed_count": 2}
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


# -- transcode cache eviction (idea 57) ------------------------------------ AC10


def test_transcode_cache_evicted_after_run_when_transcoding_on(monkeypatch):
    from src.sync import sync_worker

    monkeypatch.setattr(sync_worker.app_config, "get_transcode_cache_max_mb", lambda: 3)
    mgr = _manager()
    mgr.transcode_cache.enforce_limit.return_value = {"evicted": 0, "freed_bytes": 0, "swept_parts": 0}
    profile = SyncProfile(name="x", path="/dest", transcode_to_mp3=True)
    worker = SyncWorker(mgr, _ITEMS, profile)

    worker.run()

    mgr.transcode_cache.enforce_limit.assert_called_once_with(3 * 1024 * 1024)


def test_transcode_cache_not_evicted_when_transcoding_off():  # AC10
    mgr = _manager()
    worker = SyncWorker(mgr, _ITEMS, SyncProfile(name="x", path="/dest", transcode_to_mp3=False))

    worker.run()

    mgr.transcode_cache.enforce_limit.assert_not_called()


def test_transcode_cache_not_evicted_when_run_cancelled():  # AC10
    mgr = _manager()
    worker = SyncWorker(mgr, _ITEMS, SyncProfile(name="x", path="/dest", transcode_to_mp3=True))
    worker.request_cancel()

    worker.run()

    mgr.transcode_cache.enforce_limit.assert_not_called()


# -- signals, failures and progress ------------------------------------------


def test_worker_does_not_shadow_qthread_finished():
    from PySide6.QtCore import QThread

    assert SyncWorker.finished is QThread.finished
    assert hasattr(SyncWorker, "sync_finished")


def test_unexpected_error_emits_failed_then_partial_results():
    mgr = _manager()
    mgr.prune_device.side_effect = RuntimeError("disk on fire")
    worker = SyncWorker(mgr, _ITEMS, SyncProfile(name="x", path="/dest", prune_untracked=True))
    failed, finished = [], []
    worker.failed.connect(failed.append)
    worker.sync_finished.connect(finished.append)

    worker.run()

    assert failed == ["disk on fire"]
    assert len(finished) == 1
    assert len(finished[0]) == 1  # the playlist that finished before the error


def test_clear_errors_are_emitted_as_notices():
    mgr = _manager()
    mgr.clear_device_folder.return_value = ["Could not clear /dest/music: denied"]
    worker = SyncWorker(mgr, _ITEMS, SyncProfile(name="x", path="/dest", clear_before_sync=True))
    notices = []
    worker.notice.connect(notices.append)

    worker.run()

    assert notices == ["Could not clear /dest/music: denied"]


def test_item_progress_maps_onto_one_overall_scale():
    from src.sync.sync_worker import PROGRESS_UNITS_PER_ITEM

    items = [dict(_ITEMS[0], playlist_id=n) for n in (1, 2)]
    worker = SyncWorker(_manager(), items, SyncProfile(name="x", path="/dest"))
    seen = []
    worker.progress.connect(lambda cur, total, _msg: seen.append((cur, total)))

    worker._item_index = 1
    worker._progress_callback(5, 10, "Copying: t")

    assert seen == [(int(1.5 * PROGRESS_UNITS_PER_ITEM), 2 * PROGRESS_UNITS_PER_ITEM)]
