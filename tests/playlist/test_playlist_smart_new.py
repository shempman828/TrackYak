"""Tests for SmartPlaylistCreateDialog's validate-before-accept behavior.

Regressions covered:
- A blank name used to accept (and close) the dialog with no check,
  silently discarding every criteria row the user had built up.
- A criteria row whose operator needs a value but has none used to save
  fine, producing a playlist that matches nothing with no explanation.
- get_data() now also returns the auto-refresh flag.
"""

from PySide6.QtWidgets import QDialog, QMessageBox
import pytest

from src.playlist.playlist_smart_new import SmartPlaylistCreateDialog


@pytest.fixture(autouse=True)
def _no_blocking_message_boxes(monkeypatch):
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: None)


def _select_no_value_operator(criteria_widget):
    """Pick an operator ("is empty"/"has a value") that needs no input,
    so the row is valid regardless of which field type it starts on."""
    combo = criteria_widget.operator_combo
    for i in range(combo.count()):
        if combo.itemData(i) in ("isnull", "notnull"):
            combo.setCurrentIndex(i)
            return
    raise AssertionError("no no-value operator found")


def test_blank_name_does_not_accept(qapp):
    dialog = SmartPlaylistCreateDialog()
    dialog.name_edit.setText("   ")
    _select_no_value_operator(dialog.criteria_widgets[0])

    dialog._on_ok_clicked()

    assert dialog.result() != QDialog.Accepted


def test_criteria_row_missing_value_does_not_accept(qapp):
    dialog = SmartPlaylistCreateDialog()
    dialog.name_edit.setText("My Smart Playlist")
    # Default row is left with its default (blank) value and an operator
    # that needs one.

    dialog._on_ok_clicked()

    assert dialog.result() != QDialog.Accepted


def test_valid_form_accepts_and_returns_auto_refresh(qapp):
    dialog = SmartPlaylistCreateDialog()
    dialog.name_edit.setText("My Smart Playlist")
    _select_no_value_operator(dialog.criteria_widgets[0])
    dialog.auto_refresh_check.setChecked(True)

    dialog._on_ok_clicked()

    assert dialog.result() == QDialog.Accepted
    name, _description, _logic, _criteria, auto_refresh = dialog.get_data()
    assert name == "My Smart Playlist"
    assert auto_refresh is True
