"""Tests for src/artist/edit/artist_edit_member.py::_MembershipPanelBase._edit.

Covers the Finalize audit fix (2026-09-18): editing a GroupMembership row
used to delete it and re-add it, which could lose the membership entirely if
the add silently failed (add_entity/delete_entity never raise
SQLAlchemyError -- they return None/False). It now uses a single atomic
update_entity_by_filter call on the row's composite key instead.
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QMessageBox
import pytest

from src.artist.edit import artist_edit_member
from src.artist.edit.artist_edit_member import _MembershipPanelBase


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
        self._data = data

    def text(self):
        return self._text

    def data(self, _role):
        return self._data


class _FakeTable:
    def __init__(self, items):
        self._items = items  # {(row, col): _FakeItem}

    def item(self, row, col):
        return self._items.get((row, col))


class _FakeMembershipSelf:
    """Bare stand-in exposing only what _edit touches on self -- skips
    constructing the real table-backed widget."""

    def __init__(self, controller, table):
        self.controller = controller
        self.table = table
        self.reload_calls = 0

    def _reload(self):
        self.reload_calls += 1


class _FakeEditDialog:
    """Stand-in for _EditMembershipDialog that skips real QDialog
    construction (which requires a genuine QWidget parent)."""

    def __init__(self, role, start_year, end_year, is_current, parent=None):
        self._values = {
            "role": role or None,
            "active_start_year": int(start_year) if start_year else None,
            "active_end_year": int(end_year) if end_year else None,
            "is_current": 1 if is_current else 0,
        }

    def exec(self):
        return QDialog.Accepted

    def values(self):
        return self._values


def _table_for_row(role="Guitarist", start="1990", end="", current="No"):
    return _FakeTable(
        {
            (0, 0): _FakeItem(data=(10, 20)),
            (0, 1): _FakeItem(text=role),
            (0, 2): _FakeItem(text=start),
            (0, 3): _FakeItem(text=end),
            (0, 4): _FakeItem(text=current),
        }
    )


@pytest.fixture(autouse=True)
def _fake_dialog(monkeypatch):
    monkeypatch.setattr(artist_edit_member, "_EditMembershipDialog", _FakeEditDialog)
    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: None)


def test_edit_membership_uses_a_single_atomic_update():
    update = _FakeUpdate(result=True)
    fake = _FakeMembershipSelf(_FakeController(update), _table_for_row())

    _MembershipPanelBase._edit(fake, 0)

    assert update.calls == [
        (
            "GroupMembership",
            {"group_id": 10, "member_id": 20},
            {
                "role": "Guitarist",
                "active_start_year": 1990,
                "active_end_year": None,
                "is_current": 0,
            },
        )
    ]
    assert fake.reload_calls == 1


def test_edit_membership_failure_reports_error_without_losing_the_row(monkeypatch):
    critical_calls = []
    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: critical_calls.append(a))

    update = _FakeUpdate(result=False)
    fake = _FakeMembershipSelf(_FakeController(update), _table_for_row())

    _MembershipPanelBase._edit(fake, 0)

    # The update was attempted and reported as failed; unlike the old
    # delete-then-add pattern, nothing was ever deleted.
    assert update.calls
    assert critical_calls
    assert fake.reload_calls == 0


def test_edit_membership_no_selection_data_is_a_no_op():
    table = _FakeTable({(0, 0): _FakeItem(data=None)})
    update = _FakeUpdate(result=True)
    fake = _FakeMembershipSelf(_FakeController(update), table)

    _MembershipPanelBase._edit(fake, 0)

    assert update.calls == []
    assert fake.reload_calls == 0
