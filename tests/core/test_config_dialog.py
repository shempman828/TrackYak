"""Regression test for the Settings-dialog open lag: computing the
alias-deduplicated font family list (fc-list subprocess + QFontDatabase
clustering, see FontFamilyWorker) used to run synchronously in
ConfigDialog.__init__ on every single open, blocking the UI thread.

It now runs once per process in FontFamilyWorker on a background QThread,
with ConfigDialog showing a placeholder until it lands and caching the
result at the class level so later opens skip the work entirely.
"""

import time

from PySide6.QtCore import Qt
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QDockWidget, QLabel, QMainWindow, QStackedWidget, QWidget
import pytest

from src.core.config_dialog import ConfigDialog
import src.core.font_family_worker as font_family_worker_module
from src.foundation import config_setup as config_setup_module
from src.foundation.config_setup import Config


# ---- test_config_dialog_font_worker_async.py ---------------------------------
def _pump_until(condition, app, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not condition() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.005)
    assert condition(), "background font computation never completed"


def test_first_open_backgrounds_font_computation_and_caches_it(qapp, monkeypatch):
    monkeypatch.setattr(QFontDatabase, "families", staticmethod(lambda *a, **k: ["Test Sans"]))
    monkeypatch.setattr(font_family_worker_module.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError("no fc-list")))
    ConfigDialog._canonical_font_families_cache = None

    dialog = ConfigDialog(Config())
    try:
        # Dialog must be usable immediately -- not blocked on the
        # background computation.
        assert dialog.font_combo.itemText(0) == "Loading fonts…"
        assert dialog.font_combo.isEnabled() is False
        assert ConfigDialog._canonical_font_families_cache is None

        _pump_until(lambda: ConfigDialog._canonical_font_families_cache is not None, qapp)

        assert dialog.font_combo.isEnabled() is True
        assert dialog.font_combo.count() == 1
        assert dialog.font_combo.itemText(0) == "Test Sans"
    finally:
        dialog.reject()
        qapp.processEvents()
        ConfigDialog._canonical_font_families_cache = None


def test_second_open_reuses_cache_without_a_placeholder(qapp, monkeypatch):
    monkeypatch.setattr(QFontDatabase, "families", staticmethod(lambda *a, **k: ["Test Sans"]))
    monkeypatch.setattr(font_family_worker_module.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError("no fc-list")))
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


def test_closing_dialog_before_worker_finishes_does_not_leave_thread_running(qapp, monkeypatch):
    def slow_families(*a, **k):
        time.sleep(0.2)
        return ["Test Sans"]

    monkeypatch.setattr(QFontDatabase, "families", staticmethod(slow_families))
    monkeypatch.setattr(font_family_worker_module.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError("no fc-list")))
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
    assert dock in roots


def test_visible_restyle_roots_never_creates_a_status_bar(qapp):
    from PySide6.QtWidgets import QStatusBar

    main_window = QMainWindow()
    main_window.setStatusBar(None)  # as GUI._init_status_system does
    main_window.status_bar_widget = QWidget(main_window)
    dialog_stand_in = QWidget(main_window)
    bars_before = len(main_window.findChildren(QStatusBar))

    roots = ConfigDialog._visible_restyle_roots(dialog_stand_in)

    assert len(main_window.findChildren(QStatusBar)) == bars_before
    assert main_window.status_bar_widget in roots


def test_visible_restyle_roots_falls_back_to_just_self_without_a_main_window(qapp):
    orphan = QWidget()
    assert ConfigDialog._visible_restyle_roots(orphan) == [orphan]


# ---- waveform display mode (linear vs. perceptual/log), see
# docs/specs/waveform_display_mode.md ------------------------------------------
# AC3  audio tab loads the checkbox from waveform_display_mode
# AC4  applying the checkbox writes "log"/"linear" back to config


@pytest.fixture
def fresh_config(tmp_path, monkeypatch):
    scratch_ini = tmp_path / "config.ini"
    monkeypatch.setattr(config_setup_module, "config", lambda name: str(scratch_ini))
    config_setup_module.Config._instance = None
    config_setup_module.Config._initialized = False
    cfg = config_setup_module.Config()
    yield cfg
    config_setup_module.Config._instance = None
    config_setup_module.Config._initialized = False


