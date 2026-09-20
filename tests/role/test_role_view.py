"""Regression test for delete_role() comparing QMessageBox.question()'s
return value against QDialog.Yes (which doesn't exist -- only
QMessageBox.Yes does), raising AttributeError before the role could ever be
deleted. See src/role/role_view.py delete_role().
"""

import threading
from unittest.mock import MagicMock, patch

from PySide6.QtCore import Qt, QThread
from PySide6.QtWidgets import QMenu, QMessageBox
import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import scoped_session, sessionmaker

from src.db.db_helpers.delete import DeleteDB
from src.db.db_helpers.get import GetFromDB
from src.db.db_helpers.update import UpdateDB
from src.db.db_tables.album import Album
from src.db.db_tables.artist import Artist
from src.db.db_tables.associations import AlbumRoleAssociation, TrackArtistRole
from src.db.db_tables.base import Base
from src.db.db_tables.role import Role
from src.db.db_tables.track import Track
from src.role.role_loader_worker import RoleLoaderWorker
from src.role.role_tree_builder import RoleTreeBuilder
from src.role.role_view import RoleView


# ---- test_role_view_delete.py ------------------------------------------------
class _StubConfig:
    """Config accessor surface used by RoleView._add_to_excluded_roles."""

    def __init__(self, roles=None):
        self._roles = list(roles or [])
        self.save_calls = 0

    def get_excluded_roles(self):
        return list(self._roles)

    def set_excluded_roles(self, names):
        self._roles = list(names)

    def save(self):
        self.save_calls += 1


class _Controller_del:
    def __init__(self, session, config=None):
        self.get = GetFromDB(session)
        self.delete = DeleteDB(session)
        self.config = config or _StubConfig()


@pytest.fixture
def controller_del(session):
    return _Controller_del(session)


def _make_role_del(session, name):
    role = Role(role_name=name)
    session.add(role)
    session.commit()
    return role


def _patch_confirm_delete_role(view, monkeypatch, *, confirmed, checked, captured=None):
    """Patch view._confirm_delete_role so it builds the real confirmation box
    (proving the checkbox is genuinely attached with the right label) but
    returns a canned result instead of blocking on box.exec_()."""
    real_build = view._build_role_delete_confirmation_box

    def _confirm_delete_role(message):
        box = real_build(message)
        if captured is not None:
            captured["checkbox_text"] = box._exclusion_checkbox.text()
            captured["checkbox_default_checked"] = box._exclusion_checkbox.isChecked()
        return confirmed, checked

    monkeypatch.setattr(view, "_confirm_delete_role", _confirm_delete_role)


def test_confirmed_delete_removes_role(qapp, controller_del, monkeypatch):
    monkeypatch.setattr(RoleView, "load_roles", lambda self: None)
    role = _make_role_del(controller_del.get.session, "Guitar")
    view = RoleView(controller_del)

    _patch_confirm_delete_role(view, monkeypatch, confirmed=True, checked=False)

    view.delete_role(role.role_id)

    assert controller_del.get.get_entity_object("Role", role_id=role.role_id) is None
    assert view.status_bar.text() == "Deleted Guitar"


def test_declined_delete_keeps_role(qapp, controller_del, monkeypatch):
    monkeypatch.setattr(RoleView, "load_roles", lambda self: None)
    role = _make_role_del(controller_del.get.session, "Guitar")
    view = RoleView(controller_del)

    _patch_confirm_delete_role(view, monkeypatch, confirmed=False, checked=False)

    view.delete_role(role.role_id)

    assert controller_del.get.get_entity_object("Role", role_id=role.role_id) is not None


def test_delete_confirmation_box_has_excluded_roles_checkbox(qapp, controller_del, monkeypatch):
    monkeypatch.setattr(RoleView, "load_roles", lambda self: None)
    role = _make_role_del(controller_del.get.session, "Guitar")
    view = RoleView(controller_del)

    captured = {}
    _patch_confirm_delete_role(view, monkeypatch, confirmed=False, checked=False, captured=captured)
    view.delete_role(role.role_id)

    assert captured["checkbox_text"] == "Also add deleted role to Excluded Roles list"
    assert captured["checkbox_default_checked"] is False


