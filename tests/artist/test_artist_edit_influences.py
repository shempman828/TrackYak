"""Tests for src/artist/edit/artist_edit_influences.py::InfluencesTab's swap
and edit-description actions.

Covers the Finalize audit fix (2026-09-18): both actions used to delete an
ArtistInfluence row and re-add it, which could lose the relation entirely if
the add silently failed -- add_entity/delete_entity never raise
SQLAlchemyError, they swallow it and return None/False, so the old
try/except around the pair never caught anything. Both actions now use a
single atomic update_entity_by_filter call on the row's composite key
instead, so a failure leaves the original row untouched.
"""

from types import SimpleNamespace

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QMessageBox
import pytest

from src.artist.edit import artist_edit_influences
from src.artist.edit.artist_edit_influences import DIR_INFLUENCED, InfluencesTab


class _FakeUpdate:
    def __init__(self, result=True):
        self.result = result
        self.calls = []

    def update_entity_by_filter(self, model_name, filters, **kwargs):
        self.calls.append((model_name, filters, kwargs))
        return self.result


class _FakeController:
    def __init__(self, update=None):
        self.update = update or _FakeUpdate()


class _FakeItem:
    def __init__(self, text="", data=None):
        self._text = text
        self._data = data or {}

    def text(self):
        return self._text

    def data(self, role):
        return self._data.get(role)


class _FakeTable:
    def __init__(self, items):
        self._items = items  # {(row, col): _FakeItem}

    def item(self, row, col):
        return self._items.get((row, col))


class _FakeInfluencesSelf:
    """Bare stand-in exposing only what the two actions touch on self --
    skips constructing the real table-backed widget, which isn't relevant
    to the DB-call logic being fixed."""

    _row_key = InfluencesTab._row_key

    def __init__(self, controller, rows=None, selected=None, table=None):
        self.controller = controller
        self.artist = SimpleNamespace(artist_id=1)
        self._rows = rows or []
        self._selected = selected or []
        self.table = table
        self.reload_calls = 0

    def _selected_row_data(self):
        return self._selected

    def _reload_and_refresh(self):
        self.reload_calls += 1


@pytest.fixture(autouse=True)
def _no_modal_dialogs(monkeypatch):
    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: None)


# ── _handle_swap_selected ──────────────────────────────────────────────────


def test_swap_selected_uses_a_single_atomic_update():
    row = {"direction": DIR_INFLUENCED, "other_id": 2, "name": "Other", "description": "d"}
    update = _FakeUpdate(result=True)
    fake = _FakeInfluencesSelf(_FakeController(update), rows=[row], selected=[row])

    InfluencesTab._handle_swap_selected(fake)

    assert update.calls == [
        (
            "ArtistInfluence",
            {"influencer_id": 1, "influenced_id": 2},
            {"influencer_id": 2, "influenced_id": 1},
        )
    ]
    assert fake.reload_calls == 1


def test_swap_selected_never_deletes_the_row_it_cannot_replace():
    row = {"direction": DIR_INFLUENCED, "other_id": 2, "name": "Other", "description": "d"}
    update = _FakeUpdate(result=False)
    fake = _FakeInfluencesSelf(_FakeController(update), rows=[row], selected=[row])

    InfluencesTab._handle_swap_selected(fake)

    # Only the single update was attempted -- no separate delete call exists
    # in this code path any more, so a failed swap can't lose the row.
    assert update.calls == [
        (
            "ArtistInfluence",
            {"influencer_id": 1, "influenced_id": 2},
            {"influencer_id": 2, "influenced_id": 1},
        )
    ]
    assert fake.reload_calls == 1


# ── _handle_edit_description ────────────────────────────────────────────────


class _FakeDescDialog:
    """Stand-in for _EditDescriptionDialog that skips real QDialog
    construction (which requires a genuine QWidget parent)."""

    return_value = "New description"

    def __init__(self, description, parent=None):
        self.description = description

    def exec(self):
        return QDialog.Accepted

    def value(self):
        return type(self).return_value


def test_edit_description_uses_update_entity_by_filter(monkeypatch):
    monkeypatch.setattr(artist_edit_influences, "_EditDescriptionDialog", _FakeDescDialog)
    _FakeDescDialog.return_value = "New description"

    rel_item = _FakeItem(data={Qt.UserRole: 2, Qt.UserRole + 1: DIR_INFLUENCED})
    desc_item = _FakeItem(text="Old description")
    table = _FakeTable({(0, 1): rel_item, (0, 2): desc_item})
    update = _FakeUpdate(result=True)
    fake = _FakeInfluencesSelf(_FakeController(update), table=table)

    InfluencesTab._handle_edit_description(fake, 0)

    assert update.calls == [
        (
            "ArtistInfluence",
            {"influencer_id": 1, "influenced_id": 2},
            {"description": "New description"},
        )
    ]
    assert fake.reload_calls == 1


def test_edit_description_failure_reports_error_without_losing_the_row(monkeypatch):
    monkeypatch.setattr(artist_edit_influences, "_EditDescriptionDialog", _FakeDescDialog)
    _FakeDescDialog.return_value = "New description"
    critical_calls = []
    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: critical_calls.append(a))

    rel_item = _FakeItem(data={Qt.UserRole: 2, Qt.UserRole + 1: DIR_INFLUENCED})
    desc_item = _FakeItem(text="Old description")
    table = _FakeTable({(0, 1): rel_item, (0, 2): desc_item})
    update = _FakeUpdate(result=False)
    fake = _FakeInfluencesSelf(_FakeController(update), table=table)

    InfluencesTab._handle_edit_description(fake, 0)

    # The update was attempted and reported as failed; unlike the old
    # delete-then-add pattern, nothing was deleted along the way.
    assert update.calls
    assert critical_calls
    assert fake.reload_calls == 0
