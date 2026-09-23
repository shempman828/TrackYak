"""Injects the "Developer" tab (holding the developer-mode toggle) into the
General Settings dialog.

``patch()`` wraps three ``ConfigDialog`` methods:

* ``_setup_ui``            — append the Developer tab after the dialog builds,
* ``_load_current_settings`` — populate the checkbox from the persisted flag,
* ``_apply_settings``      — write the checkbox value back (before the dialog's
  own ``config.save()`` runs, so it rides the existing flush to disk).

Nothing in ``src/core`` references this module.
"""

from __future__ import annotations

import functools

from PySide6.QtWidgets import QCheckBox, QLabel, QVBoxLayout, QWidget

from src.core.config_dialog import ConfigDialog
from src.dev import dev_immediate_write, dev_mode


class DeveloperSettingsTab(QWidget):
    """Settings tab for the developer-mode flag and its dependent options."""

    def __init__(self, config, parent=None):
        super().__init__(parent)
        self._config = config

        layout = QVBoxLayout(self)
        self.enable_check = QCheckBox("Enable developer mode")
        layout.addWidget(self.enable_check)

        caption = QLabel("Unlocks experimental and diagnostic options. Some changes take effect after restarting the app.")
        caption.setWordWrap(True)
        caption.setEnabled(False)
        layout.addWidget(caption)

        self.immediate_write_check = QCheckBox("Write file metadata immediately on change")
        layout.addWidget(self.immediate_write_check)

        immediate_write_caption = QLabel("Writes changed tags straight to the file, on every save. May briefly pause the UI on larger edits.")
        immediate_write_caption.setWordWrap(True)
        immediate_write_caption.setEnabled(False)
        layout.addWidget(immediate_write_caption)

        layout.addStretch()

        self.enable_check.toggled.connect(self._on_master_toggled)

    def _on_master_toggled(self, checked: bool) -> None:
        self.immediate_write_check.setEnabled(checked)
        if not checked:
            self.immediate_write_check.setChecked(False)

    def load(self) -> None:
        enabled = dev_mode.is_enabled(self._config)
        self.enable_check.setChecked(enabled)
        self.immediate_write_check.setEnabled(enabled)
        self.immediate_write_check.setChecked(enabled and dev_immediate_write.is_enabled(self._config))

    def apply(self) -> None:
        dev_mode.set_enabled(self._config, self.enable_check.isChecked())
        dev_immediate_write.set_enabled(self._config, self.immediate_write_check.isChecked())


_orig_setup_ui = None
_orig_load = None
_orig_apply = None


def patch() -> None:
    global _orig_setup_ui, _orig_load, _orig_apply

    if _orig_setup_ui is None:
        _orig_setup_ui = ConfigDialog._setup_ui
        original = _orig_setup_ui

        @functools.wraps(original)
        def _setup_ui(self):
            original(self)
            if getattr(self, "dev_tab", None) is None:
                self.dev_tab = DeveloperSettingsTab(self.config)
                self.tabs.addTab(self.dev_tab, "Developer")

        ConfigDialog._setup_ui = _setup_ui

    if _orig_load is None:
        _orig_load = ConfigDialog._load_current_settings
        original_load = _orig_load

        @functools.wraps(original_load)
        def _load_current_settings(self):
            original_load(self)
            dev_tab = getattr(self, "dev_tab", None)
            if dev_tab is not None:
                dev_tab.load()

        ConfigDialog._load_current_settings = _load_current_settings

    if _orig_apply is None:
        _orig_apply = ConfigDialog._apply_settings
        original_apply = _orig_apply

        @functools.wraps(original_apply)
        def _apply_settings(self):
            dev_tab = getattr(self, "dev_tab", None)
            if dev_tab is not None:
                # Before the wrapped body so its own config.save() persists it.
                dev_tab.apply()
            original_apply(self)

        ConfigDialog._apply_settings = _apply_settings


def unpatch() -> None:
    """Restore ConfigDialog. Used by tests; harmless if never patched."""
    global _orig_setup_ui, _orig_load, _orig_apply

    if _orig_setup_ui is not None:
        ConfigDialog._setup_ui = _orig_setup_ui
        _orig_setup_ui = None
    if _orig_load is not None:
        ConfigDialog._load_current_settings = _orig_load
        _orig_load = None
    if _orig_apply is not None:
        ConfigDialog._apply_settings = _orig_apply
        _orig_apply = None