def test_checked_delete_adds_role_to_excluded_and_reports_count(qapp, session, monkeypatch):
    config = _StubConfig()
    controller = _Controller_del(session, config)
    monkeypatch.setattr(RoleView, "load_roles", lambda self: None)
    role = _make_role_del(session, "Guitar")
    view = RoleView(controller)

    _patch_confirm_delete_role(view, monkeypatch, confirmed=True, checked=True)
    view.delete_role(role.role_id)

    assert config.get_excluded_roles() == ["Guitar"]
    assert config.save_calls == 1
    assert view.status_bar.text() == "Deleted Guitar, added 1 to Excluded Roles"


def test_unchecked_delete_leaves_excluded_roles_untouched(qapp, session, monkeypatch):
    config = _StubConfig()
    controller = _Controller_del(session, config)
    monkeypatch.setattr(RoleView, "load_roles", lambda self: None)
    role = _make_role_del(session, "Guitar")
    view = RoleView(controller)

    _patch_confirm_delete_role(view, monkeypatch, confirmed=True, checked=False)
    view.delete_role(role.role_id)

    assert config.get_excluded_roles() == []
    assert config.save_calls == 0
    assert view.status_bar.text() == "Deleted Guitar"
    assert "Excluded Roles" not in view.status_bar.text()


# ---- test_role_view_recursive_counts.py --------------------------------------
# Regression test: the role tree must roll up recursive (own + descendant)
# counts and display them with the genre/playlist "own · recursive"
# convention, instead of only ever showing each role's direct assignment
# count. See src/role/role_loader_worker.py RoleLoaderWorker /
# src/role/role_tree_builder.py RoleTreeBuilder._make_role_item.
class _Controller_rc:
    def __init__(self, session):
        self.get = GetFromDB(session)


def _make_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _run_worker(controller):
    """Runs RoleLoaderWorker.run() synchronously and captures its emit."""
    worker = RoleLoaderWorker(controller)
    payload = {}
    worker.finished.connect(
        lambda roles, albums, tracks, recursive: payload.update(
            all_roles=roles, album_counts=albums, track_counts=tracks, recursive_counts=recursive
        )
    )
    worker.run()
    return payload


def test_recursive_counts_roll_up_without_double_counting():
    session = _make_session()
    controller = _Controller_rc(session)

    parent = Role(role_name="String Instruments")
    child = Role(role_name="Guitar", parent=parent)
    session.add_all([parent, child])
    session.commit()

    album = Album(album_name="Test Album")
    artist_a = Artist(artist_name="Artist A")
    artist_b = Artist(artist_name="Artist B")
    track = Track(track_name="Test Track", album=album)
    session.add_all([album, artist_a, artist_b, track])
    session.commit()

    # One album credit directly on the parent role...
    session.add(
        AlbumRoleAssociation(
            album_id=album.album_id, artist_id=artist_a.artist_id, role_id=parent.role_id
        )
    )
    # ...and one track credit directly on the child role.
    session.add(
        TrackArtistRole(
            track_id=track.track_id, artist_id=artist_b.artist_id, role_id=child.role_id
        )
    )
    session.commit()

    payload = _run_worker(controller)

    # Own counts stay exactly as before: one direct assignment each.
    assert payload["album_counts"].get(parent.role_id, 0) == 1
    assert payload["track_counts"].get(child.role_id, 0) == 1

    # Recursive counts roll the child's credit up into the parent...
    assert payload["recursive_counts"][parent.role_id] == 2
    # ...but the child's own recursive count is unaffected by its parent.
    assert payload["recursive_counts"][child.role_id] == 1

    session.close()


def test_recursive_counts_do_not_dedupe_distinct_role_credits():
    """The same artist can hold two different roles on the same track (e.g.
    both a sibling "Guitar" and "Bass" role under "String Instruments") --
    those are two distinct credits and must both still be counted, not
    collapsed by an over-eager track/artist-only dedup key."""
    session = _make_session()
    controller = _Controller_rc(session)

    parent = Role(role_name="String Instruments")
    guitar = Role(role_name="Guitar", parent=parent)
    bass = Role(role_name="Bass", parent=parent)
    session.add_all([parent, guitar, bass])
    session.commit()

    artist = Artist(artist_name="Multi-Instrumentalist")
    album = Album(album_name="Test Album")
    track = Track(track_name="Test Track", album=album)
    session.add_all([artist, album, track])
    session.commit()

    session.add_all(
        [
            TrackArtistRole(
                track_id=track.track_id, artist_id=artist.artist_id, role_id=guitar.role_id
            ),
            TrackArtistRole(
                track_id=track.track_id, artist_id=artist.artist_id, role_id=bass.role_id
            ),
        ]
    )
    session.commit()

    payload = _run_worker(controller)

    assert payload["recursive_counts"][guitar.role_id] == 1
    assert payload["recursive_counts"][bass.role_id] == 1
    # Both sibling credits must roll up -- not dedupe to 1.
    assert payload["recursive_counts"][parent.role_id] == 2

    session.close()


