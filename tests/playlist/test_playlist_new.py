"""Tests for PlaylistCreateDialog's validate-before-accept behavior.

Regression: the dialog used to accept (and close) on a blank name with no
check at all. The caller then rejected the empty name after the dialog was
already gone, silently discarding the description the user had typed.
"""

from PySide6.QtWidgets import QDialog, QDialogButtonBox, QMessageBox

from src.playlist.playlist_new import PlaylistCreateDialog


def test_blank_name_does_not_accept(qapp, monkeypatch):
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: None)
    dialog = PlaylistCreateDialog()
    dialog.name_input.setText("   ")
    dialog.desc_input.setPlainText("some text")

    dialog._validate_and_accept()

    assert dialog.result() != QDialog.Accepted


def test_valid_name_accepts(qapp):
    dialog = PlaylistCreateDialog()
    dialog.name_input.setText("My Playlist")

    dialog._validate_and_accept()

    assert dialog.result() == QDialog.Accepted


def test_ok_button_is_labeled_create(qapp):
    dialog = PlaylistCreateDialog()

    assert dialog.buttons.button(QDialogButtonBox.Ok).text() == "Create"
