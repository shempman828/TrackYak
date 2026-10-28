"""Navigation dock: persisted order/visibility, the Customize Navigation
dialog, keyboard and toggle behavior, and width pinning when the queue
dock toggles.
"""

from PySide6.QtCore import QPoint, QSize, Qt
from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import QDockWidget, QMainWindow, QMenu, QStackedWidget, QTreeWidget, QWidget

from src.core import navigation_dock as navigation_dock_module
from src.core.main_window import GUI
from src.core.navigation_customization import PINNED_VIEW, NavigationCustomizationDialog
from src.core.navigation_dock import NavigationDock
from src.foundation.config_setup import app_config

# ---------------------------------------------------------------------------
# Unit tests for GUI's persisted navbar order/visibility handling.
# ---------------------------------------------------------------------------


class _NavHost:
    # apply_navigation_state calls self._populate_navigation() internally;
    # bind the real implementation so the stand-in behaves the same way a
    # real GUI instance would.
    _populate_navigation = GUI._populate_navigation

    def __init__(self, view_names):
        self._view_factories = dict.fromkeys(view_names, lambda: None)
        self.nav_tree = QTreeWidget()
        self._view_cache = {}
        self.stacked_widget = QStackedWidget()
        self.view_registry = {}
        for name in view_names:
            idx = self.stacked_widget.addWidget(QWidget())
            self.view_registry[name] = idx

    def _ensure_view_built(self, view_name):
        self._view_cache[view_name] = True


def _labels(host):
    return [host.nav_tree.topLevelItem(i).text(0) for i in range(host.nav_tree.topLevelItemCount())]


def test_populate_navigation_respects_persisted_order_and_hidden(qapp, monkeypatch):
    host = _NavHost(["Tracks", "Albums", "Artists", "Genres"])
    monkeypatch.setattr(app_config, "get_nav_item_order", lambda: ["Genres", "Tracks", "Artists", "Albums"])
    monkeypatch.setattr(app_config, "get_nav_hidden_items", lambda: ["Artists"])

    GUI._load_navigation_state(host)
    GUI._populate_navigation(host)

    assert _labels(host) == ["Genres", "Tracks", "Albums"]


def test_populate_navigation_appends_unknown_views_visible_by_default(qapp, monkeypatch):
    host = _NavHost(["Tracks", "Albums", "Artists"])
    monkeypatch.setattr(app_config, "get_nav_item_order", lambda: ["Tracks", "Albums"])
    monkeypatch.setattr(app_config, "get_nav_hidden_items", lambda: [])

    GUI._load_navigation_state(host)
    GUI._populate_navigation(host)

    assert _labels(host) == ["Tracks", "Albums", "Artists"]


def test_populate_navigation_never_hides_tracks(qapp, monkeypatch):
    host = _NavHost(["Tracks", "Albums"])
    monkeypatch.setattr(app_config, "get_nav_item_order", lambda: ["Tracks", "Albums"])
    monkeypatch.setattr(app_config, "get_nav_hidden_items", lambda: ["Tracks"])

    GUI._load_navigation_state(host)
    GUI._populate_navigation(host)

    assert "Tracks" in _labels(host)


def test_apply_navigation_state_switches_away_from_now_hidden_view(qapp, monkeypatch):
    host = _NavHost(["Tracks", "Albums", "Artists"])
    saved = {}
    monkeypatch.setattr(app_config, "get_nav_item_order", lambda: [])
    monkeypatch.setattr(app_config, "get_nav_hidden_items", lambda: [])
    monkeypatch.setattr(app_config, "set_nav_item_order", lambda v: saved.__setitem__("order", v))
    monkeypatch.setattr(app_config, "set_nav_hidden_items", lambda v: saved.__setitem__("hidden", v))
    GUI._load_navigation_state(host)
    GUI._populate_navigation(host)
    host.stacked_widget.setCurrentIndex(host.view_registry["Albums"])

    GUI.apply_navigation_state(host, ["Tracks", "Artists", "Albums"], ["Albums"])

    assert saved["order"] == ["Tracks", "Artists", "Albums"]
    assert saved["hidden"] == ["Albums"]
    assert host.stacked_widget.currentIndex() == host.view_registry["Tracks"]


