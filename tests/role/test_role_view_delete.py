"""Regression test for delete_role() comparing QMessageBox.question()'s
return value against QDialog.Yes (which doesn't exist -- only
QMessageBox.Yes does), raising AttributeError before the role could ever be
deleted. See src/role/role_view.py delete_role().
"""

import pytest
from PySide6.QtWidgets import QMessageBox
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.db_helpers.delete import DeleteDB
from src.db.db_helpers.get import GetFromDB
from src.db.db_tables.base import Base
from src.db.db_tables.role import Role
from src.role.role_view import RoleView


class _Controller:
    def __init__(self, session):
        self.get = GetFromDB(session)
        self.delete = DeleteDB(session)


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


def _make_role(session, name):
    role = Role(role_name=name)
    session.add(role)
    session.commit()
    return role


def test_confirmed_delete_removes_role(qapp, controller, monkeypatch):
    monkeypatch.setattr(RoleView, "load_roles", lambda self: None)
    role = _make_role(controller.get.session, "Guitar")
    view = RoleView(controller)

    monkeypatch.setattr(
        "src.role.role_view.QMessageBox.question", lambda *a, **k: QMessageBox.Yes
    )

    view.delete_role(role.role_id)

    assert controller.get.get_entity_object("Role", role_id=role.role_id) is None
    assert view.status_bar.text() == "Deleted Guitar"


def test_declined_delete_keeps_role(qapp, controller, monkeypatch):
    monkeypatch.setattr(RoleView, "load_roles", lambda self: None)
    role = _make_role(controller.get.session, "Guitar")
    view = RoleView(controller)

    monkeypatch.setattr(
        "src.role.role_view.QMessageBox.question", lambda *a, **k: QMessageBox.No
    )

    view.delete_role(role.role_id)

    assert controller.get.get_entity_object("Role", role_id=role.role_id) is not None
