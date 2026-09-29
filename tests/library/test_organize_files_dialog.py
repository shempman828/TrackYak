"""Regression tests for the OrganizeFilesDialog.

Guards the split of the former combined "Manage Library" dialog into two
separate Tools menu actions: this dialog now only organizes files, and the
metadata write flow lives entirely in show_metadata_write_dialog. The old
in-dialog "Update Metadata" button and its half-wired worker plumbing must
stay gone.
"""

from unittest.mock import Mock

from PySide6.QtGui import QCloseEvent

from src.library.organize_files_dialog import OrganizeFilesDialog


def test_dialog_has_no_metadata_section(qapp):
    dlg = OrganizeFilesDialog(None)
    try:
        # the metadata section and its controls are gone entirely
        assert not hasattr(dlg, "btn_update_metadata")
        assert not hasattr(dlg, "_build_metadata_section")

        # superseded worker plumbing from the even older in-dialog flow
        assert not hasattr(dlg, "metadata_updater")
        assert not hasattr(dlg, "metadata_progress")
        assert not hasattr(dlg, "metadata_status")
        assert not hasattr(dlg, "_cancel_metadata_update")
        assert not hasattr(dlg, "_reset_metadata_ui")
        assert not hasattr(dlg, "btn_cancel_metadata")

        # organization flow is still wired
        assert hasattr(dlg, "org_progress")
        assert hasattr(dlg, "org_status")
        assert hasattr(dlg, "btn_cancel_organize")
        assert dlg.windowTitle() == "Organize Files"
    finally:
        dlg.deleteLater()


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


def test_close_with_no_worker_does_not_error(qapp):
    dlg = OrganizeFilesDialog(None)
    try:
        dlg.closeEvent(QCloseEvent())  # self.organizer is None -- must not raise
    finally:
        dlg.deleteLater()


def _dialog_with_signal_spy():
    dlg = OrganizeFilesDialog(Mock())
    emitted = []
    dlg.library_modified.connect(lambda: emitted.append(True))
    return dlg, emitted


def test_completion_with_moves_signals_library_modified(qapp):
    dlg, emitted = _dialog_with_signal_spy()
    try:
        dlg._organization_complete(True, 3)

        assert emitted == [True]
    finally:
        dlg.deleteLater()


def test_cancel_after_committed_moves_still_signals_library_modified(qapp, monkeypatch):
    """A mid-run cancel still committed the moves done so far, so the views must reload (regression)."""
    monkeypatch.setattr("src.library.organize_files_dialog.QMessageBox.warning", Mock())
    dlg, emitted = _dialog_with_signal_spy()
    try:
        dlg._organization_complete(False, 2)

        assert emitted == [True]
    finally:
        dlg.deleteLater()


def test_completion_without_moves_does_not_signal(qapp):
    dlg, emitted = _dialog_with_signal_spy()
    try:
        dlg._organization_complete(True, 0)

        assert emitted == []
    finally:
        dlg.deleteLater()
