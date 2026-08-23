"""Regression test for the UI-scale-slider freeze: dragging the Appearance
scale slider used to call DisplaySettings.set_ui_scale() on every debounce
settle, which restyles via QApplication.setStyleSheet() -- an O(total live
widget count) call that re-polishes every widget in the app, including
whichever QStackedWidget page isn't currently visible (e.g. an album grid
with thousands of AlbumWidgets accumulated from lazy-load). That made the
app freeze for as long as it took to re-polish widgets nobody could see.

ConfigDialog._visible_restyle_roots() computes the set of widgets a live
scale preview should actually touch: the dialog itself, plus whatever the
user can see behind it -- not the whole app.
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDockWidget, QLabel, QMainWindow, QStackedWidget, QWidget

from src.core.config_dialog import ConfigDialog


def test_visible_restyle_roots_includes_visible_page_excludes_hidden_ones(qapp):
    main_window = QMainWindow()
    stacked = QStackedWidget()
    main_window.setCentralWidget(stacked)
    main_window.stacked_widget = stacked

    hidden_page = QLabel("Albums (huge grid, not on screen right now)")
    visible_page = QLabel("Tracks (what the user is actually looking at)")
    stacked.addWidget(hidden_page)
    stacked.addWidget(visible_page)
    stacked.setCurrentWidget(visible_page)

    dock = QDockWidget("Player", main_window)
    main_window.addDockWidget(Qt.BottomDockWidgetArea, dock)

    # _visible_restyle_roots() only reads self.parent() and a few duck-typed
    # attributes off it, so a plain parented QWidget standing in for the
    # dialog exercises the real method without needing a fully-constructed
    # ConfigDialog (which requires a live Config/audio-device setup).
    dialog_stand_in = QWidget(main_window)

    roots = ConfigDialog._visible_restyle_roots(dialog_stand_in)

    assert dialog_stand_in in roots
    assert visible_page in roots
    assert hidden_page not in roots
    assert main_window.menuBar() in roots
    assert main_window.statusBar() in roots
    assert dock in roots


def test_visible_restyle_roots_falls_back_to_just_self_without_a_main_window(qapp):
    orphan = QWidget()
    assert ConfigDialog._visible_restyle_roots(orphan) == [orphan]
