"""Regression test for AlbumEditor.closeEvent.

The dialog used to crash with DetachedInstanceError when the app's DB
session closed before this dialog's closeEvent fired (e.g. on app
shutdown, MainWindow.closeEvent closes the shared session while an
AlbumEditor is still open as a separate top-level window). closeEvent
now catches that and just accepts the close instead of crashing.
"""

from unittest.mock import MagicMock, patch

from sqlalchemy.orm.exc import DetachedInstanceError

from src.album.edit.base_album_edit import AlbumEditor


def test_close_event_accepts_when_album_is_detached(qapp):
    dialog = AlbumEditor.__new__(AlbumEditor)
    event = MagicMock()

    with patch.object(AlbumEditor, "_has_unsaved_changes", side_effect=DetachedInstanceError("x")):
        dialog.closeEvent(event)

    event.accept.assert_called_once()
    event.ignore.assert_not_called()
