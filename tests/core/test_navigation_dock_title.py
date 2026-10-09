"""Regression test: NavigationDock must expose a non-empty windowTitle.

The dock's visible title bar is intentionally suppressed via
setTitleBarWidget(QWidget()), but Qt's default right-click dock/toolbar
context menu (QMainWindow.createPopupMenu()) labels each dock's toggle
action using windowTitle(). An empty title produced a blank, unlabeled
entry in that menu.
"""

from PySide6.QtWidgets import QMainWindow

from src.core.navigation_dock import NavigationDock


def test_navigation_dock_has_nonempty_title_for_toggle_menu(qapp):
    window = QMainWindow()
    window._switch_view = lambda *args, **kwargs: None
    nav_dock = NavigationDock(window)

    assert nav_dock.windowTitle() != ""
    assert nav_dock.toggleViewAction().text() == nav_dock.windowTitle()

    window.close()


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
