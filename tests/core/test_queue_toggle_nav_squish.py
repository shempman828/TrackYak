"""Regression tests for the "queue toggle squishes the nav bar" bug.

QMainWindow's dock layout reclaims the queue dock's width from whichever
sibling dock has the lowest width floor (the navigation dock) instead of
from the central widget. GUI.set_queue_visible re-pins the nav dock's
width via resizeDocks() across the toggle to prevent that. The nav dock
has no resize-driven auto-collapse, so a reflow can never collapse it.

Full end-to-end pixel-geometry assertions are not used here: the offscreen
QPA platform (used for headless test runs) does not fully replicate a real
window manager's dock/splitter layout math ("This plugin does not support
propagateSizeHints()"), so exact widths after a real reflow aren't reliable
in this environment. Instead each mechanism is verified directly.
"""

from PySide6.QtCore import QSize
from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import QDockWidget, QMainWindow, QWidget

from src.core.main_window import GUI
from src.core.navigation_dock import NavigationDock


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
