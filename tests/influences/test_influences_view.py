"""Regression test for the "Add Influence" button: show_add_influence_dialog()
must be able to import AddInfluenceDialog. A bare `from influences_dialog
import AddInfluenceDialog` (missing the src.influences package prefix) raises
ModuleNotFoundError at runtime, which the surrounding except clause swallows
into a generic "Failed to open influence dialog" QMessageBox.
"""

from unittest.mock import MagicMock

from PySide6.QtWidgets import QDialog, QMessageBox, QWidget

from src.influences.influences_view import InfluencesView


def test_show_add_influence_dialog_opens_without_import_error(qapp, monkeypatch):
    view = InfluencesView.__new__(InfluencesView)
    QWidget.__init__(view)
    view.controller = MagicMock()
    view.controller.get.get_all_entities.return_value = []
    view.graph_view = MagicMock()

    critical_mock = MagicMock()
    monkeypatch.setattr(QMessageBox, "critical", critical_mock)
    monkeypatch.setattr(QDialog, "exec", lambda self: QDialog.Rejected)

    view.show_add_influence_dialog()

    critical_mock.assert_not_called()
