"""OrganizeFilesDialog and FileOrganizer: cancel on close, library-modified
signalling, and count-only progress text.
"""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from PySide6.QtGui import QCloseEvent
import pytest

from src.library.organize.file_manager import FileOrganizer
from src.library.organize.organize_files_dialog import OrganizeFilesDialog

# ---------------------------------------------------------------------------
# Regression tests for the OrganizeFilesDialog.
# ---------------------------------------------------------------------------


def test_close_while_organizing_cancels_the_worker(qapp):
    """Closing the dialog mid-scan must cancel the FileOrganizer thread instead of orphaning it (regression)."""
    dlg = OrganizeFilesDialog(None)
    try:
        fake_worker = Mock()
        fake_worker.isRunning.return_value = True
        dlg.organizer = fake_worker

        dlg.closeEvent(QCloseEvent())

        fake_worker.request_cancel.assert_called_once()
        fake_worker.wait.assert_called_once()
    finally:
        dlg.deleteLater()


def _dialog_with_signal_spy():
    dlg = OrganizeFilesDialog(Mock())
    emitted = []
    dlg.library_modified.connect(lambda: emitted.append(True))
    return dlg, emitted


@pytest.mark.parametrize(
    ("success", "moved", "expected"),
    [(True, 3, [True]), (False, 2, [True]), (True, 0, [])],
    ids=["moves", "cancel-after-moves", "no-moves"],
)
def test_completion_signals_library_modified_only_when_files_moved(qapp, monkeypatch, success, moved, expected):
    # A mid-run cancel still committed the moves done so far, so the views must reload.
    monkeypatch.setattr("src.library.organize.organize_files_dialog.QMessageBox.warning", Mock())
    dlg, emitted = _dialog_with_signal_spy()
    try:
        dlg._organization_complete(success, moved)

        assert emitted == expected
    finally:
        dlg.deleteLater()


# ---------------------------------------------------------------------------
# Regression tests for FileOrganizer progress reporting.
# ---------------------------------------------------------------------------


def test_analyze_progress_reports_counts_not_track_names(qapp):
    tracks = [SimpleNamespace(track_name="Secret Song", track_file_path=None), SimpleNamespace(track_name="Another Song", track_file_path=None)]
    organizer = FileOrganizer(root=Path("/tmp"), controller=Mock())
    messages = []
    organizer.progress_updated.connect(lambda percent, text: messages.append(text))

    organizer._analyze_organization(tracks)

    assert messages == ["Analyzing files… 1/2", "Analyzing files… 2/2"]
    assert not any(track.track_name in msg for track in tracks for msg in messages)