def _stub_font_worker(monkeypatch):
    monkeypatch.setattr(QFontDatabase, "families", staticmethod(lambda *a, **k: ["Test Sans"]))
    monkeypatch.setattr(font_family_worker_module.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError("no fc-list")))
    ConfigDialog._canonical_font_families_cache = {"Test Sans"}


def test_audio_tab_loads_log_checkbox_checked_when_config_is_log(qapp, monkeypatch, fresh_config):
    _stub_font_worker(monkeypatch)
    fresh_config.set_waveform_display_mode("log")

    dialog = ConfigDialog(fresh_config)
    try:
        assert dialog.waveform_log_scale_check.isChecked() is True
    finally:
        dialog.reject()
        qapp.processEvents()
        ConfigDialog._canonical_font_families_cache = None


def test_unchecking_and_applying_writes_linear_back_to_config(qapp, monkeypatch, fresh_config):
    _stub_font_worker(monkeypatch)
    fresh_config.set_waveform_display_mode("log")

    dialog = ConfigDialog(fresh_config)
    try:
        dialog.waveform_log_scale_check.setChecked(False)
        dialog._apply_settings()
        assert fresh_config.get_waveform_display_mode() == "linear"
    finally:
        dialog.reject()
        qapp.processEvents()
        ConfigDialog._canonical_font_families_cache = None


# ---- live Appearance preview: load must not apply, Cancel must revert ---------


class _FakeDisplaySettings:
    """Records setter calls; mirrors the DisplaySettings attributes ConfigDialog reads."""

    def __init__(self):
        self.calls = []
        self.theme_name = "dark_mode"
        self.ui_scale = 1.0
        self.font_family = "Test Sans"
        self.font_size = 10
        self._auto_hide = False
        self._blur = False
        self._censor = False

    def get_available_themes(self):
        return ["dark_mode", "light_mode"]

    def get_menu_bar_auto_hide(self):
        return self._auto_hide

    def get_blur_explicit_art(self):
        return self._blur

    def get_censor_explicit_words(self):
        return self._censor

    def set_theme(self, name):
        self.calls.append(("set_theme", name))
        self.theme_name = name

    def set_font_family(self, family):
        self.calls.append(("set_font_family", family))
        self.font_family = family

    def set_font_size(self, size):
        self.calls.append(("set_font_size", size))
        self.font_size = size

    def set_menu_bar_auto_hide(self, enabled):
        self.calls.append(("set_menu_bar_auto_hide", enabled))
        self._auto_hide = enabled

    def set_blur_explicit_art(self, enabled):
        self.calls.append(("set_blur_explicit_art", enabled))
        self._blur = enabled

    def set_censor_explicit_words(self, enabled):
        self.calls.append(("set_censor_explicit_words", enabled))
        self._censor = enabled

    def set_ui_scale(self, scale):
        self.calls.append(("set_ui_scale", scale))
        self.ui_scale = scale

    def preview_ui_scale_in(self, scale, roots):
        self.calls.append(("preview_ui_scale_in", scale))
        self.ui_scale = scale


def _close(dialog, qapp):
    dialog.done(0)
    qapp.processEvents()
    ConfigDialog._canonical_font_families_cache = None


def test_opening_dialog_does_not_live_apply_anything(qapp, monkeypatch, fresh_config):
    _stub_font_worker(monkeypatch)
    ds = _FakeDisplaySettings()
    ds.theme_name = "light_mode"  # not the first combo entry, so loading changes the combo index
    ds.ui_scale = 1.2

    dialog = ConfigDialog(fresh_config, ds)
    try:
        assert dialog.theme_combo.currentText() == "light_mode"
        assert dialog.scale_slider.value() == 120
        assert ds.calls == []
        assert dialog._pending_scale_value is None
    finally:
        ds.calls.clear()
        _close(dialog, qapp)
    assert ds.calls == [], "closing an untouched dialog must not commit or revert anything"


