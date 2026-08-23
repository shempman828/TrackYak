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
