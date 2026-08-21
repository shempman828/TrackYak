"""Regression tests for the "queue toggle squishes the nav bar" bug.

Two independent mechanisms combined to produce the visible squish:

1. QMainWindow's dock layout reclaims the queue dock's width from whichever
   sibling dock has the lowest width floor (the navigation dock) instead of
   from the central widget. GUI.set_queue_visible now re-pins the nav dock's
   width via resizeDocks() across the toggle to prevent that.
2. NavigationDock's auto-collapse eventFilter used to watch its own resize
   event, so the transient reflow from (1) could be misread as "the user
   shrank the window" and trigger a full collapse to 60px. It now watches
   the main window's resize event instead.

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
    window, nav_dock, queue_dock = _make_window_with_docks(qapp)
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


def test_navigation_eventfilter_ignores_own_resize_reacts_to_window_resize(qapp):
    window = QMainWindow()
    window._switch_view = lambda *args, **kwargs: None
    nav_dock = NavigationDock(window)
    nav_dock.nav_auto_collapse = True
    nav_dock.nav_collapsed = False

    # A resize event on the dock itself -- e.g. from a sibling dock's
    # visibility change squeezing it -- must not be mistaken for the window
    # shrinking.
    own_resize = QResizeEvent(QSize(100, 600), QSize(256, 600))
    nav_dock.eventFilter(nav_dock, own_resize)
    assert nav_dock.nav_collapsed is False

    # The main window actually shrinking below the breakpoint should still
    # trigger the intended auto-collapse behavior.
    window_shrink = QResizeEvent(QSize(1200, 800), QSize(1500, 800))
    nav_dock.eventFilter(window, window_shrink)
    assert nav_dock.nav_collapsed is True

    # And growing back past the breakpoint should re-expand it.
    window_grow = QResizeEvent(QSize(1500, 800), QSize(1200, 800))
    nav_dock.eventFilter(window, window_grow)
    assert nav_dock.nav_collapsed is False

    window.close()
