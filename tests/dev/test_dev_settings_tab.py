"""Developer-mode flag persistence + the injected General Settings tab.

Maps to acceptance criteria 1 and 10 in
docs/specs/developer_mode_primary_artist_count_sort.md.
"""

import configparser

from PySide6.QtGui import QFontDatabase

from src.core.config_dialog import ConfigDialog
import src.core.font_family_worker as font_family_worker_module
import src.dev as dev_pkg
from src.dev import dev_mode


class _FakeConfig:
    """Just the ``.config`` configparser handle that dev_mode touches."""

    def __init__(self):
        self.config = configparser.ConfigParser()


# --------------------------------------------------------------------------- #
# AC1 — flag default + round-trip
# --------------------------------------------------------------------------- #
def test_flag_defaults_off_and_round_trips_in_memory():
    cfg = _FakeConfig()
    assert dev_mode.is_enabled(cfg) is False

    dev_mode.set_enabled(cfg, True)
    assert dev_mode.is_enabled(cfg) is True

    dev_mode.set_enabled(cfg, False)
    assert dev_mode.is_enabled(cfg) is False


def test_flag_survives_save_and_reload(tmp_path):
    path = tmp_path / "config.ini"

    cfg = _FakeConfig()
    dev_mode.set_enabled(cfg, True)
    with path.open("w") as fh:
        cfg.config.write(fh)

    reloaded = _FakeConfig()
    reloaded.config.read(path)
    assert dev_mode.is_enabled(reloaded) is True


# --------------------------------------------------------------------------- #
# AC10 — the injected "Developer" tab loads from and applies to config
# --------------------------------------------------------------------------- #
def test_config_dialog_developer_tab_load_and_apply(qapp, monkeypatch, dev_config, dev_patches):
    # Keep ConfigDialog.__init__ off the fc-list subprocess (same shim the
    # existing config-dialog tests use).
    monkeypatch.setattr(QFontDatabase, "families", staticmethod(lambda *a, **k: ["Test Sans"]))
    monkeypatch.setattr(
        font_family_worker_module.subprocess,
        "run",
        lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError("no fc-list")),
    )
    ConfigDialog._canonical_font_families_cache = {"Test Sans"}
    # _apply_settings() flushes via config.save() — no-op it so the real
    # config.ini is never written.
    monkeypatch.setattr(dev_config, "save", lambda: None)

    dev_mode.set_enabled(dev_config, False)
    dev_pkg.install()

    dialog = ConfigDialog(dev_config)
    try:
        tab_labels = [dialog.tabs.tabText(i) for i in range(dialog.tabs.count())]
        assert "Developer" in tab_labels

        # load() reflected the persisted (off) value
        assert dialog.dev_tab.enable_check.isChecked() is False

        # toggle + apply writes it back through dev_mode
        dialog.dev_tab.enable_check.setChecked(True)
        dialog._apply_settings()
        assert dev_mode.is_enabled(dev_config) is True

        # a fresh dialog now loads the new value
        dialog2 = ConfigDialog(dev_config)
        try:
            assert dialog2.dev_tab.enable_check.isChecked() is True
        finally:
            dialog2.reject()
            qapp.processEvents()
    finally:
        dialog.reject()
        qapp.processEvents()
        ConfigDialog._canonical_font_families_cache = None
