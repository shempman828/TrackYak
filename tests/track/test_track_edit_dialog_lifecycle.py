"""TrackEditDialog close/save lifecycle: tab cleanup on every close path, save failures keep the dialog open."""

from types import SimpleNamespace

from PySide6.QtWidgets import QMessageBox, QWidget
import pytest

from src.track.edit.track_edit import TrackEditDialog


class _Update:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def update_entities(self, model_name, ids, **kwargs):
        self.calls.append((model_name, list(ids), kwargs))
        return self.result


def _controller(update_result=True):
    return SimpleNamespace(update=_Update(update_result))


def _track(track_id=1, name="Old Title"):
    return SimpleNamespace(track_id=track_id, track_name=name)


def _dialog(controller):
    dlg = TrackEditDialog(_track(), controller)
    dlg.show()
    return dlg


def _spy_cleanup(dlg) -> list:
    calls = []
    dlg._tabs[0].cleanup = lambda: calls.append(1)
    return calls


def test_cancel_runs_tab_cleanup_once(qapp):
    dlg = _dialog(_controller())
    calls = _spy_cleanup(dlg)
    dlg.reject()
    dlg.close()  # a later close must not clean up again
    assert calls == [1]


def test_save_runs_tab_cleanup(qapp):
    dlg = _dialog(_controller())
    calls = _spy_cleanup(dlg)
    dlg._on_save()
    assert calls == [1]


def test_save_failure_keeps_dialog_open_and_shows_error(qapp):
    controller = _controller(update_result=False)
    dlg = _dialog(controller)
    assert dlg.set_live_track_name("New Title")

    dlg._on_save()

    assert controller.update.calls == [("Track", [1], {"track_name": "New Title"})]
    assert dlg.isVisible()
    assert not dlg._error_label.isHidden()
    assert "Could not save" in dlg._error_label.text()
    dlg._close_approved = True
    dlg.close()


def test_save_success_closes_dialog(qapp):
    controller = _controller(update_result=True)
    dlg = _dialog(controller)
    dlg.set_live_track_name("New Title")
    dlg._on_save()
    assert not dlg.isVisible()


def test_broken_tab_placeholder_is_skipped_on_save_and_close(qapp, monkeypatch):
    dlg = _dialog(_controller())
    dlg._tabs[1] = QWidget()  # what _ensure_tab_built stores when a tab fails to build
    errors = []
    monkeypatch.setattr("src.track.edit.track_edit.logger.exception", lambda *a, **k: errors.append(a))
    dlg._on_save()
    assert errors == []


def test_empty_track_list_is_rejected(qapp):
    with pytest.raises(ValueError):
        TrackEditDialog([], _controller())


def test_discard_prompt_cancel_keeps_dialog_open(qapp, monkeypatch):
    dlg = _dialog(_controller())
    calls = _spy_cleanup(dlg)
    dlg.set_live_track_name("New Title")
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Cancel)
    dlg.reject()
    assert dlg.isVisible()
    assert calls == []
    dlg._close_approved = True
    dlg.close()