def test_make_role_item_uses_own_recursive_display_convention(qapp):
    """Mirrors GenreView/PlaylistView's "own · recursive" tree label, per
    the "use genre and playlist tree count method and number display
    convention for roles too" request."""
    assert RoleTreeBuilder._format_role_count(1, 2) == "1 · 2"
    assert RoleTreeBuilder._format_role_count(3, 3) == "3"


# ---- test_role_view_search_filter.py -----------------------------------------
# Regression test: editing a role in the role view rebuilds the tree, and
# that rebuild used to drop the active search filter (every rebuilt item
# defaults to visible, and nothing reapplied `_filter_roles`). It also used to
# trigger a full database reload (`load_roles()`, a real background QThread
# doing 3 full-table queries) for a change that only touches a single row.
#
# See src/role/role_view.py `_rebuild_tree()` and `on_item_edited()`.
class _Controller_sf:
    def __init__(self, session):
        self.get = GetFromDB(session)
        self.update = UpdateDB(session)


@pytest.fixture
def controller_sf(session):
    return _Controller_sf(session)


def _make_role_sf(session, name):
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
    qapp, session, controller_sf, monkeypatch
):
    guitar = _make_role_sf(session, "Guitar")
    piano = _make_role_sf(session, "Piano")

    load_calls = {"n": 0}
    monkeypatch.setattr(
        RoleView, "load_roles", lambda self: load_calls.__setitem__("n", load_calls["n"] + 1)
    )

    view = RoleView(controller_sf)
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


def test_rename_to_same_name_with_stray_whitespace_is_not_rejected(
    qapp, session, controller_sf, monkeypatch
):
    """A rename that only trims/adds whitespace resolves back to the role's
    own current name -- the duplicate-name check must exclude the role being
    renamed, or this gets rejected as "already exists". See
    src/role/role_view.py on_item_edited()."""
    monkeypatch.setattr(RoleView, "load_roles", lambda self: None)
    guitar = _make_role_sf(session, "Guitar")

    view = RoleView(controller_sf)
    view._all_roles = [guitar]
    view._album_counts = {}
    view._track_counts = {}
    view._rebuild_tree()

    guitar_item = _item_for(view.role_tree, guitar.role_id)
    # Leading whitespace added to the display text; strips back down to the
    # exact same name already held by this role.
    guitar_item.setText(0, "  Guitar (0)")

    # on_item_edited's own rebuild replaces the tree items, so guitar_item is
    # now stale -- look the (new) item up again by role id.
    assert guitar.role_name == "Guitar"
    assert _item_for(view.role_tree, guitar.role_id).text(0) == "Guitar (0)"
    assert view.status_bar.text() == "Renamed to Guitar"


# ---- test_role_hierarchy_export.py -----------------------------------------
# The role tree's right-click context menu gains an "Export Hierarchy..."
# action that writes the full role hierarchy as a box-drawing tree to a .txt
# or .md file, independent of any active search filter, ordered by the tree's
# active sort mode. See src/role/role_view.py RoleView.export_hierarchy().
class _Controller_eh:
    def __init__(self, session):
        self.get = GetFromDB(session)
        self.update = UpdateDB(session)


@pytest.fixture
def controller_eh(session):
    return _Controller_eh(session)


@pytest.fixture(autouse=True)
def _no_bg_role_load(request, monkeypatch):
    """Keep RoleView's constructor from kicking off a background load that
    could race with the tests' hand-set ``_all_roles``. Only applied to the
    export-hierarchy tests (those taking ``controller_eh``)."""
    if "controller_eh" in request.fixturenames:
        monkeypatch.setattr(RoleView, "load_roles", lambda self: None)


def _make_role_eh(session, name, parent=None):
    role = Role(role_name=name, parent=parent)
    session.add(role)
    session.commit()
    return role


