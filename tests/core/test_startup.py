"""Application startup: splash timing, the first-run startup dialog, and the
WebEngine pre-warm probe.
"""

import inspect

from PySide6.QtCore import QAbstractAnimation
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QMainWindow
import pytest

import run
from src.core.splash_screen import StartupSplash
from src.core.startup_dialog import StartupDialog
from src.foundation import config_setup as config_setup_module

# ---------------------------------------------------------------------------
# splash screen
# ---------------------------------------------------------------------------


@pytest.fixture
def splash(qapp):
    widget = StartupSplash(min_duration_ms=10_000)
    yield widget
    widget.close()


def test_finish_before_min_duration_defers_exit(splash):
    splash.finish()

    assert splash.finish_requested is True
    assert splash.exit_animation.state() != QAbstractAnimation.State.Running


def test_min_duration_elapsed_starts_deferred_exit(splash):
    splash.finish()
    splash._on_min_duration_elapsed()

    assert splash._min_duration_elapsed is True
    assert splash.exit_animation.state() == QAbstractAnimation.State.Running


def test_min_duration_elapsed_without_finish_does_not_exit(splash):
    splash._on_min_duration_elapsed()

    assert splash._min_duration_elapsed is True
    assert splash.exit_animation.state() != QAbstractAnimation.State.Running


def test_finish_after_min_duration_starts_exit_immediately(splash):
    splash._on_min_duration_elapsed()
    splash.finish()

    assert splash.exit_animation.state() == QAbstractAnimation.State.Running


def test_update_status_stores_message_and_clamps_progress(splash):
    splash.update_status("Loading...", progress=42)
    assert (splash._message, splash._progress) == ("Loading...", 42)

    splash.update_status("Loading", 150)
    assert splash._progress == 100
    splash.update_status("Loading", -5)
    assert splash._progress == 0
    splash.update_status("Loading", None)
    assert splash._progress is None


# ---------------------------------------------------------------------------
# First-run StartupDialog: directory validation, error display and theme keys.
# ---------------------------------------------------------------------------


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


def test_finish_without_directory_shows_error_and_creates_nothing(qapp, monkeypatch, fresh_config, tmp_path):
    monkeypatch.chdir(tmp_path)
    dialog = StartupDialog(fresh_config)
    dialog._selected_dir = None
    dialog.dir_display.setText("No directory selected")
    errors = []
    monkeypatch.setattr(dialog, "_show_error", errors.append)

    dialog._finish_setup()

    assert errors
    assert not (tmp_path / "No directory selected").exists()
    assert dialog.result() == 0


def test_mkdir_failure_uses_show_error(qapp, monkeypatch, fresh_config, tmp_path):
    from src.core import startup_dialog as module

    dialog = StartupDialog(fresh_config)
    dialog._selected_dir = tmp_path / "new_lib"
    shown = []
    monkeypatch.setattr(module.QMessageBox, "critical", lambda parent, title, msg: shown.append(msg))
    monkeypatch.setattr(module.Path, "mkdir", lambda self, **k: (_ for _ in ()).throw(OSError("read-only")))

    dialog._finish_setup()

    assert shown and "read-only" in shown[0]


def test_finish_saves_directory_and_display_theme(qapp, monkeypatch, fresh_config, tmp_path):
    monkeypatch.setattr(fresh_config, "save", lambda: None)
    dialog = StartupDialog(fresh_config)
    dialog._selected_dir = tmp_path
    dialog.theme_combo.clear()
    dialog.theme_combo.addItem("dark_mode")

    dialog._finish_setup()

    assert fresh_config.get_base_directory() == tmp_path
    assert not fresh_config.config.has_option("app", "theme_file")
    assert fresh_config.get_display_theme() == "dark_mode"
    assert fresh_config.is_first_run() is False


def test_theme_combo_shows_stems(qapp, fresh_config):
    dialog = StartupDialog(fresh_config)
    texts = [dialog.theme_combo.itemText(i) for i in range(dialog.theme_combo.count())]
    assert all(not t.endswith(".qss") for t in texts)


# ---------------------------------------------------------------------------
# Regression test for the "opening Places/Influences minimizes and resizes
# ---------------------------------------------------------------------------


def test_prewarm_webengine_parents_probe_into_window(qapp):
    window = QMainWindow()

    run._prewarm_webengine(window)

    probes = window.findChildren(QWebEngineView)
    assert len(probes) == 1
    assert probes[0].parent() is window

    window.close()
