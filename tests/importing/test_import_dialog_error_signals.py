"""Regression test: ImportDialog must connect ImportWorker.error_occurred
and resource_warning so per-file failures and memory warnings during an
import reach the user (previously they were only logged, never surfaced
by the dialog).
"""

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import QListWidgetItem
import pytest

from src.importing import import_dialog as import_dialog_module
from src.importing.import_dialog import ImportDialog


class _FakeController:
    pass


class _FakeImportWorker(QObject):
    """Stand-in for ImportWorker: exposes the same signals without spawning
    a real QThread, so tests can emit them deterministically."""

    progress = Signal(int, int)
    finished = Signal(int)
    error_occurred = Signal(str)
    resource_warning = Signal(str, float)
    art_conflicts = Signal(list)

    def __init__(self, controller, paths):
        super().__init__()
        self.controller = controller
        self.paths = paths

    def start(self):
        pass

    def isRunning(self):
        return False

    def request_cancel(self):
        pass

    def wait(self):
        pass


@pytest.fixture
def dialog(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(import_dialog_module, "CONFIG_FILE", str(tmp_path / "import_paths.json"))
    monkeypatch.setattr(import_dialog_module, "ImportWorker", _FakeImportWorker)
    dlg = ImportDialog(_FakeController())
    yield dlg
    dlg.deleteLater()


def test_start_import_connects_error_and_resource_signals(dialog, tmp_path):
    item = QListWidgetItem(str(tmp_path))
    item.setCheckState(Qt.Checked)
    dialog.dir_list.addItem(item)

    errors_seen = []
    warnings_seen = []
    dialog._handle_import_error = errors_seen.append
    dialog._handle_resource_warning = lambda t, v: warnings_seen.append((t, v))

    dialog._start_import()
    dialog.import_worker.error_occurred.emit("boom")
    dialog.import_worker.resource_warning.emit("memory", 999.0)

    assert errors_seen == ["boom"]
    assert warnings_seen == [("memory", 999.0)]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