def test_cancel_reverts_live_appearance_changes(qapp, monkeypatch, fresh_config):
    _stub_font_worker(monkeypatch)
    ds = _FakeDisplaySettings()
    dialog = ConfigDialog(fresh_config, ds)
    try:
        dialog.theme_combo.setCurrentText("light_mode")
        dialog.font_size_spin.setValue(14)
        dialog.censor_words_check.setChecked(True)
        dialog.scale_slider.setValue(130)
        dialog._preview_pending_scale()

        dialog.reject()

        assert ds.theme_name == "dark_mode"
        assert ds.font_size == 10
        assert ds._censor is False
        assert ds.ui_scale == 1.0
        assert ("set_ui_scale", 1.3) not in ds.calls
        assert fresh_config.get_theme_file() != "light_mode.qss"
    finally:
        _close(dialog, qapp)


def test_ok_commits_previewed_scale(qapp, monkeypatch, fresh_config):
    _stub_font_worker(monkeypatch)
    monkeypatch.setattr("src.core.config_dialog.reconfigure_logging", lambda cfg: None)
    ds = _FakeDisplaySettings()
    dialog = ConfigDialog(fresh_config, ds)
    try:
        dialog.scale_slider.setValue(130)
        dialog._ok_clicked()
        assert ("set_ui_scale", 1.3) in ds.calls
    finally:
        _close(dialog, qapp)


def test_ok_keeps_dialog_open_when_apply_fails(qapp, monkeypatch, fresh_config):
    _stub_font_worker(monkeypatch)
    monkeypatch.setattr(ConfigDialog, "_show_error", lambda self, msg: None)

    def boom(*_a):
        raise ValueError("disk full")

    monkeypatch.setattr(fresh_config, "save", boom)
    dialog = ConfigDialog(fresh_config)
    accepted = []
    dialog.accepted.connect(lambda: accepted.append(True))
    try:
        dialog._ok_clicked()
        assert accepted == []
    finally:
        _close(dialog, qapp)


def test_exclusive_mode_none_on_player_does_not_abort_loading(qapp, monkeypatch, fresh_config):
    _stub_font_worker(monkeypatch)
    errors = []
    monkeypatch.setattr(ConfigDialog, "_show_error", lambda self, msg: errors.append(msg))
    fresh_config.set_backup_count(7)

    class _Player:
        exclusive_mode = None
        normalization_enabled = True
        normalization_target = -16.0

        def get_audio_devices(self):
            return []

    dialog = ConfigDialog(fresh_config, None, _Player())
    try:
        assert errors == []
        assert dialog.exclusive_mode_check.isChecked() is False
        assert dialog.normalization_spin.value() == -16.0
        assert dialog.backup_count_spin.value() == 7
    finally:
        _close(dialog, qapp)


def test_normalization_controls_disabled_without_player(qapp, monkeypatch, fresh_config):
    _stub_font_worker(monkeypatch)
    dialog = ConfigDialog(fresh_config)
    try:
        assert not dialog.normalization_check.isEnabled()
        assert not dialog.normalization_spin.isEnabled()
    finally:
        _close(dialog, qapp)


def test_unused_settings_controls_are_removed(qapp, monkeypatch, fresh_config):
    _stub_font_worker(monkeypatch)
    dialog = ConfigDialog(fresh_config)
    try:
        for name in ("scan_startup_check", "auto_refresh_check", "repeat_combo", "shuffle_check", "buffer_combo"):
            assert not hasattr(dialog, name), name
    finally:
        _close(dialog, qapp)


def test_font_list_arrival_never_live_applies_the_first_font(qapp, monkeypatch, fresh_config):
    monkeypatch.setattr(QFontDatabase, "families", staticmethod(lambda *a, **k: ["Aaa Font", "Test Sans"]))
    ConfigDialog._canonical_font_families_cache = None
    monkeypatch.setattr("src.core.config_dialog.FontFamilyWorker.start", lambda self: None)
    ds = _FakeDisplaySettings()
    dialog = ConfigDialog(fresh_config, ds)
    try:
        dialog._on_font_families_computed({"Aaa Font", "Test Sans"})
        assert dialog.font_combo.currentText() == "Test Sans"
        assert not any(call[0] == "set_font_family" for call in ds.calls)
    finally:
        _close(dialog, qapp)
