"""Tests for src/artist/edit/artist_edit_alias.py::AliasesTab._perform_swap.

Covers the Finalize audit fix (2026-09-18): add_entity/delete_entity/
update_entity never raise SQLAlchemyError -- they swallow it internally and
return None/False -- so the old rename-then-delete-then-add sequence could
lose the old primary name for good if the final add silently failed. The
steps are now ordered (add the new alias first, rename second, delete the
promoted alias last) and each return value is checked, so a failure at any
point never leaves the old name unrecoverable.
"""

from types import SimpleNamespace

from PySide6.QtWidgets import QMessageBox
import pytest

from src.artist.edit.artist_edit_alias import AliasesTab


class _FakeAdd:
    def __init__(self, entity=None):
        self.entity = entity
        self.calls = []

    def add_entity(self, model_name, **kwargs):
        self.calls.append((model_name, kwargs))
        return self.entity


class _FakeDelete:
    def __init__(self, result=True):
        self.result = result
        self.calls = []

    def delete_entity(self, model_name, entity_id):
        self.calls.append((model_name, entity_id))
        return self.result


class _FakeUpdate:
    def __init__(self, result=True):
        self.result = result
        self.calls = []

    def update_entity(self, model_name, entity_id, **kwargs):
        self.calls.append((model_name, entity_id, kwargs))
        return self.result


class _FakeController:
    def __init__(self, add=None, delete=None, update=None):
        self.add = add or _FakeAdd()
        self.delete = delete or _FakeDelete()
        self.update = update or _FakeUpdate()


class _FakeAliasTab:
    """Bare stand-in for AliasesTab's instance state -- _perform_swap only
    touches self.controller and self.artist, so the full Qt widget tree
    (built by EntityAliasesTab.__init__) isn't needed for this logic."""

    def __init__(self, controller, artist):
        self.controller = controller
        self.artist = artist


def _artist(artist_id=1, artist_name="Old Name"):
    return SimpleNamespace(artist_id=artist_id, artist_name=artist_name)


@pytest.fixture(autouse=True)
def _confirm_yes(monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: None)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: None)


def test_swap_adds_alias_then_renames_then_deletes_old():
    artist = _artist()
    new_alias = SimpleNamespace(alias_id=99)
    controller = _FakeController(add=_FakeAdd(entity=new_alias))
    tab = _FakeAliasTab(controller, artist)

    result = AliasesTab._perform_swap(tab, alias_id=10, new_primary="New Name", alias_type="")

    assert result is True
    assert artist.artist_name == "New Name"
    assert controller.add.calls == [
        ("ArtistAlias", {"artist_id": 1, "alias_name": "Old Name", "alias_type": "Former Name"})
    ]
    assert controller.delete.calls == [("ArtistAlias", 10)]


def test_swap_aborts_without_losing_old_name_when_alias_add_fails():
    artist = _artist()
    controller = _FakeController(add=_FakeAdd(entity=None))
    tab = _FakeAliasTab(controller, artist)

    result = AliasesTab._perform_swap(tab, alias_id=10, new_primary="New Name", alias_type="")

    assert result is False
    assert artist.artist_name == "Old Name"  # never renamed
    assert controller.delete.calls == []  # old alias row never touched


def test_swap_rolls_back_new_alias_when_rename_fails():
    artist = _artist()
    new_alias = SimpleNamespace(alias_id=99)
    controller = _FakeController(add=_FakeAdd(entity=new_alias), update=_FakeUpdate(result=False))
    tab = _FakeAliasTab(controller, artist)

    result = AliasesTab._perform_swap(tab, alias_id=10, new_primary="New Name", alias_type="")

    assert result is False
    assert artist.artist_name == "Old Name"
    assert controller.delete.calls == [("ArtistAlias", 99)]  # rolled back the new alias only


def test_swap_tolerates_failure_to_remove_the_promoted_alias():
    """The promoted alias row (alias_id) is deleted last; if that fails the
    worst case is a harmless leftover alias duplicating the new primary
    name, not a lost old name -- so the swap still reports success."""
    artist = _artist()
    new_alias = SimpleNamespace(alias_id=99)
    controller = _FakeController(add=_FakeAdd(entity=new_alias), delete=_FakeDelete(result=False))
    tab = _FakeAliasTab(controller, artist)

    result = AliasesTab._perform_swap(tab, alias_id=10, new_primary="New Name", alias_type="")

    assert result is True
    assert artist.artist_name == "New Name"
