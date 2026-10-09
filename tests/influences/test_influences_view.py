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
        busy_changed = Signal(bool)

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


def _bare_view(qapp):
    from src.common.widgets.entity_completer_edit import EntityCompleterEdit

    view = InfluencesView.__new__(InfluencesView)
    QWidget.__init__(view)
    view.controller = MagicMock()
    view.graph_view = MagicMock()
    view.find_field = EntityCompleterEdit("Find artist…", view)
    view._last_focus = None
    return view


def test_add_dialog_result_goes_to_graph_as_one_pair(qapp, monkeypatch):
    # The old code re-added the "last 5" rows of an unordered query.
    from src.influences import influences_view as module

    view = _bare_view(qapp)
    view.controller.get.get_all_entities.return_value = []

    class FakeDialog:
        def __init__(self, *_args):
            self.added_influence = ((1, "A"), (2, "B"))

        def exec(self):
            return QDialog.Accepted

    monkeypatch.setattr(module, "AddInfluenceDialog", FakeDialog)

    view.show_add_influence_dialog()

    view.graph_view.add_influence.assert_called_once_with((1, "A"), (2, "B"))


def test_remove_rows_skip_orphans_and_carry_description(qapp):
    from types import SimpleNamespace

    view = _bare_view(qapp)
    good = SimpleNamespace(influencer_id=1, influenced_id=2, influencer=SimpleNamespace(artist_name="A"), influenced=SimpleNamespace(artist_name="B"), description="Modal")
    orphan = SimpleNamespace(influencer_id=3, influenced_id=4, influencer=None, influenced=SimpleNamespace(artist_name="D"), description=None)
    view.controller.get.get_all_entities.return_value = [good, orphan]

    rows = view._load_influence_rows()

    assert rows == [{"influencer_id": 1, "influenced_id": 2, "influencer_name": "A", "influenced_name": "B", "description": "Modal"}]


def test_find_focuses_once_for_pick_plus_enter(qapp):
    view = _bare_view(qapp)
    view.find_field.setText("Miles Davis")

    view._focus_typed_artist()
    view._focus_typed_artist()

    view.graph_view.focus_artist_by_name.assert_called_once_with("Miles Davis")


def test_refresh_disabled_while_graph_busy(qapp, monkeypatch):
    from PySide6.QtCore import Signal

    class StubGraph(QWidget):
        graph_updated = Signal()
        busy_changed = Signal(bool)

        def __init__(self, controller):
            super().__init__()
            self.node_names = {}

    monkeypatch.setattr("src.influences.influences_view.InfluenceGraphView", StubGraph)
    monkeypatch.setattr(InfluencesView, "show_global_view", lambda self: None)
    view = InfluencesView(controller=None)

    view.graph_view.busy_changed.emit(True)
    assert not view.refresh_button.isEnabled()
    view.graph_view.busy_changed.emit(False)
    assert view.refresh_button.isEnabled()
    assert view.fit_view_button.accessibleName() == "Fit to View"
    assert "Double-click" not in view.legend_button.toolTip()
