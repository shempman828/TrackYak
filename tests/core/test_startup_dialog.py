"""First-run StartupDialog: directory validation, error display and theme keys."""

import pytest

from src.core.startup_dialog import StartupDialog
from src.foundation import config_setup as config_setup_module


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


def test_finish_saves_directory_and_both_theme_keys(qapp, monkeypatch, fresh_config, tmp_path):
    monkeypatch.setattr(fresh_config, "save", lambda: None)
    dialog = StartupDialog(fresh_config)
    dialog._selected_dir = tmp_path
    dialog.theme_combo.clear()
    dialog.theme_combo.addItem("dark_mode")

    dialog._finish_setup()

    assert fresh_config.get_base_directory() == tmp_path
    assert fresh_config.get_theme_file() == "dark_mode.qss"
    assert fresh_config.get_display_theme() == "dark_mode"
    assert fresh_config.is_first_run() is False


def test_theme_combo_shows_stems(qapp, fresh_config):
    dialog = StartupDialog(fresh_config)
    texts = [dialog.theme_combo.itemText(i) for i in range(dialog.theme_combo.count())]
    assert all(not t.endswith(".qss") for t in texts)
