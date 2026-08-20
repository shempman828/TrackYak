"""Tests for docs/specs/genre_delete_add_to_exclusion.md: genre deletion's
confirmation dialog gains a checkbox to also add the deleted genre(s) to the
Excluded Genres config list. Each test below maps to one numbered acceptance
criterion in that spec.

Note: these tests never call the real QMessageBox.exec_() -- driving Qt's
native modal loop under the offscreen platform (or monkeypatching the native
exec_ method) is unreliable and has been observed to segfault. Instead,
_confirm_delete is patched per-instance to call the *real*
_build_delete_confirmation_box (so checkbox construction/labeling is still
exercised against production code) and then return a canned (confirmed,
add_to_excluded) result in place of running the modal loop -- i.e. "the user
saw this dialog and clicked X with the checkbox in state Y", without needing
an actual click.
"""

import configparser

import pytest
from PySide6.QtWidgets import QMessageBox
from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker

from src.common.alias_management_dialog import AliasManagementDialog
from src.db.db_helpers.delete import DeleteDB
from src.db.db_helpers.get import GetFromDB
from src.db.db_tables.base import Base
from src.db.db_tables.genre import Genre
from src.genre.genre_view import GenreView

CHECKBOX_LABEL = "Also add deleted genre(s) to Excluded Genres list"


class _StubConfig:
    """Stands in for src.core.config_setup.Config: same accessor surface the
    real Skipped/Excluded Genres tab and library_import.py use, backed by a
    plain list instead of config.ini."""

    def __init__(self, initial=None):
        self._excluded = list(initial or [])
        self.save_calls = 0

    def get_excluded_genres(self):
        return list(self._excluded)

    def set_excluded_genres(self, names):
        self._excluded = list(names)

    def save(self):
        self.save_calls += 1


class _Controller:
    def __init__(self, session, config=None):
        self.get = GetFromDB(session)
        self.delete = DeleteDB(session)
        self.config = config or _StubConfig()


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


@pytest.fixture
def controller(session):
    return _Controller(session)


def _make_genre(session, name, parent_id=None):
    genre = Genre(genre_name=name, parent_id=parent_id)
    session.add(genre)
    session.commit()
    return genre


def _patch_confirm_delete(view, monkeypatch, confirmed, checked, captured=None):
    """Patch view._confirm_delete so it builds the real confirmation box
    (proving the checkbox is genuinely attached with the right label/default
    state) but returns a canned result instead of blocking on box.exec_()."""
    real_build = view._build_delete_confirmation_box

    def _confirm_delete(message):
        box = real_build(message)
        checkbox = box._exclusion_checkbox
        if captured is not None:
            captured["message"] = message
            captured["checkbox_text"] = checkbox.text() if checkbox else None
            captured["checkbox_default_checked"] = checkbox.isChecked() if checkbox else None
            captured["standard_buttons"] = box.standardButtons()
        return confirmed, checked

    monkeypatch.setattr(view, "_confirm_delete", _confirm_delete)


# ---------------------------------------------------------------------------
# AC1 / AC2 / AC3 -- confirmation dialog includes the checkbox
# ---------------------------------------------------------------------------


def test_single_genre_delete_confirmation_has_checkbox(qapp, controller, monkeypatch):
    _make_genre(controller.get.session, "Rock")
    view = GenreView(controller)
    view.tree.selectAll()

    captured = {}
    _patch_confirm_delete(view, monkeypatch, confirmed=True, checked=False, captured=captured)

    view.delete_selected_genres()

    assert captured["checkbox_text"] == CHECKBOX_LABEL
    assert captured["checkbox_default_checked"] is False
    assert captured["standard_buttons"] == (QMessageBox.Yes | QMessageBox.No)


def test_multi_genre_delete_confirmation_has_checkbox(qapp, controller, monkeypatch):
    _make_genre(controller.get.session, "Rock")
    _make_genre(controller.get.session, "Jazz")
    view = GenreView(controller)
    view.tree.selectAll()

    captured = {}
    _patch_confirm_delete(view, monkeypatch, confirmed=True, checked=False, captured=captured)

    view.delete_selected_genres()

    assert captured["checkbox_text"] == CHECKBOX_LABEL
    assert captured["checkbox_default_checked"] is False


def test_legacy_delete_genre_confirmation_has_checkbox(qapp, controller, monkeypatch):
    genre = _make_genre(controller.get.session, "Rock")
    view = GenreView(controller)

    captured = {}
    _patch_confirm_delete(view, monkeypatch, confirmed=True, checked=False, captured=captured)

    view.delete_genre(genre.genre_id)

    assert captured["checkbox_text"] == CHECKBOX_LABEL
    assert captured["checkbox_default_checked"] is False


# ---------------------------------------------------------------------------
# AC4 / AC5 -- checked vs. unchecked controls the exclusion-list side effect
# ---------------------------------------------------------------------------


def test_unchecked_delete_leaves_excluded_genres_unchanged(qapp, controller, monkeypatch):
    _make_genre(controller.get.session, "Rock")
    view = GenreView(controller)
    view.tree.selectAll()
    _patch_confirm_delete(view, monkeypatch, confirmed=True, checked=False)

    view.delete_selected_genres()

    assert controller.config.get_excluded_genres() == []


def test_checked_delete_adds_genre_names_to_excluded_genres(qapp, controller, monkeypatch):
    _make_genre(controller.get.session, "Rock")
    _make_genre(controller.get.session, "Jazz")
    view = GenreView(controller)
    view.tree.selectAll()
    _patch_confirm_delete(view, monkeypatch, confirmed=True, checked=True)

    view.delete_selected_genres()

    assert set(controller.config.get_excluded_genres()) == {"Rock", "Jazz"}


