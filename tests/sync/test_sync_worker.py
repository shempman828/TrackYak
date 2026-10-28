"""Sync execution: SyncWorker and the view's sync execution mixin."""

from unittest.mock import Mock

from PySide6.QtWidgets import QTextEdit
import pytest

import src.sync.sync_execution_mixin as sem
from src.sync.sync_execution_mixin import SyncExecutionMixin
from src.sync.sync_profile import SyncProfile
from src.sync.sync_worker import SyncWorker

# ---------------------------------------------------------------------------
# SyncWorker orchestration: after the per-playlist copy loop it runs a prune
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# SyncExecutionMixin rendering: the prune pass is surfaced to the user -- the
# ---------------------------------------------------------------------------


class _Host(SyncExecutionMixin):
    def __init__(self):
        self.sync_log = QTextEdit()
        self.current_action = Mock()
        self.progress_bar = Mock()
        self.status_manager = Mock()
        self._set_sync_ui_state = Mock()
        self._show_sync_result = Mock()
        self.activity = Mock()
        self.sync_worker = None
        self._selection_totals = (3, 0, 0, 0.0)


def test_on_prune_complete_lists_removed_files():  # AC10
    host = _Host()
    host._on_prune_complete({"removed_tracks": ["Artist - A.mp3", "Artist - B.mp3"], "removed_playlists": ["Old.m3u"], "removed_count": 3})
    text = host.sync_log.toPlainText()
    assert "🗑  Removed 3 file(s) no longer in this profile" in text
    assert "- Artist - A.mp3" in text
    assert "- Old.m3u" in text
    assert host._prune_removed == 3


def test_on_prune_complete_silent_when_nothing_removed():  # AC10
    host = _Host()
    host._on_prune_complete({"removed_tracks": [], "removed_playlists": [], "removed_count": 0})
    assert host.sync_log.toPlainText() == ""
    assert host._prune_removed == 0


def test_sync_finished_summary_includes_removed_count(monkeypatch):  # AC10
    monkeypatch.setattr(sem, "show_status_message", Mock())
    host = _Host()
    host._prune_removed = 4
    host._on_sync_finished([{"success": True, "tracks_copied": 2, "tracks_skipped": 0}])
    assert "4 removed" in host.sync_log.toPlainText()


def test_sync_finished_summary_omits_removed_count_when_zero(monkeypatch):  # AC10
    monkeypatch.setattr(sem, "show_status_message", Mock())
    host = _Host()
    host._on_sync_finished([{"success": True, "tracks_copied": 2, "tracks_skipped": 0}])
    assert "removed" not in host.sync_log.toPlainText()


def _confirm_host(profile):
    host = _Host()
    host.current_profile = profile
    host.selected_items = [{"kind": "playlist", "name": "P", "track_count": 3}]
    return host


def _captured_confirm_text(monkeypatch, host):
    captured = {}

    def fake_question(_parent, _title, msg, *a, **kw):
        captured["msg"] = msg
        return sem.QMessageBox.No

    monkeypatch.setattr(sem.QMessageBox, "question", staticmethod(fake_question))
    host._start_sync()
    return captured["msg"]


def test_confirm_dialog_warns_about_removal_when_prune_on(monkeypatch):  # AC12
    host = _confirm_host(SyncProfile(name="x", path="/dest", prune_untracked=True))
    msg = _captured_confirm_text(monkeypatch, host)
    assert "no longer in this profile" in msg
    assert "removed from the destination" in msg


def test_confirm_dialog_has_no_removal_warning_when_prune_off(monkeypatch):  # AC12
    host = _confirm_host(SyncProfile(name="x", path="/dest", prune_untracked=False))
    msg = _captured_confirm_text(monkeypatch, host)
    assert "no longer in this profile" not in msg


def test_sync_failure_shows_error_headline_not_nothing_copied():
    host = _Host()
    host._on_sync_failed("disk on fire")
    host._on_sync_finished([])

    headline, tone = host._show_sync_result.call_args[0]
    assert headline == "Sync failed — disk on fire"
    assert tone == "error"
    host.status_manager.end_task.assert_called_once_with("Sync error: disk on fire", 5000)


def test_sync_finished_refreshes_cache_button_label():
    host = _Host()
    host._update_cache_button_label = Mock()
    host._on_sync_finished([{"success": True, "tracks_copied": 1, "tracks_skipped": 0}])
    host._update_cache_button_label.assert_called_once()


def test_worker_gets_a_snapshot_of_the_profile(monkeypatch):
    started = []

    class _Worker:
        def __init__(self, _mgr, items, profile):
            self.items, self.profile = items, profile
            for name in ("progress", "playlist_complete", "prune_complete", "notice", "failed", "sync_finished"):
                setattr(self, name, Mock())

        def start(self):
            started.append(self)

    monkeypatch.setattr(sem, "SyncWorker", _Worker)
    monkeypatch.setattr(sem.QMessageBox, "question", staticmethod(lambda *a, **kw: sem.QMessageBox.Yes))
    profile = SyncProfile(name="x", path="/dest")
    host = _confirm_host(profile)
    host.sync_manager = Mock()

    host._start_sync()
    profile.path = "/elsewhere"  # an edit made while the run is going

    assert started[0].profile.path == "/dest"
    assert started[0].items is not host.selected_items
