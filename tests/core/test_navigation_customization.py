"""Tests for NavigationCustomizationDialog and its right-click entry point on
the nav tree. Covers docs/specs/navbar_customization.md AC6, AC7, AC8, AC10.
"""

from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QMainWindow, QMenu, QStackedWidget, QWidget

from src.core import navigation_dock as navigation_dock_module
from src.core.navigation_customization import PINNED_VIEW, NavigationCustomizationDialog
from src.core.navigation_dock import NavigationDock


class _NavHost:
    """Minimal main_window stand-in exposing what the dialog / apply_navigation_state need."""

    def __init__(self, view_names, order=None, hidden=None):
        self._view_factories = dict.fromkeys(view_names, lambda: None)
        self._nav_order = list(order) if order is not None else list(view_names)
        self._nav_hidden = set(hidden) if hidden is not None else set()
        self.stacked_widget = QStackedWidget()
        self.view_registry = {}
        for name in view_names:
            idx = self.stacked_widget.addWidget(QWidget())
            self.view_registry[name] = idx
        self.applied = None

    def apply_navigation_state(self, order, hidden):
        self.applied = {"order": list(order), "hidden": list(hidden)}
        self._nav_order = list(order)
        self._nav_hidden = set(hidden)


def _row(dialog, view_name):
    for i in range(dialog.item_list.count()):
        item = dialog.item_list.item(i)
        if item.data(Qt.UserRole) == view_name:
            return item
    raise AssertionError(f"{view_name!r} not found in dialog list")


def test_dialog_loads_check_state_from_current_nav_state(qapp):
    host = _NavHost(["Tracks", "Albums", "Artists"], hidden=["Artists"])
    dialog = NavigationCustomizationDialog(host)

    assert _row(dialog, "Albums").checkState() == Qt.Checked
    assert _row(dialog, "Artists").checkState() == Qt.Unchecked


def test_dialog_pinned_view_row_is_uncheckable_and_checked(qapp):
    host = _NavHost(["Tracks", "Albums"])
    dialog = NavigationCustomizationDialog(host)

    tracks_item = _row(dialog, PINNED_VIEW)
    assert tracks_item.checkState() == Qt.Checked
    assert not (tracks_item.flags() & Qt.ItemIsUserCheckable)


def test_get_selected_state_reflects_order_and_unchecked_rows(qapp):
    host = _NavHost(["Tracks", "Albums", "Artists"])
    dialog = NavigationCustomizationDialog(host)

    _row(dialog, "Albums").setCheckState(Qt.Unchecked)
    # Simulate a drag reorder by moving the "Artists" row to the top.
    artists_row = dialog.item_list.row(_row(dialog, "Artists"))
    item = dialog.item_list.takeItem(artists_row)
    dialog.item_list.insertItem(0, item)

    state = dialog.get_selected_state()
    assert state["order"][0] == "Artists"
    assert state["hidden"] == ["Albums"]


def test_get_selected_state_never_hides_pinned_view(qapp):
    host = _NavHost(["Tracks", "Albums"])
    dialog = NavigationCustomizationDialog(host)

    # Even if something forced the pinned row's check state off directly,
    # get_selected_state is the authoritative guard against hiding it.
    _row(dialog, PINNED_VIEW).setCheckState(Qt.Unchecked)

    state = dialog.get_selected_state()
    assert PINNED_VIEW not in state["hidden"]


def test_accept_changes_applies_state_to_main_window(qapp):
    host = _NavHost(["Tracks", "Albums", "Artists"])
    dialog = NavigationCustomizationDialog(host)
    _row(dialog, "Artists").setCheckState(Qt.Unchecked)

    dialog.accept_changes()

    assert host.applied == {"order": ["Tracks", "Albums", "Artists"], "hidden": ["Artists"]}


def test_nav_tree_context_menu_offers_customize_navigation(qapp, monkeypatch):
    captured_menus = []
    monkeypatch.setattr(QMenu, "exec_", lambda self, *a, **k: captured_menus.append(self))

    window = QMainWindow()
    window._switch_view = lambda *args, **kwargs: None
    nav_dock = NavigationDock(window)

    nav_dock._show_nav_context_menu(QPoint(0, 0))

    assert len(captured_menus) == 1
    labels = [action.text() for action in captured_menus[0].actions()]
    assert "Customize Navigation…" in labels

    window.close()


def test_nav_tree_context_menu_action_opens_customization_dialog(qapp, monkeypatch):
    opened_for = []

    class _StubDialog:
        def __init__(self, main_window, parent=None):
            opened_for.append(main_window)

        def exec_(self):
            pass

    monkeypatch.setattr(navigation_dock_module, "NavigationCustomizationDialog", _StubDialog)

    window = QMainWindow()
    window._switch_view = lambda *args, **kwargs: None
    nav_dock = NavigationDock(window)

    nav_dock._show_navigation_customization_dialog()

    assert opened_for == [window]
    window.close()