# ---------------------------------------------------------------------------
# AC6 -- case-insensitive dedup against existing entries
# ---------------------------------------------------------------------------


def test_checked_delete_does_not_duplicate_existing_case_insensitive_entry(
    qapp, session, monkeypatch
):
    controller = _Controller(session, config=_StubConfig(initial=["rock"]))
    _make_genre(session, "Rock")
    view = GenreView(controller)
    view.tree.selectAll()
    _patch_confirm_delete(view, monkeypatch, confirmed=True, checked=True)

    view.delete_selected_genres()

    assert controller.config.get_excluded_genres() == ["rock"]


# ---------------------------------------------------------------------------
# AC7 -- a failed delete in a batch is not added to the exclusion list
# ---------------------------------------------------------------------------


def test_checked_delete_skips_exclusion_add_for_failed_genre(qapp, controller, monkeypatch):
    rock = _make_genre(controller.get.session, "Rock")
    jazz = _make_genre(controller.get.session, "Jazz")
    view = GenreView(controller)
    view.tree.selectAll()
    _patch_confirm_delete(view, monkeypatch, confirmed=True, checked=True)

    real_delete_entity = controller.delete.delete_entity

    def _flaky_delete_entity(model_name, entity_id=None, **kwargs):
        if entity_id == jazz.genre_id:
            raise SQLAlchemyError("simulated failure")
        return real_delete_entity(model_name, entity_id=entity_id, **kwargs)

    monkeypatch.setattr(controller.delete, "delete_entity", _flaky_delete_entity)

    view.delete_selected_genres()

    assert controller.config.get_excluded_genres() == ["Rock"]
    assert controller.get.get_entity_object("Genre", genre_id=jazz.genre_id) is not None
    assert controller.get.get_entity_object("Genre", genre_id=rock.genre_id) is None


# ---------------------------------------------------------------------------
# AC8 -- exclusion-list change is persisted to disk, not just in memory
# ---------------------------------------------------------------------------


def test_checked_delete_persists_to_scratch_config_file(qapp, controller, monkeypatch, tmp_path):
    from src.core import config_setup

    scratch_ini = tmp_path / "config.ini"
    monkeypatch.setattr(config_setup, "config", lambda name: str(scratch_ini))
    config_setup.Config._instance = None
    config_setup.Config._initialized = False
    real_config = config_setup.Config()
    controller.config = real_config

    _make_genre(controller.get.session, "Rock")
    view = GenreView(controller)
    view.tree.selectAll()
    _patch_confirm_delete(view, monkeypatch, confirmed=True, checked=True)

    view.delete_selected_genres()

    # Reset the singleton and read back a fresh Config instance from the
    # same scratch file, proving the change survived a save/reload cycle
    # rather than only living on the in-memory instance.
    config_setup.Config._instance = None
    config_setup.Config._initialized = False
    reloaded = config_setup.Config()
    assert reloaded.get_excluded_genres() == ["Rock"]

    raw = configparser.ConfigParser()
    raw.read(scratch_ini)
    assert raw["library"]["excluded_genres"] == "Rock"

    config_setup.Config._instance = None
    config_setup.Config._initialized = False


# ---------------------------------------------------------------------------
# AC9 / AC10 -- status bar text
# ---------------------------------------------------------------------------


def test_checked_bulk_delete_status_bar_reports_exclusion_count(qapp, controller, monkeypatch):
    _make_genre(controller.get.session, "Rock")
    _make_genre(controller.get.session, "Jazz")
    view = GenreView(controller)
    view.tree.selectAll()
    _patch_confirm_delete(view, monkeypatch, confirmed=True, checked=True)

    view.delete_selected_genres()

    assert view.status_bar.text() == "Deleted 2 genre(s), added 2 to Excluded Genres"


def test_unchecked_bulk_delete_status_bar_unchanged(qapp, controller, monkeypatch):
    _make_genre(controller.get.session, "Rock")
    _make_genre(controller.get.session, "Jazz")
    view = GenreView(controller)
    view.tree.selectAll()
    _patch_confirm_delete(view, monkeypatch, confirmed=True, checked=False)

    view.delete_selected_genres()

    assert view.status_bar.text() == "Deleted 2 genre(s)"
    assert "Excluded Genres" not in view.status_bar.text()


# ---------------------------------------------------------------------------
# AC11 -- shares one config source of truth with the Skipped Genres tab
# ---------------------------------------------------------------------------


class _FakeDialogSelf:
    """Lets us call AliasManagementDialog._create_skipped_genres_tab as an
    unbound method against a lightweight stand-in, without constructing the
    full 9-tab dialog (which needs a much larger DB-backed controller).
    Borrows the real add/remove/save methods too, since the tab's line-edit
    wires returnPressed to self._add_excluded_genre at construction time."""

    _add_excluded_genre = AliasManagementDialog._add_excluded_genre
    _remove_excluded_genre = AliasManagementDialog._remove_excluded_genre
    _save_excluded_genres = AliasManagementDialog._save_excluded_genres

    def __init__(self, controller):
        self.controller = controller


def test_skipped_genres_tab_reflects_names_added_via_delete(qapp, controller, monkeypatch):
    _make_genre(controller.get.session, "Rock")
    view = GenreView(controller)
    view.tree.selectAll()
    _patch_confirm_delete(view, monkeypatch, confirmed=True, checked=True)

    view.delete_selected_genres()

    fake_self = _FakeDialogSelf(controller)
    tab_page = AliasManagementDialog._create_skipped_genres_tab(fake_self)
    assert tab_page is not None  # keep it referenced so Qt doesn't GC the child QListWidget
    names = [
        fake_self.excluded_genres_list.item(i).text()
        for i in range(fake_self.excluded_genres_list.count())
    ]
    assert "Rock" in names
