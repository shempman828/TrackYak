"""
SyncExecutionMixin rendering: the prune pass is surfaced to the user -- the
confirm dialog warns that untracked files will be removed, and the text log
(the Activity page's "Show log" section) lists what got removed plus a
running total.
"""

from unittest.mock import Mock

from PySide6.QtWidgets import QTextEdit
import pytest

import src.sync.sync_execution_mixin as sem
from src.sync.sync_execution_mixin import SyncExecutionMixin
from src.sync.sync_profile import SyncProfile

pytestmark = pytest.mark.usefixtures("qapp")


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
