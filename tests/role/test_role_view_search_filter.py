"""Regression test: editing a role in the role view rebuilds the tree, and
that rebuild used to drop the active search filter (every rebuilt item
defaults to visible, and nothing reapplied `_filter_roles`). It also used to
trigger a full database reload (`load_roles()`, a real background QThread
doing 3 full-table queries) for a change that only touches a single row.

See src/role/role_view.py `_rebuild_tree()` and `on_item_edited()`.
"""

import pytest
from PySide6.QtCore import Qt
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.db_helpers.get import GetFromDB
from src.db.db_helpers.update import UpdateDB
from src.db.db_tables.base import Base
from src.db.db_tables.role import Role
from src.role.role_view import RoleView


class _Controller:
    def __init__(self, session):
        self.get = GetFromDB(session)
        self.update = UpdateDB(session)


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


def _item_for(tree, role_id):
    root = tree.invisibleRootItem()
    for i in range(root.childCount()):
        item = root.child(i)
        if item.data(0, Qt.UserRole) == role_id:
            return item
    return None


def test_rename_preserves_search_filter_without_full_reload(
    qapp, session, controller, monkeypatch
):
    guitar = _make_role(session, "Guitar")
    piano = _make_role(session, "Piano")

    load_calls = {"n": 0}
    monkeypatch.setattr(
        RoleView, "load_roles", lambda self: load_calls.__setitem__("n", load_calls["n"] + 1)
    )

    view = RoleView(controller)
    view._all_roles = [guitar, piano]
    view._album_counts = {}
    view._track_counts = {}
    view._rebuild_tree()

    view.search_field.setText("Guitar")
    assert _item_for(view.role_tree, piano.role_id).isHidden() is True
    assert _item_for(view.role_tree, guitar.role_id).isHidden() is False

    # setText() on a tree item synchronously emits itemChanged, which the
    # tree already connects to on_item_edited() -- no explicit call needed
    # (and calling it again here would touch a QTreeWidgetItem the rename's
    # own rebuild already destroyed).
    guitar_item = _item_for(view.role_tree, guitar.role_id)
    guitar_item.setText(0, "Classical Guitar")

    # The rename must not have gone through a full reload.
    assert load_calls["n"] == 1  # only the constructor's initial call

    # The cache and the DB must both reflect the rename...
    assert guitar.role_name == "Classical Guitar"

    # ...and the search filter must still be honored after the rebuild.
    assert _item_for(view.role_tree, piano.role_id).isHidden() is True
    assert _item_for(view.role_tree, guitar.role_id).isHidden() is False