def test_apply_navigation_state_keeps_current_view_when_still_visible(qapp, monkeypatch):
    host = _NavHost(["Tracks", "Albums", "Artists"])
    monkeypatch.setattr(app_config, "get_nav_item_order", lambda: [])
    monkeypatch.setattr(app_config, "get_nav_hidden_items", lambda: [])
    monkeypatch.setattr(app_config, "set_nav_item_order", lambda v: None)
    monkeypatch.setattr(app_config, "set_nav_hidden_items", lambda v: None)
    GUI._load_navigation_state(host)
    GUI._populate_navigation(host)
    host.stacked_widget.setCurrentIndex(host.view_registry["Albums"])

    GUI.apply_navigation_state(host, ["Tracks", "Albums", "Artists"], ["Artists"])

    assert host.stacked_widget.currentIndex() == host.view_registry["Albums"]


def test_apply_navigation_state_drops_unknown_view_names(qapp, monkeypatch):
    host = _NavHost(["Tracks", "Albums"])
    saved = {}
    monkeypatch.setattr(app_config, "get_nav_item_order", lambda: [])
    monkeypatch.setattr(app_config, "get_nav_hidden_items", lambda: [])
    monkeypatch.setattr(app_config, "set_nav_item_order", lambda v: saved.__setitem__("order", v))
    monkeypatch.setattr(app_config, "set_nav_hidden_items", lambda v: saved.__setitem__("hidden", v))
    GUI._load_navigation_state(host)
    GUI._populate_navigation(host)
    host.stacked_widget.setCurrentIndex(host.view_registry["Albums"])

    GUI.apply_navigation_state(host, ["Ghost", "Albums", "Tracks"], ["Albums", "Ghost"])

    assert saved["order"] == ["Albums", "Tracks"]
    assert saved["hidden"] == ["Albums"]
    assert host.stacked_widget.currentIndex() == host.view_registry["Tracks"]


# ---------------------------------------------------------------------------
# Tests for NavigationCustomizationDialog and its right-click entry point on
# ---------------------------------------------------------------------------


class _NavHostCustomization:
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
    host = _NavHostCustomization(["Tracks", "Albums", "Artists"], hidden=["Artists"])
    dialog = NavigationCustomizationDialog(host)

    assert _row(dialog, "Albums").checkState() == Qt.Checked
    assert _row(dialog, "Artists").checkState() == Qt.Unchecked


def test_dialog_pinned_view_row_is_uncheckable_and_checked(qapp):
    host = _NavHostCustomization(["Tracks", "Albums"])
    dialog = NavigationCustomizationDialog(host)

    tracks_item = _row(dialog, PINNED_VIEW)
    assert tracks_item.checkState() == Qt.Checked
    assert not (tracks_item.flags() & Qt.ItemIsUserCheckable)


def test_get_selected_state_reflects_order_and_unchecked_rows(qapp):
    host = _NavHostCustomization(["Tracks", "Albums", "Artists"])
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
    host = _NavHostCustomization(["Tracks", "Albums"])
    dialog = NavigationCustomizationDialog(host)

    # Even if something forced the pinned row's check state off directly,
    # get_selected_state is the authoritative guard against hiding it.
    _row(dialog, PINNED_VIEW).setCheckState(Qt.Unchecked)

    state = dialog.get_selected_state()
    assert PINNED_VIEW not in state["hidden"]


def test_accept_changes_applies_state_to_main_window(qapp):
    host = _NavHostCustomization(["Tracks", "Albums", "Artists"])
    dialog = NavigationCustomizationDialog(host)
    _row(dialog, "Artists").setCheckState(Qt.Unchecked)

    dialog.accept_changes()

    assert host.applied == {"order": ["Tracks", "Albums", "Artists"], "hidden": ["Artists"]}


