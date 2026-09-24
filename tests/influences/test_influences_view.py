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


def test_toolbar_keeps_its_hint_height_when_view_is_tall(qapp, monkeypatch):
    """The toolbar and graph were both Preferred with no stretch factor, so
    the view's QVBoxLayout split the spare height between them and the
    toolbar grew to ~half the window, with empty space above and below the
    controls. The graph must take all spare height."""
    from PySide6.QtCore import Signal

    class StubGraph(QWidget):
        graph_updated = Signal()

        def __init__(self, controller):
            super().__init__()
            self.node_names = {}

    monkeypatch.setattr("src.influences.influences_view.InfluenceGraphView", StubGraph)
    monkeypatch.setattr(InfluencesView, "show_global_view", lambda self: None)

    view = InfluencesView(controller=None)
    view.resize(1200, 900)
    view.show()
    qapp.processEvents()

    toolbar = view.findChild(QWidget, "InfluencesToolbar")
    assert toolbar.height() == toolbar.sizeHint().height()
    assert view.graph_view.height() > view.height() - 2 * toolbar.height()