def test_context_menu_has_export_action_on_item_and_empty_space(session, qapp, controller_eh):
    guitar = _make_role_eh(session, "Guitar")
    view = RoleView(controller_eh)
    view._all_roles = [guitar]
    view._rebuild_tree()
    item = view.role_tree.topLevelItem(0)

    with patch("src.role.role_view.QMenu") as mock_menu_cls:
        mock_menu = mock_menu_cls.return_value
        with patch.object(view.role_tree, "itemAt", return_value=item):
            view.show_context_menu(view.role_tree.visualItemRect(item).center())
        action_labels = [c.args[0] for c in mock_menu.addAction.call_args_list]
        assert "Export Hierarchy..." in action_labels

    with patch("src.role.role_view.QMenu") as mock_menu_cls:
        mock_menu = mock_menu_cls.return_value
        with patch.object(view.role_tree, "itemAt", return_value=None):
            view.show_context_menu(view.role_tree.rect().bottomRight())
        action_labels = [c.args[0] for c in mock_menu.addAction.call_args_list]
        assert "Export Hierarchy..." in action_labels


def test_export_with_no_roles_shows_status_and_skips_dialog(qapp, controller_eh):
    view = RoleView(controller_eh)
    view._all_roles = []

    with (
        patch("src.role.role_view.QFileDialog.getSaveFileName") as mock_dialog,
        patch("src.role.role_view.show_status_message") as mock_status,
    ):
        view.export_hierarchy()

    mock_dialog.assert_not_called()
    mock_status.assert_called_once()
    assert "No roles available" in mock_status.call_args.args[1]


def test_export_opens_save_dialog_with_txt_md_filter(session, qapp, controller_eh):
    guitar = _make_role_eh(session, "Guitar")
    view = RoleView(controller_eh)
    view._all_roles = [guitar]

    with patch(
        "src.role.role_view.QFileDialog.getSaveFileName", return_value=("", "")
    ) as mock_dialog:
        view.export_hierarchy()

    args, _kwargs = mock_dialog.call_args
    assert args[2] == "role_hierarchy.txt"
    assert "*.txt" in args[3]
    assert "*.md" in args[3]


def test_txt_export_writes_plain_box_drawing_text_ignoring_filter(
    session, qapp, controller_eh, tmp_path
):
    strings = _make_role_eh(session, "Strings")
    guitar = _make_role_eh(session, "Guitar", parent=strings)
    view = RoleView(controller_eh)
    view._all_roles = [strings, guitar]
    view._rebuild_tree()
    view.search_field.setText("nonexistent-filter")  # hides everything in the tree

    out_path = tmp_path / "role_hierarchy.txt"
    with patch(
        "src.role.role_view.QFileDialog.getSaveFileName",
        return_value=(str(out_path), "Text Files (*.txt)"),
    ):
        view.export_hierarchy()

    assert out_path.read_text(encoding="utf-8") == "Strings\n└── Guitar"


def test_md_export_wraps_content_in_code_fence(session, qapp, controller_eh, tmp_path):
    strings = _make_role_eh(session, "Strings")
    _make_role_eh(session, "Guitar", parent=strings)
    view = RoleView(controller_eh)
    view._all_roles = list(view.controller.get.session.query(Role).all())

    out_path = tmp_path / "role_hierarchy.md"
    with patch(
        "src.role.role_view.QFileDialog.getSaveFileName",
        return_value=(str(out_path), "Markdown Files (*.md)"),
    ):
        view.export_hierarchy()

    assert out_path.read_text(encoding="utf-8") == "```\nStrings\n└── Guitar\n```"


def test_sibling_order_follows_active_sort_mode(session, qapp, controller_eh, tmp_path):
    alto = _make_role_eh(session, "Alto")
    bass = _make_role_eh(session, "Bass")
    view = RoleView(controller_eh)
    view._all_roles = [alto, bass]

    # Name mode -> alphabetical.
    view.sort_mode = "name"
    name_path = tmp_path / "by_name.txt"
    with patch(
        "src.role.role_view.QFileDialog.getSaveFileName",
        return_value=(str(name_path), "Text Files (*.txt)"),
    ):
        view.export_hierarchy()
    assert name_path.read_text(encoding="utf-8") == "Alto\nBass"

    # Count mode -> higher recursive count first.
    view.sort_mode = "count"
    view._recursive_counts = {alto.role_id: 1, bass.role_id: 9}
    count_path = tmp_path / "by_count.txt"
    with patch(
        "src.role.role_view.QFileDialog.getSaveFileName",
        return_value=(str(count_path), "Text Files (*.txt)"),
    ):
        view.export_hierarchy()
    assert count_path.read_text(encoding="utf-8") == "Bass\nAlto"


