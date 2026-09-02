"""Tests for AliasManagementDialog's parse-ignore list tabs.

Focus: the new "Skipped Roles" tab (docs/specs/role_parse_ignore_list.md),
built by the same _create_exclusion_tab() machinery as "Skipped Genres".
The add/remove/save handlers are exercised against the real dialog methods
via a lightweight stand-in, following the _FakeDialogSelf pattern from
tests/genre/test_genre_view.py.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.common.alias_management_dialog import AliasManagementDialog
from src.db.db_helpers.add import AddToDB
from src.db.db_helpers.delete import DeleteDB
from src.db.db_helpers.get import GetFromDB
from src.db.db_helpers.update import UpdateDB
from src.db.db_tables.base import Base


class _StubConfig:
    """Same accessor surface the Skipped Genres/Roles tabs use, backed by
    plain lists instead of config.ini."""

    def __init__(self, genres=None, roles=None):
        self._genres = list(genres or [])
        self._roles = list(roles or [])
        self.save_calls = 0

    def get_excluded_genres(self):
        return list(self._genres)

    def set_excluded_genres(self, names):
        self._genres = list(names)

    def get_excluded_roles(self):
        return list(self._roles)

    def set_excluded_roles(self, names):
        self._roles = list(names)

    def save(self):
        self.save_calls += 1


class _Controller:
    def __init__(self, session, config):
        self.get = GetFromDB(session)
        self.add = AddToDB(session)
        self.update = UpdateDB(session)
        self.delete = DeleteDB(session)
        self.config = config


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


class _FakeDialogSelf:
    """Call the real exclusion-tab builder/handlers without constructing
    the full multi-tab dialog."""

    _create_exclusion_tab = AliasManagementDialog._create_exclusion_tab
    _create_skipped_roles_tab = AliasManagementDialog._create_skipped_roles_tab
    _add_excluded = AliasManagementDialog._add_excluded
    _remove_excluded = AliasManagementDialog._remove_excluded
    _save_exclusion = AliasManagementDialog._save_exclusion

    def __init__(self, controller):
        self.controller = controller


# --- AC3: tab presence, order, population -----------------------------------


def test_skipped_roles_tab_follows_skipped_genres_and_lists_config(qapp, session):
    config = _StubConfig(genres=["Noise"], roles=["Composer", "Remixer"])
    dialog = AliasManagementDialog(_Controller(session, config))

    labels = [dialog.tabs.tabText(i) for i in range(dialog.tabs.count())]
    assert labels[0] == "Skipped Genres"
    assert labels[1] == "Skipped Roles"

    listed = [
        dialog.excluded_roles_list.item(i).text() for i in range(dialog.excluded_roles_list.count())
    ]
    assert listed == ["Composer", "Remixer"]


# --- AC4/5/6: add / dedupe / remove / persist -----------------------------


def test_add_excluded_role_appends_and_persists(qapp, session):
    config = _StubConfig()
    fake = _FakeDialogSelf(_Controller(session, config))
    tab = fake._create_skipped_roles_tab()
    assert tab is not None  # keep Qt children alive

    fake.excluded_role_edit.setText("Engineer")
    fake._add_excluded("excluded_roles_list", "excluded_role_edit", config.set_excluded_roles)

    assert config.get_excluded_roles() == ["Engineer"]
    assert config.save_calls == 1
    assert fake.excluded_role_edit.text() == ""


def test_add_duplicate_role_is_noop(qapp, session):
    config = _StubConfig(roles=["Engineer"])
    fake = _FakeDialogSelf(_Controller(session, config))
    tab = fake._create_skipped_roles_tab()
    assert tab is not None  # keep Qt children alive

    fake.excluded_role_edit.setText("engineer")  # different case
    fake._add_excluded("excluded_roles_list", "excluded_role_edit", config.set_excluded_roles)

    assert config.get_excluded_roles() == ["Engineer"]
    assert fake.excluded_roles_list.count() == 1


def test_remove_selected_role_persists_removal(qapp, session):
    config = _StubConfig(roles=["Engineer", "Composer"])
    fake = _FakeDialogSelf(_Controller(session, config))
    tab = fake._create_skipped_roles_tab()
    assert tab is not None  # keep Qt children alive

    fake.excluded_roles_list.setCurrentRow(0)  # "Engineer"
    fake._remove_excluded("excluded_roles_list", config.set_excluded_roles)

    assert config.get_excluded_roles() == ["Composer"]
