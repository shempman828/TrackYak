"""Tests for ImportDialog UI state during an import:
- Add/Remove buttons disable while an import is running (previously
  stayed enabled, letting the tracked-directory list be edited in ways
  that didn't reflect the in-flight import).
- The empty-state placeholder shows/hides with the directory list.
- The progress bar (added to replace the dead progress_updated signal)
  reflects ImportWorker.progress.
"""

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import QListWidgetItem
import pytest

from src.importing import import_dialog as import_dialog_module
from src.importing.import_dialog import ImportDialog


class _FakeController:
    pass


class _FakeImportWorker(QObject):
    progress = Signal(int, int)
    finished = Signal(int)
    error_occurred = Signal(str)
    resource_warning = Signal(str, float)
    art_conflicts = Signal(list)

    def __init__(self, controller, paths):
        super().__init__()
        self.controller = controller
        self.paths = paths
        self._running = False

    def start(self):
        self._running = True

    def isRunning(self):
        return self._running

    def request_cancel(self):
        self._running = False

    def wait(self):
        pass


@pytest.fixture
def dialog(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(import_dialog_module, "CONFIG_FILE", str(tmp_path / "import_paths.json"))
    monkeypatch.setattr(import_dialog_module, "ImportWorker", _FakeImportWorker)
    dlg = ImportDialog(_FakeController())
    yield dlg
    dlg.deleteLater()


def _checked_item(path):
    item = QListWidgetItem(str(path))
    item.setCheckState(Qt.Checked)
    return item


def test_empty_state_toggles_with_directory_list(dialog, tmp_path):
    # isHidden() reflects the explicit show()/hide() state regardless of
    # whether the (never-shown, headless-test) top-level dialog itself is
    # on screen -- isVisible() would be False for everything here either way.
    assert not dialog.empty_state_label.isHidden()
    assert dialog.dir_list.isHidden()

    dialog.dir_list.addItem(_checked_item(tmp_path))
    dialog._update_empty_state()

    assert dialog.empty_state_label.isHidden()
    assert not dialog.dir_list.isHidden()


def test_add_remove_buttons_disabled_while_import_runs(dialog, tmp_path):
    dialog.dir_list.addItem(_checked_item(tmp_path))

    dialog._start_import()
    assert not dialog.btn_add.isEnabled()
    assert not dialog.btn_remove.isEnabled()
    assert not dialog.progress_bar.isHidden()

    dialog.cancel_import()
    assert dialog.btn_add.isEnabled()
    assert dialog.btn_remove.isEnabled()
    assert dialog.progress_bar.isHidden()


def test_progress_bar_reflects_worker_progress(dialog, tmp_path):
    dialog.dir_list.addItem(_checked_item(tmp_path))
    dialog._start_import()

    dialog.import_worker.progress.emit(3, 10)

    assert dialog.progress_bar.minimum() == 0
    assert dialog.progress_bar.maximum() == 10
    assert dialog.progress_bar.value() == 3


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