# ---- test_role_view_session_release.py -------------------------------------
# Regression: RoleLoaderWorker.run() must release its background thread's
# scoped_session in a finally block. load_roles() starts a fresh QThread on
# every Roles-nav revisit; the scoped_session registry hands each new OS
# thread its own Session, and without remove() that Session's pooled
# connection is never returned and its WAL read transaction stays open for
# the life of the process -- a connection leaked per revisit, which added up
# to most of a multi-GB RSS climb over a long view-switching session.
#
# See src/role/role_loader_worker.py RoleLoaderWorker.run().
class _Controller_sr:
    def __init__(self, session_factory):
        self.get = GetFromDB(session_factory)
        self.SessionFactory = session_factory


def test_role_loader_worker_releases_thread_local_session(qapp, tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'lib.db'}")
    Base.metadata.create_all(engine)
    factory = scoped_session(sessionmaker(bind=engine, expire_on_commit=False))

    seed = factory()
    parent = Role(role_name="Strings")
    child = Role(role_name="Guitar", parent=parent)
    album = Album(album_name="A")
    artist = Artist(artist_name="X")
    track = Track(track_name="t", album=album)
    seed.add_all([parent, child, album, artist, track])
    seed.commit()
    seed.add(
        AlbumRoleAssociation(
            album_id=album.album_id, artist_id=artist.artist_id, role_id=parent.role_id
        )
    )
    seed.add(
        TrackArtistRole(track_id=track.track_id, artist_id=artist.artist_id, role_id=child.role_id)
    )
    seed.commit()
    factory.remove()

    monkeypatch.setattr("src.db.db_engine.Session", factory, raising=False)

    real_remove = factory.remove
    calls = {}

    def spy_remove():
        calls["thread"] = threading.get_ident()
        calls["n"] = calls.get("n", 0) + 1
        return real_remove()

    monkeypatch.setattr(factory, "remove", spy_remove)

    worker = RoleLoaderWorker(_Controller_sr(factory))
    thread = QThread()
    worker.moveToThread(thread)
    done = {}
    thread.started.connect(worker.run)
    worker.finished.connect(lambda *a: done.setdefault("ok", True))
    worker.error.connect(lambda msg: done.setdefault("err", msg))
    worker.finished.connect(thread.quit)
    worker.error.connect(thread.quit)
    thread.start()

    budget = 5000
    while not thread.isFinished() and budget > 0:
        qapp.processEvents()
        thread.wait(50)
        budget -= 50
    assert thread.isFinished()
    thread.deleteLater()

    assert done.get("ok") and "err" not in done
    assert calls.get("n", 0) >= 1, (
        "run() never released its scoped_session — connection leaks per revisit"
    )
    assert calls["thread"] != threading.get_ident()

    factory.remove()
    engine.dispose()


# ---- test_role_view_change_parent.py ----------------------------------------
# "Change Parent" context-menu submenu: lets the user re-parent a role
# without drag-and-drop, built on the same shared populate_entity_submenu
# used by the player dock's cascading "Add to Playlist" / "Add to Mood"
# submenus. The clicked role and its own descendants must never appear as
# choices, since picking either would create a cycle in the hierarchy.
# See src/role/role_view.py show_context_menu / _populate_change_parent_submenu.


def _leaf_texts(menu):
    """Non-separator action texts, in order, for a real QMenu."""
    return [a.text() for a in menu.actions() if not a.isSeparator()]


def test_change_parent_submenu_placed_after_split_before_new_parent_role(
    session, qapp, controller_eh
):
    guitar = _make_role_eh(session, "Guitar")
    view = RoleView(controller_eh)
    view._all_roles = [guitar]
    view._rebuild_tree()
    item = view.role_tree.topLevelItem(0)

    created = []

    def _new_menu(*args, **kwargs):
        m = MagicMock()
        created.append(m)
        return m

    with (
        patch("src.role.role_view.QMenu", side_effect=_new_menu),
        # The submenu's own content is exercised by the tests below; this
        # test only cares where it's inserted among the top menu's actions,
        # and a real QAction can't be parented to a MagicMock submenu.
        patch.object(view, "_populate_change_parent_submenu"),
        patch.object(view.role_tree, "itemAt", return_value=item),
    ):
        view.show_context_menu(view.role_tree.visualItemRect(item).center())

    top_menu = created[0]
    change_parent_menu = created[1]
    calls = [(call[0], call[1][0] if call[1] else None) for call in top_menu.method_calls]
    split_idx = calls.index(("addAction", "Split…"))
    change_parent_idx = calls.index(("addMenu", change_parent_menu))
    new_parent_idx = calls.index(("addAction", "New Parent Role"))
    assert split_idx < change_parent_idx < new_parent_idx


