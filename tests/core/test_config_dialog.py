"""Regression test for the Settings-dialog open lag: computing the
alias-deduplicated font family list (fc-list subprocess + QFontDatabase
clustering, see FontFamilyWorker) used to run synchronously in
ConfigDialog.__init__ on every single open, blocking the UI thread.

It now runs once per process in FontFamilyWorker on a background QThread,
with ConfigDialog showing a placeholder until it lands and caching the
result at the class level so later opens skip the work entirely.
"""
import time
from PySide6.QtGui import QFontDatabase
from src.core.config_dialog import ConfigDialog
from src.core.config_setup import Config
import src.core.font_family_worker as font_family_worker_module
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDockWidget, QLabel, QMainWindow, QStackedWidget, QWidget

# ---- test_config_dialog_font_worker_async.py ---------------------------------
def _pump_until(condition, app, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not condition() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.005)
    assert condition(), "background font computation never completed"

def test_first_open_backgrounds_font_computation_and_caches_it(qapp, monkeypatch):
    monkeypatch.setattr(
        QFontDatabase, "families", staticmethod(lambda *a, **k: ["Test Sans"])
    )
    monkeypatch.setattr(
        font_family_worker_module.subprocess,
        "run",
        lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError("no fc-list")),
    )
    ConfigDialog._canonical_font_families_cache = None

    dialog = ConfigDialog(Config())
    try:
        # Dialog must be usable immediately -- not blocked on the
        # background computation.
        assert dialog.font_combo.itemText(0) == "Loading fonts…"
        assert dialog.font_combo.isEnabled() is False
        assert ConfigDialog._canonical_font_families_cache is None

        _pump_until(
            lambda: ConfigDialog._canonical_font_families_cache is not None, qapp
        )

        assert dialog.font_combo.isEnabled() is True
        assert dialog.font_combo.count() == 1
        assert dialog.font_combo.itemText(0) == "Test Sans"
    finally:
        dialog.reject()
        qapp.processEvents()
        ConfigDialog._canonical_font_families_cache = None

def test_second_open_reuses_cache_without_a_placeholder(qapp, monkeypatch):
    monkeypatch.setattr(
        QFontDatabase, "families", staticmethod(lambda *a, **k: ["Test Sans"])
    )
    monkeypatch.setattr(
        font_family_worker_module.subprocess,
        "run",
        lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError("no fc-list")),
    )
    ConfigDialog._canonical_font_families_cache = {"Test Sans"}

    dialog = ConfigDialog(Config())
    try:
        # Cache was already warm -- no worker spawned, no placeholder shown.
        assert not hasattr(dialog, "_font_family_worker")
        assert dialog.font_combo.isEnabled() is True
        assert dialog.font_combo.itemText(0) == "Test Sans"
    finally:
        dialog.reject()
        qapp.processEvents()
        ConfigDialog._canonical_font_families_cache = None

def test_closing_dialog_before_worker_finishes_does_not_leave_thread_running(
    qapp, monkeypatch
):
    def slow_families(*a, **k):
        time.sleep(0.2)
        return ["Test Sans"]

    monkeypatch.setattr(QFontDatabase, "families", staticmethod(slow_families))
    monkeypatch.setattr(
        font_family_worker_module.subprocess,
        "run",
        lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError("no fc-list")),
    )
    ConfigDialog._canonical_font_families_cache = None

    dialog = ConfigDialog(Config())
    worker = dialog._font_family_worker
    assert worker.isRunning()

    dialog.reject()
    qapp.processEvents()

    assert worker.isRunning() is False
    ConfigDialog._canonical_font_families_cache = None

# ---- test_config_dialog_scale_scope.py ---------------------------------------
# Regression test for the UI-scale-slider freeze: dragging the Appearance
# scale slider used to call DisplaySettings.set_ui_scale() on every debounce
# settle, which restyles via QApplication.setStyleSheet() -- an O(total live
# widget count) call that re-polishes every widget in the app, including
# whichever QStackedWidget page isn't currently visible (e.g. an album grid
# with thousands of AlbumWidgets accumulated from lazy-load). That made the
# app freeze for as long as it took to re-polish widgets nobody could see.
#
# ConfigDialog._visible_restyle_roots() computes the set of widgets a live
# scale preview should actually touch: the dialog itself, plus whatever the
# user can see behind it -- not the whole app.
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
