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