def test_change_parent_submenu_excludes_clicked_role_and_descendants(session, qapp, controller_eh):
    parent = _make_role_eh(session, "Strings")
    child = _make_role_eh(session, "Guitar", parent=parent)
    sibling = _make_role_eh(session, "Percussion")
    view = RoleView(controller_eh)
    view._all_roles = [parent, child, sibling]
    view.current_role_id = parent.role_id

    submenu = QMenu()
    view._populate_change_parent_submenu(submenu)

    assert _leaf_texts(submenu) == ["Top Level (No Parent)", "Percussion"]
    assert submenu.actions()[1].isSeparator()


def test_change_parent_top_level_action_sets_parent_to_none(session, qapp, controller_eh):
    parent = _make_role_eh(session, "Strings")
    child = _make_role_eh(session, "Guitar", parent=parent)
    view = RoleView(controller_eh)
    view._all_roles = [parent, child]
    view.current_role_id = child.role_id
    view.load_roles = MagicMock()
    fired = []
    view.role_updated.connect(lambda: fired.append(True))

    submenu = QMenu()
    view._populate_change_parent_submenu(submenu)
    top_level_action = next(a for a in submenu.actions() if a.text() == "Top Level (No Parent)")
    top_level_action.trigger()

    session.refresh(child)
    assert child.parent_id is None
    view.load_roles.assert_called_once()
    assert fired == [True]


def test_change_parent_role_action_sets_new_parent(session, qapp, controller_eh):
    strings = _make_role_eh(session, "Strings")
    percussion = _make_role_eh(session, "Percussion")
    view = RoleView(controller_eh)
    view._all_roles = [strings, percussion]
    view.current_role_id = strings.role_id
    view.load_roles = MagicMock()
    fired = []
    view.role_updated.connect(lambda: fired.append(True))

    submenu = QMenu()
    view._populate_change_parent_submenu(submenu)
    percussion_action = next(a for a in submenu.actions() if a.text() == "Percussion")
    percussion_action.trigger()

    session.refresh(strings)
    assert strings.parent_id == percussion.role_id
    view.load_roles.assert_called_once()
    assert fired == [True]


def test_change_parent_branch_own_action_sets_new_parent(session, qapp, controller_eh):
    """A role with its own children renders as a submenu branch, not a plain
    leaf action -- picking the branch role itself (not one of its children)
    must still set it as the new parent, using the "Percussion" (not
    "Add to 'Percussion'") wording since this isn't an "add to" action."""
    guitar = _make_role_eh(session, "Guitar")
    percussion = _make_role_eh(session, "Percussion")
    drums = _make_role_eh(session, "Drums", parent=percussion)
    view = RoleView(controller_eh)
    view._all_roles = [guitar, percussion, drums]
    view.current_role_id = guitar.role_id
    view.load_roles = MagicMock()
    fired = []
    view.role_updated.connect(lambda: fired.append(True))

    submenu = QMenu()
    view._populate_change_parent_submenu(submenu)
    percussion_branch = next(a for a in submenu.actions() if a.text() == "Percussion")
    assert percussion_branch.menu() is not None, "Percussion has a child, so it must be a submenu"
    branch_self_action = next(
        a for a in percussion_branch.menu().actions() if a.text() == "Percussion"
    )
    branch_self_action.trigger()

    session.refresh(guitar)
    assert guitar.parent_id == percussion.role_id
    view.load_roles.assert_called_once()
    assert fired == [True]


