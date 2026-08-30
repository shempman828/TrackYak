"""Regression tests for FileManager dialog.

Guards the removal of the dead half-wired in-dialog metadata-update flow
(item 455 pulled the progress/complete handlers; this finished the job).
The live path delegates entirely to show_metadata_write_dialog, so the
old worker reference, its cancel/reset helpers, and the Cancel button
rendered next to "Update Metadata" must stay gone.
"""

from src.file_management.file_manager_dialog import FileManager


def test_metadata_section_has_no_dead_update_flow(qapp):
    dlg = FileManager(None)
    try:
        # superseded worker plumbing
        assert not hasattr(dlg, "metadata_updater")
        assert not hasattr(dlg, "metadata_progress")
        assert not hasattr(dlg, "metadata_status")
        assert not hasattr(dlg, "_cancel_metadata_update")
        assert not hasattr(dlg, "_reset_metadata_ui")

        # the dead clickable control
        assert not hasattr(dlg, "btn_cancel_metadata")

        # live path is still wired
        assert hasattr(dlg, "btn_update_metadata")
    finally:
        dlg.deleteLater()
