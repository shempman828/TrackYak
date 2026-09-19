"""Tests for NewAlbumDialog.

Regression coverage for a BLOCKER: the dialog used to wire up a
_resize_timer whose timeout connected to _do_resize_art, a method that was
never defined on NewAlbumDialog. That made every "New Album" attempt raise
AttributeError immediately on construction. The dead timer setup was
removed; this test guards against it (or an equivalent) coming back.
"""

from src.album.edit.album_new import NewAlbumDialog


def test_new_album_dialog_constructs_without_raising(qapp):
    dialog = NewAlbumDialog(controller=None)

    assert dialog.album_name == ""
    assert dialog.release_year is None
    assert dialog.artist_name == ""
    assert dialog.is_compilation == 0