def test_change_parent_failed_update_shows_critical_and_does_not_reload(
    session, qapp, controller_eh
):
    strings = _make_role_eh(session, "Strings")
    percussion = _make_role_eh(session, "Percussion")
    view = RoleView(controller_eh)
    view._all_roles = [strings, percussion]
    view.current_role_id = strings.role_id
    view.load_roles = MagicMock()
    fired = []
    view.role_updated.connect(lambda: fired.append(True))

    submenu = QMenu()
    view._populate_change_parent_submenu(submenu)
    percussion_action = next(a for a in submenu.actions() if a.text() == "Percussion")

    with (
        patch.object(view.controller.update, "update_entity", return_value=False),
        patch("src.role.role_view.QMessageBox.critical") as mock_critical,
    ):
        percussion_action.trigger()

    mock_critical.assert_called_once()
    view.load_roles.assert_not_called()
    assert fired == []
    session.refresh(strings)
    assert strings.parent_id is None


def test_change_parent_sqlalchemy_error_shows_critical_instead_of_propagating(
    session, qapp, controller_eh
):
    """update_entity raising SQLAlchemyError (not just RuntimeError) must be
    caught and shown as an error dialog, not propagate out uncaught. See
    src/role/role_view.py _context_change_role_parent()."""
    strings = _make_role_eh(session, "Strings")
    percussion = _make_role_eh(session, "Percussion")
    view = RoleView(controller_eh)
    view._all_roles = [strings, percussion]
    view.current_role_id = strings.role_id
    view.load_roles = MagicMock()

    submenu = QMenu()
    view._populate_change_parent_submenu(submenu)
    percussion_action = next(a for a in submenu.actions() if a.text() == "Percussion")

    with (
        patch.object(view.controller.update, "update_entity", side_effect=SQLAlchemyError("boom")),
        patch("src.role.role_view.QMessageBox.critical") as mock_critical,
    ):
        percussion_action.trigger()  # must not raise

    mock_critical.assert_called_once()
    view.load_roles.assert_not_called()


def test_drop_event_partial_failure_refreshes_and_notifies(session, qapp, controller_eh):
    """If one item in a multi-select drop moves successfully before a later
    one raises, the tree must still be refreshed and the user told, instead
    of silently drifting out of sync with the DB. See
    src/role/role_view.py on_drop_event()."""
    guitar = _make_role_eh(session, "Guitar")
    piano = _make_role_eh(session, "Piano")
    view = RoleView(controller_eh)
    view._all_roles = [guitar, piano]
    view._rebuild_tree()
    view.load_roles = MagicMock()
    fired = []
    view.role_updated.connect(lambda: fired.append(True))

    guitar_item = _item_for(view.role_tree, guitar.role_id)
    piano_item = _item_for(view.role_tree, piano.role_id)
    guitar_item.setSelected(True)
    piano_item.setSelected(True)

    calls = {"n": 0}

    def flaky_update(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return True
        raise SQLAlchemyError("boom")

    fake_event = MagicMock()
    fake_event.pos.return_value = view.role_tree.rect().bottomRight()

    with (
        patch.object(view.controller.update, "update_entity", side_effect=flaky_update),
        patch.object(view.role_tree, "itemAt", return_value=None),
        patch("src.role.role_view.show_status_message") as mock_status,
    ):
        view.on_drop_event(fake_event)  # must not raise

    view.load_roles.assert_called_once()
    assert fired == [True]
    mock_status.assert_called_once()
    fake_event.ignore.assert_called_once()


def test_create_new_parent_role_sqlalchemy_error_shows_critical(session, qapp, controller_eh):
    """A DB error from get_entity_object must be caught and shown as an
    error dialog, not propagate out uncaught. See src/role/role_view.py
    create_new_parent_role()."""
    guitar = _make_role_eh(session, "Guitar")
    view = RoleView(controller_eh)
    view._all_roles = [guitar]

    with (
        patch.object(view.controller.get, "get_entity_object", side_effect=SQLAlchemyError("boom")),
        patch("src.role.role_view.QMessageBox.critical") as mock_critical,
    ):
        view.create_new_parent_role(guitar.role_id)  # must not raise

    mock_critical.assert_called_once()


def test_create_new_child_role_sqlalchemy_error_shows_critical(session, qapp, controller_eh):
    """Same as create_new_parent_role: a DB error must be caught, not
    propagate out uncaught. See src/role/role_view.py
    create_new_child_role()."""
    guitar = _make_role_eh(session, "Guitar")
    view = RoleView(controller_eh)
    view._all_roles = [guitar]

    with (
        patch.object(view.controller.get, "get_entity_object", side_effect=SQLAlchemyError("boom")),
        patch("src.role.role_view.QMessageBox.critical") as mock_critical,
    ):
        view.create_new_child_role(guitar.role_id)  # must not raise

    mock_critical.assert_called_once()