def test_hide_all_and_show_all_toggle_every_row_except_pinned(qapp):
    host = _NavHostCustomization(["Tracks", "Albums", "Artists"])
    dialog = NavigationCustomizationDialog(host)
    assert dialog.hide_all_btn.text() == "Hide All"
    assert dialog.show_all_btn.text() == "Show All"

    dialog.hide_all_btn.click()
    assert dialog.get_selected_state()["hidden"] == ["Albums", "Artists"]
    assert _row(dialog, PINNED_VIEW).checkState() == Qt.Checked

    dialog.show_all_btn.click()
    assert dialog.get_selected_state()["hidden"] == []


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


# ---------------------------------------------------------------------------
# Regression test: NavigationDock must expose a non-empty windowTitle.
# ---------------------------------------------------------------------------


def test_enter_key_on_nav_tree_switches_to_current_item(qapp):
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtWidgets import QTreeWidgetItem

    window = QMainWindow()
    switched = []
    window._switch_view = switched.append
    nav_dock = NavigationDock(window)
    item = QTreeWidgetItem(nav_dock.nav_tree, ["Albums"])
    nav_dock.nav_tree.setCurrentItem(item)

    handled = nav_dock.eventFilter(nav_dock.nav_tree, QKeyEvent(QEvent.KeyPress, Qt.Key_Return, Qt.NoModifier))

    assert handled is True
    assert switched == [item]
    assert nav_dock.nav_tree.focusPolicy() == Qt.TabFocus
    window.close()


def test_second_toggle_stops_the_running_animation(qapp):
    window = QMainWindow()
    window._switch_view = lambda *a: None
    nav_dock = NavigationDock(window)

    nav_dock.collapse_navigation()
    first = nav_dock._nav_animation
    nav_dock.expand_navigation()

    assert first is not nav_dock._nav_animation
    assert first.state() == first.State.Stopped
    window.close()


# ---------------------------------------------------------------------------
# Regression tests for the "queue toggle squishes the nav bar" bug.
# ---------------------------------------------------------------------------


def _make_window_with_docks(qapp):
    window = QMainWindow()
    window._switch_view = lambda *args, **kwargs: None

    nav_dock = NavigationDock(window)

    queue_dock = QDockWidget("Queue", window)
    queue_dock.setWidget(QWidget())
    queue_dock.setMinimumWidth(300)
    queue_dock.setMaximumWidth(500)
    from PySide6.QtCore import Qt

    window.addDockWidget(Qt.RightDockWidgetArea, queue_dock)
    queue_dock.hide()

    window.navigation_dock = nav_dock
    window.queue_dock = queue_dock
    return window, nav_dock, queue_dock


def test_set_queue_visible_repins_navigation_dock_width(qapp, monkeypatch):
    window, nav_dock, _queue_dock = _make_window_with_docks(qapp)
    window.resize(1280, 800)
    window.show()
    qapp.processEvents()

    nav_width_before = nav_dock.width()
    calls = []
    original_resize_docks = window.resizeDocks

    def spy_resize_docks(docks, sizes, orientation):
        calls.append((list(docks), list(sizes)))
        return original_resize_docks(docks, sizes, orientation)

    monkeypatch.setattr(window, "resizeDocks", spy_resize_docks)

    GUI.set_queue_visible(window, True)

    assert calls, "set_queue_visible must re-pin the nav dock's width via resizeDocks()"
    docks, sizes = calls[0]
    assert docks == [nav_dock]
    assert sizes == [nav_width_before]

    window.close()


def test_window_resize_never_collapses_navigation(qapp):
    window = QMainWindow()
    window._switch_view = lambda *args, **kwargs: None
    nav_dock = NavigationDock(window)

    nav_dock.eventFilter(window, QResizeEvent(QSize(800, 600), QSize(1500, 800)))
    nav_dock.eventFilter(nav_dock, QResizeEvent(QSize(100, 600), QSize(256, 600)))

    assert nav_dock.nav_collapsed is False
    window.close()
