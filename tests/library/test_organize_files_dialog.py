"""Regression tests for the OrganizeFilesDialog.

Guards the split of the former combined "Manage Library" dialog into two
separate Tools menu actions: this dialog now only organizes files, and the
metadata write flow lives entirely in show_metadata_write_dialog. The old
in-dialog "Update Metadata" button and its half-wired worker plumbing must
stay gone.
"""

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
