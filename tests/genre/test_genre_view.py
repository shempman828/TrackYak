"""Tests for docs/specs/genre_hierarchy_export.md: the genre tree's
right-click context menu gains an "Export Hierarchy..." action that writes
the full genre hierarchy as a box-drawing tree to a .txt or .md file,
independent of the current Flat View toggle or search filter, ordered by
the tree's active sort column/direction. Each test maps to one numbered
acceptance criterion in that spec (criteria 1-3 are covered directly
against render_hierarchy_as_text in tests/common/test_hierarchy_tree_render.py).

tests/genre/conftest.py patches GenreLoaderWorker.start to run synchronously,
so GenreView(controller_he) can be used exactly as before this feature.
"""

import configparser
from unittest.mock import patch

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox
import pytest
from sqlalchemy.exc import SQLAlchemyError

from src.common.alias_management_dialog import AliasManagementDialog
from src.db.db_helpers.delete import DeleteDB
from src.db.db_helpers.get import GetFromDB
from src.db.db_helpers.update import UpdateDB
from src.db.db_tables.associations import TrackGenre
from src.db.db_tables.genre import Genre
from src.db.db_tables.track import Track
from src.genre.genre_view import GenreLoaderWorker, GenreView


# ---- test_genre_hierarchy_export.py ------------------------------------------
class _Controller_he:
    def __init__(self, session):
        self.get = GetFromDB(session)
        self.update = UpdateDB(session)
        self.delete = DeleteDB(session)


@pytest.fixture
def controller_he(session):
    return _Controller_he(session)


def _make_genre_he(session, name, parent=None):
    genre = Genre(genre_name=name, parent=parent)
    session.add(genre)
    session.commit()
    return genre


def _add_tracks_with_genre_he(session, genre, n):
    for i in range(n):
        track = Track(track_name=f"{genre.genre_name} track {i}")
        session.add(track)
        session.flush()
        session.add(TrackGenre(track_id=track.track_id, genre_id=genre.genre_id))
    session.commit()


def test_context_menu_has_export_action_on_item_and_empty_space(session, qapp, controller_he):
    _make_genre_he(session, "Rock")
    view = GenreView(controller_he)
    item = view.tree.topLevelItem(0)

    with patch("src.genre.genre_view.QMenu") as mock_menu_cls:
        mock_menu = mock_menu_cls.return_value
        with patch.object(view.tree, "itemAt", return_value=item):
            view.show_context_menu(view.tree.visualItemRect(item).center())
        action_labels = [c.args[0] for c in mock_menu.addAction.call_args_list]
        assert "Export Hierarchy..." in action_labels

    with patch("src.genre.genre_view.QMenu") as mock_menu_cls:
        mock_menu = mock_menu_cls.return_value
        with patch.object(view.tree, "itemAt", return_value=None):
            view.show_context_menu(view.tree.rect().bottomRight())
        action_labels = [c.args[0] for c in mock_menu.addAction.call_args_list]
        assert action_labels == ["Export Hierarchy..."]


def test_export_with_no_genres_shows_status_and_skips_dialog(qapp, controller_he):
    view = GenreView(controller_he)
    assert view._all_genres == []

    with (
        patch("src.genre.genre_view.QFileDialog.getSaveFileName") as mock_dialog,
        patch("src.genre.genre_view.show_status_message") as mock_status,
    ):
        view.export_hierarchy()

    mock_dialog.assert_not_called()
    mock_status.assert_called_once()
    assert "No genres available" in mock_status.call_args.args[1]


def test_export_opens_save_dialog_with_txt_md_filter(session, qapp, controller_he, tmp_path):
    _make_genre_he(session, "Rock")
    view = GenreView(controller_he)

    with patch(
        "src.genre.genre_view.QFileDialog.getSaveFileName", return_value=("", "")
    ) as mock_dialog:
        view.export_hierarchy()

    args, _kwargs = mock_dialog.call_args
    assert args[2] == "genre_hierarchy.txt"
    assert "*.txt" in args[3]
    assert "*.md" in args[3]


def test_txt_export_writes_plain_box_drawing_text(session, qapp, controller_he, tmp_path):
    rock = _make_genre_he(session, "Rock")
    _make_genre_he(session, "Punk", parent=rock)
    view = GenreView(controller_he)

    out_path = tmp_path / "hierarchy.txt"
    with patch(
        "src.genre.genre_view.QFileDialog.getSaveFileName",
        return_value=(str(out_path), "Text Files (*.txt)"),
    ):
        view.export_hierarchy()

    content = out_path.read_text(encoding="utf-8")
    assert content == "Rock\n└── Punk"


def test_md_export_wraps_content_in_code_fence(session, qapp, controller_he, tmp_path):
    rock = _make_genre_he(session, "Rock")
    _make_genre_he(session, "Punk", parent=rock)
    view = GenreView(controller_he)

    out_path = tmp_path / "hierarchy.md"
    with patch(
        "src.genre.genre_view.QFileDialog.getSaveFileName",
        return_value=(str(out_path), "Markdown Files (*.md)"),
    ):
        view.export_hierarchy()

    content = out_path.read_text(encoding="utf-8")
    assert content == "```\nRock\n└── Punk\n```"


def test_export_identical_regardless_of_flat_view(session, qapp, controller_he, tmp_path):
    rock = _make_genre_he(session, "Rock")
    _make_genre_he(session, "Punk", parent=rock)
    view = GenreView(controller_he)

    def _export():
        out_path = tmp_path / "out.txt"
        with patch(
            "src.genre.genre_view.QFileDialog.getSaveFileName",
            return_value=(str(out_path), "Text Files (*.txt)"),
        ):
            view.export_hierarchy()
        return out_path.read_text(encoding="utf-8")

    tree_view_output = _export()

    view.flat_view_button.setChecked(True)
    view.toggle_flat_view()
    flat_view_output = _export()

    assert tree_view_output == flat_view_output


def test_export_identical_regardless_of_search_filter(session, qapp, controller_he, tmp_path):
    rock = _make_genre_he(session, "Rock")
    _make_genre_he(session, "Punk", parent=rock)
    _make_genre_he(session, "Jazz")
    view = GenreView(controller_he)

    def _export():
        out_path = tmp_path / "out.txt"
        with patch(
            "src.genre.genre_view.QFileDialog.getSaveFileName",
            return_value=(str(out_path), "Text Files (*.txt)"),
        ):
            view.export_hierarchy()
        return out_path.read_text(encoding="utf-8")

    unfiltered_output = _export()

    view.search_bar.setText("Punk")
    filtered_output = _export()

    assert unfiltered_output == filtered_output


def test_export_follows_name_ascending_sort(session, qapp, controller_he, tmp_path):
    _make_genre_he(session, "Zydeco")
    _make_genre_he(session, "Alpha")
    view = GenreView(controller_he)
    view.tree.sortByColumn(0, Qt.SortOrder.AscendingOrder)

    out_path = tmp_path / "out.txt"
    with patch(
        "src.genre.genre_view.QFileDialog.getSaveFileName",
        return_value=(str(out_path), "Text Files (*.txt)"),
    ):
        view.export_hierarchy()

    assert out_path.read_text(encoding="utf-8") == "Alpha\nZydeco"


def test_export_follows_tracks_descending_sort_with_alphabetical_tiebreak(
    session, qapp, controller_he, tmp_path
):
    rock = _make_genre_he(session, "Rock")
    punk = _make_genre_he(session, "Punk", parent=rock)
    jazz = _make_genre_he(session, "Jazz")
    _add_tracks_with_genre_he(session, rock, 1)
    _add_tracks_with_genre_he(session, punk, 5)  # rock recursive = 6
    _add_tracks_with_genre_he(session, jazz, 2)
    alt = _make_genre_he(session, "Alt")
    _add_tracks_with_genre_he(session, alt, 2)  # ties with Jazz at 2

    view = GenreView(controller_he)
    view.tree.sortByColumn(1, Qt.SortOrder.DescendingOrder)

    out_path = tmp_path / "out.txt"
    with patch(
        "src.genre.genre_view.QFileDialog.getSaveFileName",
        return_value=(str(out_path), "Text Files (*.txt)"),
    ):
        view.export_hierarchy()

    # Rock (6) first, then Alt/Jazz tied at 2 (alphabetical tie-break).
    assert out_path.read_text(encoding="utf-8") == "Rock\n└── Punk\nAlt\nJazz"


def test_export_success_shows_status_message_with_count_and_path(
    session, qapp, controller_he, tmp_path
):
    _make_genre_he(session, "Rock")
    _make_genre_he(session, "Jazz")
    view = GenreView(controller_he)

    out_path = tmp_path / "out.txt"
    with (
        patch(
            "src.genre.genre_view.QFileDialog.getSaveFileName",
            return_value=(str(out_path), "Text Files (*.txt)"),
        ),
        patch("src.genre.genre_view.show_status_message") as mock_status,
    ):
        view.export_hierarchy()

    message = mock_status.call_args.args[1]
    assert "2" in message
    assert str(out_path) in message


def test_export_write_failure_shows_error_dialog(session, qapp, controller_he, tmp_path):
    _make_genre_he(session, "Rock")
    view = GenreView(controller_he)

    out_path = tmp_path / "out.txt"
    with (
        patch(
            "src.genre.genre_view.QFileDialog.getSaveFileName",
            return_value=(str(out_path), "Text Files (*.txt)"),
        ),
        patch("builtins.open", side_effect=OSError("permission denied")),
        patch("src.genre.genre_view.QMessageBox.critical") as mock_critical,
        patch("src.genre.genre_view.show_status_message") as mock_status,
    ):
        view.export_hierarchy()

    mock_critical.assert_called_once()
    mock_status.assert_not_called()


def test_export_contains_only_genre_names(session, qapp, controller_he, tmp_path):
    rock = _make_genre_he(session, "Rock")
    rock.description = "A genre about rocks, apparently"
    session.commit()
    _add_tracks_with_genre_he(session, rock, 42)
    view = GenreView(controller_he)

    out_path = tmp_path / "out.txt"
    with patch(
        "src.genre.genre_view.QFileDialog.getSaveFileName",
        return_value=(str(out_path), "Text Files (*.txt)"),
    ):
        view.export_hierarchy()

    content = out_path.read_text(encoding="utf-8")
    assert content == "Rock"
    assert str(rock.genre_id) not in content
    assert "42" not in content
    assert "rocks" not in content


# ---- test_genre_view_delete_exclusion.py -------------------------------------
# Tests for docs/specs/genre_delete_add_to_exclusion.md: genre deletion's
# confirmation dialog gains a checkbox to also add the deleted genre(s) to the
# Excluded Genres config list. Each test below maps to one numbered acceptance
# criterion in that spec.
#
# Note: these tests never call the real QMessageBox.exec_() -- driving Qt's
# native modal loop under the offscreen platform (or monkeypatching the native
# exec_ method) is unreliable and has been observed to segfault. Instead,
# _confirm_delete is patched per-instance to call the *real*
# _build_delete_confirmation_box (so checkbox construction/labeling is still
# exercised against production code) and then return a canned (confirmed,
# add_to_excluded) result in place of running the modal loop -- i.e. "the user
# saw this dialog and clicked X with the checkbox in state Y", without needing
# an actual click.
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


class _Controller_de:
    def __init__(self, session, config=None):
        self.get = GetFromDB(session)
        self.delete = DeleteDB(session)
        self.config = config or _StubConfig()


@pytest.fixture
def controller_de(session):
    return _Controller_de(session)


def _make_genre_de(session, name, parent_id=None):
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


def test_single_genre_delete_confirmation_has_checkbox(qapp, controller_de, monkeypatch):
    _make_genre_de(controller_de.get.session, "Rock")
    view = GenreView(controller_de)
    view.tree.selectAll()

    captured = {}
    _patch_confirm_delete(view, monkeypatch, confirmed=True, checked=False, captured=captured)

    view.delete_selected_genres()

    assert captured["checkbox_text"] == CHECKBOX_LABEL
    assert captured["checkbox_default_checked"] is False
    assert captured["standard_buttons"] == (QMessageBox.Yes | QMessageBox.No)


def test_multi_genre_delete_confirmation_has_checkbox(qapp, controller_de, monkeypatch):
    _make_genre_de(controller_de.get.session, "Rock")
    _make_genre_de(controller_de.get.session, "Jazz")
    view = GenreView(controller_de)
    view.tree.selectAll()

    captured = {}
    _patch_confirm_delete(view, monkeypatch, confirmed=True, checked=False, captured=captured)

    view.delete_selected_genres()

    assert captured["checkbox_text"] == CHECKBOX_LABEL
    assert captured["checkbox_default_checked"] is False


def test_legacy_delete_genre_confirmation_has_checkbox(qapp, controller_de, monkeypatch):
    genre = _make_genre_de(controller_de.get.session, "Rock")
    view = GenreView(controller_de)

    captured = {}
    _patch_confirm_delete(view, monkeypatch, confirmed=True, checked=False, captured=captured)

    view.delete_genre(genre.genre_id)

    assert captured["checkbox_text"] == CHECKBOX_LABEL
    assert captured["checkbox_default_checked"] is False


def test_unchecked_delete_leaves_excluded_genres_unchanged(qapp, controller_de, monkeypatch):
    _make_genre_de(controller_de.get.session, "Rock")
    view = GenreView(controller_de)
    view.tree.selectAll()
    _patch_confirm_delete(view, monkeypatch, confirmed=True, checked=False)

    view.delete_selected_genres()

    assert controller_de.config.get_excluded_genres() == []


def test_checked_delete_adds_genre_names_to_excluded_genres(qapp, controller_de, monkeypatch):
    _make_genre_de(controller_de.get.session, "Rock")
    _make_genre_de(controller_de.get.session, "Jazz")
    view = GenreView(controller_de)
    view.tree.selectAll()
    _patch_confirm_delete(view, monkeypatch, confirmed=True, checked=True)

    view.delete_selected_genres()

    assert set(controller_de.config.get_excluded_genres()) == {"Rock", "Jazz"}


def test_checked_delete_does_not_duplicate_existing_case_insensitive_entry(
    qapp, session, monkeypatch
):
    controller_de = _Controller_de(session, config=_StubConfig(initial=["rock"]))
    _make_genre_de(session, "Rock")
    view = GenreView(controller_de)
    view.tree.selectAll()
    _patch_confirm_delete(view, monkeypatch, confirmed=True, checked=True)

    view.delete_selected_genres()

    assert controller_de.config.get_excluded_genres() == ["rock"]


def test_checked_delete_skips_exclusion_add_for_failed_genre(qapp, controller_de, monkeypatch):
    rock = _make_genre_de(controller_de.get.session, "Rock")
    jazz = _make_genre_de(controller_de.get.session, "Jazz")
    view = GenreView(controller_de)
    view.tree.selectAll()
    _patch_confirm_delete(view, monkeypatch, confirmed=True, checked=True)

    real_delete_entity = controller_de.delete.delete_entity

    def _flaky_delete_entity(model_name, entity_id=None, **kwargs):
        if entity_id == jazz.genre_id:
            raise SQLAlchemyError("simulated failure")
        return real_delete_entity(model_name, entity_id=entity_id, **kwargs)

    monkeypatch.setattr(controller_de.delete, "delete_entity", _flaky_delete_entity)

    view.delete_selected_genres()

    assert controller_de.config.get_excluded_genres() == ["Rock"]
    assert controller_de.get.get_entity_object("Genre", genre_id=jazz.genre_id) is not None
    assert controller_de.get.get_entity_object("Genre", genre_id=rock.genre_id) is None


def test_checked_delete_persists_to_scratch_config_file(qapp, controller_de, monkeypatch, tmp_path):
    from src.core import config_setup

    scratch_ini = tmp_path / "config.ini"
    monkeypatch.setattr(config_setup, "config", lambda name: str(scratch_ini))
    config_setup.Config._instance = None
    config_setup.Config._initialized = False
    real_config = config_setup.Config()
    controller_de.config = real_config

    _make_genre_de(controller_de.get.session, "Rock")
    view = GenreView(controller_de)
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


def test_checked_bulk_delete_status_bar_reports_exclusion_count(qapp, controller_de, monkeypatch):
    _make_genre_de(controller_de.get.session, "Rock")
    _make_genre_de(controller_de.get.session, "Jazz")
    view = GenreView(controller_de)
    view.tree.selectAll()
    _patch_confirm_delete(view, monkeypatch, confirmed=True, checked=True)

    view.delete_selected_genres()

    assert view.status_bar.text() == "Deleted 2 genre(s), added 2 to Excluded Genres"


def test_unchecked_bulk_delete_status_bar_unchanged(qapp, controller_de, monkeypatch):
    _make_genre_de(controller_de.get.session, "Rock")
    _make_genre_de(controller_de.get.session, "Jazz")
    view = GenreView(controller_de)
    view.tree.selectAll()
    _patch_confirm_delete(view, monkeypatch, confirmed=True, checked=False)

    view.delete_selected_genres()

    assert view.status_bar.text() == "Deleted 2 genre(s)"
    assert "Excluded Genres" not in view.status_bar.text()


class _FakeDialogSelf:
    """Lets us call AliasManagementDialog._create_skipped_genres_tab as an
    unbound method against a lightweight stand-in, without constructing the
    full 9-tab dialog (which needs a much larger DB-backed controller_de).
    Borrows the real tab builder and add/remove/save machinery too, since the
    tab's line-edit wires returnPressed to those at construction time."""

    _create_exclusion_tab = AliasManagementDialog._create_exclusion_tab
    _create_skipped_genres_tab = AliasManagementDialog._create_skipped_genres_tab
    _add_excluded = AliasManagementDialog._add_excluded
    _remove_excluded = AliasManagementDialog._remove_excluded
    _save_exclusion = AliasManagementDialog._save_exclusion
    _add_excluded_genre = AliasManagementDialog._add_excluded_genre
    _remove_excluded_genre = AliasManagementDialog._remove_excluded_genre
    _save_excluded_genres = AliasManagementDialog._save_excluded_genres

    def __init__(self, controller_de):
        self.controller = controller_de


def test_skipped_genres_tab_reflects_names_added_via_delete(qapp, controller_de, monkeypatch):
    _make_genre_de(controller_de.get.session, "Rock")
    view = GenreView(controller_de)
    view.tree.selectAll()
    _patch_confirm_delete(view, monkeypatch, confirmed=True, checked=True)

    view.delete_selected_genres()

    fake_self = _FakeDialogSelf(controller_de)
    tab_page = AliasManagementDialog._create_skipped_genres_tab(fake_self)
    assert tab_page is not None  # keep it referenced so Qt doesn't GC the child QListWidget
    names = [
        fake_self.excluded_genres_list.item(i).text()
        for i in range(fake_self.excluded_genres_list.count())
    ]
    assert "Rock" in names


# ---- test_genre_view_sort_by_count.py ----------------------------------------
# Tests for docs/specs/genre_sort_by_count.md: the genre tree gains a
# native, sortable "Tracks" column showing playlist-style "own · recursive"
# track counts, loaded on a background GenreLoaderWorker thread. Each test
# below maps to one numbered acceptance criterion in that spec.
#
# tests/genre/conftest.py patches GenreLoaderWorker.start to run synchronously
# (no real QThread), so GenreView(controller_sbc) can be used exactly as before
# this feature -- see that file's docstring.
class _Controller_sbc:
    def __init__(self, session):
        self.get = GetFromDB(session)
        self.update = UpdateDB(session)
        self.delete = DeleteDB(session)


@pytest.fixture
def controller_sbc(session):
    return _Controller_sbc(session)


def _make_genre_sbc(session, name, parent=None):
    genre = Genre(genre_name=name, parent=parent)
    session.add(genre)
    session.commit()
    return genre


def _add_tracks_with_genre_sbc(session, genre, n):
    for i in range(n):
        track = Track(track_name=f"{genre.genre_name} track {i}")
        session.add(track)
        session.flush()
        session.add(TrackGenre(track_id=track.track_id, genre_id=genre.genre_id))
    session.commit()


def _top_level_names(tree):
    return [tree.topLevelItem(i).text(0) for i in range(tree.topLevelItemCount())]


def _count_execute_calls(session, monkeypatch):
    """Wrap session.execute to count calls made during the `with` block."""
    calls = {"n": 0}
    original = session.execute

    def counting_execute(*args, **kwargs):
        calls["n"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(session, "execute", counting_execute)
    return calls


def test_worker_computes_direct_and_recursive_counts(session, controller_sbc):
    rock = _make_genre_sbc(session, "Rock")
    punk = _make_genre_sbc(session, "Punk", parent=rock)
    alt = _make_genre_sbc(session, "Alt", parent=rock)
    jazz = _make_genre_sbc(session, "Jazz")
    _add_tracks_with_genre_sbc(session, rock, 1)
    _add_tracks_with_genre_sbc(session, punk, 2)
    _add_tracks_with_genre_sbc(session, jazz, 3)

    worker = GenreLoaderWorker(controller_sbc)
    result = {}
    worker.finished.connect(
        lambda genres, direct, recursive: result.update(
            genres=genres, direct=direct, recursive=recursive
        )
    )
    worker.run()

    assert result["direct"].get(rock.genre_id, 0) == 1
    assert result["direct"].get(punk.genre_id, 0) == 2
    assert result["direct"].get(alt.genre_id, 0) == 0
    assert result["direct"].get(jazz.genre_id, 0) == 3
    assert result["recursive"][rock.genre_id] == 3  # own(1) + punk(2) + alt(0)
    assert result["recursive"][punk.genre_id] == 2
    assert result["recursive"][alt.genre_id] == 0
    assert result["recursive"][jazz.genre_id] == 3
    assert {g.genre_id for g in result["genres"]} == {
        rock.genre_id,
        punk.genre_id,
        alt.genre_id,
        jazz.genre_id,
    }


def test_recursive_count_dedupes_track_tagged_at_multiple_levels(session, controller_sbc):
    rock = _make_genre_sbc(session, "Rock")
    punk = _make_genre_sbc(session, "Punk", parent=rock)

    track = Track(track_name="Overlap track")
    session.add(track)
    session.flush()
    # Tagged with both Rock (parent) and Punk (child) -- one physical track.
    session.add(TrackGenre(track_id=track.track_id, genre_id=rock.genre_id))
    session.add(TrackGenre(track_id=track.track_id, genre_id=punk.genre_id))
    session.commit()

    worker = GenreLoaderWorker(controller_sbc)
    result = {}
    worker.finished.connect(
        lambda genres, direct, recursive: result.update(direct=direct, recursive=recursive)
    )
    worker.run()

    assert result["direct"].get(rock.genre_id, 0) == 1
    assert result["direct"].get(punk.genre_id, 0) == 1
    # Naive summation would give 2 (1 own + 1 from Punk); the correct
    # recursive count is 1 unique track.
    assert result["recursive"][rock.genre_id] == 1
    assert result["recursive"][punk.genre_id] == 1


def test_worker_releases_session_and_emits_error_on_exception(session, controller_sbc, monkeypatch):
    _make_genre_sbc(session, "Rock")

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated query failure")

    monkeypatch.setattr(session, "execute", _boom)

    released = {"called": False}
    monkeypatch.setattr(
        GenreLoaderWorker,
        "_release_db_session",
        staticmethod(lambda: released.__setitem__("called", True)),
    )

    worker = GenreLoaderWorker(controller_sbc)
    outcomes = {}
    worker.finished.connect(lambda *a: outcomes.update(finished=a))
    worker.error.connect(lambda msg: outcomes.update(error=msg))
    worker.run()

    assert "finished" not in outcomes
    assert "simulated query failure" in outcomes["error"]
    assert released["called"] is True


def test_tree_has_genre_and_tracks_columns_with_sorting_enabled(qapp, controller_sbc):
    view = GenreView(controller_sbc)

    assert view.tree.columnCount() == 2
    assert view.tree.headerItem().text(0) == "Genre"
    assert view.tree.headerItem().text(1) == "Tracks"
    assert view.tree.isSortingEnabled() is True
    assert view.tree.isHeaderHidden() is False


def test_initial_load_is_alphabetical_ascending(session, qapp, controller_sbc):
    _make_genre_sbc(session, "zydeco")
    _make_genre_sbc(session, "Blues")
    _make_genre_sbc(session, "Jazz")

    view = GenreView(controller_sbc)

    assert _top_level_names(view.tree) == ["Blues", "Jazz", "zydeco"]


def test_sorting_by_tracks_column_orders_by_recursive_count(session, qapp, controller_sbc):
    rock = _make_genre_sbc(session, "Rock")
    punk = _make_genre_sbc(session, "Punk", parent=rock)
    _make_genre_sbc(session, "Alt", parent=rock)
    jazz = _make_genre_sbc(session, "Jazz")
    _add_tracks_with_genre_sbc(session, rock, 1)
    _add_tracks_with_genre_sbc(session, punk, 5)  # rock recursive = 6
    _add_tracks_with_genre_sbc(session, jazz, 2)

    view = GenreView(controller_sbc)

    view.tree.sortByColumn(1, Qt.SortOrder.DescendingOrder)
    assert _top_level_names(view.tree) == ["Rock", "Jazz"]  # 6 desc 2

    rock_item = view.tree.topLevelItem(0)
    assert [rock_item.child(i).text(0) for i in range(rock_item.childCount())] == [
        "Punk",
        "Alt",
    ]  # 5 desc 0

    view.tree.sortByColumn(1, Qt.SortOrder.AscendingOrder)
    assert _top_level_names(view.tree) == ["Jazz", "Rock"]  # 2 asc 6


def test_sorting_by_tracks_column_issues_no_database_queries(
    session, qapp, controller_sbc, monkeypatch
):
    rock = _make_genre_sbc(session, "Rock")
    jazz = _make_genre_sbc(session, "Jazz")
    _add_tracks_with_genre_sbc(session, rock, 1)
    _add_tracks_with_genre_sbc(session, jazz, 2)

    view = GenreView(controller_sbc)

    calls = _count_execute_calls(session, monkeypatch)
    view.tree.sortByColumn(1, Qt.SortOrder.DescendingOrder)
    assert calls["n"] == 0


def test_genre_without_subgenres_shows_plain_count(session, qapp, controller_sbc):
    jazz = _make_genre_sbc(session, "Jazz")
    _add_tracks_with_genre_sbc(session, jazz, 3)

    view = GenreView(controller_sbc)

    item = view.tree.topLevelItem(0)
    assert item.text(1) == "3"


def test_genre_with_subgenres_shows_own_and_recursive_when_they_differ(
    session, qapp, controller_sbc
):
    rock = _make_genre_sbc(session, "Rock")
    punk = _make_genre_sbc(session, "Punk", parent=rock)
    _add_tracks_with_genre_sbc(session, rock, 12)
    _add_tracks_with_genre_sbc(session, punk, 30)

    view = GenreView(controller_sbc)

    rock_item = view.tree.topLevelItem(0)
    assert rock_item.text(1) == "12 · 42"


def test_genre_with_zero_track_subgenres_collapses_to_single_number(session, qapp, controller_sbc):
    rock = _make_genre_sbc(session, "Rock")
    _make_genre_sbc(session, "Silent Subgenre", parent=rock)  # no tracks
    _add_tracks_with_genre_sbc(session, rock, 7)

    view = GenreView(controller_sbc)

    rock_item = view.tree.topLevelItem(0)
    assert rock_item.text(1) == "7"  # recursive == own, no "·"


def test_tracks_column_is_right_aligned_italic_with_tooltip(session, qapp, controller_sbc):
    rock = _make_genre_sbc(session, "Rock")
    punk = _make_genre_sbc(session, "Punk", parent=rock)
    _add_tracks_with_genre_sbc(session, rock, 1)
    _add_tracks_with_genre_sbc(session, punk, 2)
    jazz = _make_genre_sbc(session, "Jazz")
    _add_tracks_with_genre_sbc(session, jazz, 5)

    view = GenreView(controller_sbc)

    names = _top_level_names(view.tree)
    rock_item = view.tree.topLevelItem(names.index("Rock"))
    jazz_item = view.tree.topLevelItem(names.index("Jazz"))

    assert rock_item.textAlignment(1) == (Qt.AlignRight | Qt.AlignVCenter)
    assert rock_item.font(1).italic() is True
    assert rock_item.toolTip(1) != ""  # "1 · 3" has a "·" -> tooltip set

    assert jazz_item.text(1) == "5"
    assert jazz_item.toolTip(1) == ""  # no "·" -> no tooltip


def test_tracks_column_sorts_numerically_not_lexicographically(session, qapp, controller_sbc):
    nine = _make_genre_sbc(session, "Nine")
    twelve = _make_genre_sbc(session, "Twelve")
    _add_tracks_with_genre_sbc(session, nine, 9)
    _add_tracks_with_genre_sbc(session, twelve, 12)

    view = GenreView(controller_sbc)
    view.tree.sortByColumn(1, Qt.SortOrder.AscendingOrder)

    # Lexicographic string sort would put "12" before "9"; numeric sort
    # must put 9 before 12.
    assert _top_level_names(view.tree) == ["Nine", "Twelve"]


def test_flat_view_sorted_by_tracks_column_is_unnested_and_ordered(session, qapp, controller_sbc):
    rock = _make_genre_sbc(session, "Rock")
    punk = _make_genre_sbc(session, "Punk", parent=rock)
    _add_tracks_with_genre_sbc(session, rock, 1)
    _add_tracks_with_genre_sbc(session, punk, 5)
    jazz = _make_genre_sbc(session, "Jazz")
    _add_tracks_with_genre_sbc(session, jazz, 2)

    view = GenreView(controller_sbc)
    view.flat_view_button.setChecked(True)
    view.toggle_flat_view()
    view.tree.sortByColumn(1, Qt.SortOrder.DescendingOrder)

    assert view.tree.topLevelItemCount() == 3
    for i in range(view.tree.topLevelItemCount()):
        assert view.tree.topLevelItem(i).childCount() == 0
    # Punk(5) > Jazz(2) > Rock(1, recursive incl. Punk = 6... but Punk is
    # itself a flat top-level row here, so Rock's own count in Flat View
    # is still shown as its recursive total, 6)
    assert _top_level_names(view.tree) == ["Rock", "Punk", "Jazz"]


def test_sort_state_preserved_across_flat_view_toggle_and_reload(session, qapp, controller_sbc):
    rock = _make_genre_sbc(session, "Rock")
    _add_tracks_with_genre_sbc(session, rock, 1)
    jazz = _make_genre_sbc(session, "Jazz")
    _add_tracks_with_genre_sbc(session, jazz, 9)

    view = GenreView(controller_sbc)
    view.tree.sortByColumn(1, Qt.SortOrder.DescendingOrder)
    assert _top_level_names(view.tree) == ["Jazz", "Rock"]

    view.flat_view_button.setChecked(True)
    view.toggle_flat_view()
    assert _top_level_names(view.tree) == ["Jazz", "Rock"]  # still count-desc

    view.flat_view_button.setChecked(False)
    view.toggle_flat_view()
    assert _top_level_names(view.tree) == ["Jazz", "Rock"]  # still count-desc

    # Reload (e.g. after an edit) also preserves it, since load_genres()
    # re-triggers _on_genres_loaded -> _rebuild_tree without resetting the
    # header's sort indicator.
    view.load_genres()
    assert _top_level_names(view.tree) == ["Jazz", "Rock"]


def test_selection_and_expansion_preserved_across_resort(session, qapp, controller_sbc):
    rock = _make_genre_sbc(session, "Rock")
    punk = _make_genre_sbc(session, "Punk", parent=rock)
    _add_tracks_with_genre_sbc(session, rock, 1)
    _add_tracks_with_genre_sbc(session, punk, 2)

    view = GenreView(controller_sbc)
    rock_item = view.tree.topLevelItem(0)
    rock_item.setExpanded(True)
    view.tree.setCurrentItem(rock_item)

    view.tree.sortByColumn(1, Qt.SortOrder.DescendingOrder)

    current = view.tree.currentItem()
    assert current is not None
    assert current.data(0, Qt.UserRole) == rock.genre_id

    view._rebuild_tree()
    names = _top_level_names(view.tree)
    rebuilt_rock = view.tree.topLevelItem(names.index("Rock"))
    assert rebuilt_rock.isExpanded() is True


def test_rename_via_name_column_updates_the_genre(session, qapp, controller_sbc):
    rock = _make_genre_sbc(session, "Rock")

    view = GenreView(controller_sbc)
    item = view.tree.topLevelItem(0)
    item.setText(0, "Classic Rock")
    view.on_item_edited(item, 0)

    session.expire(rock)
    assert rock.genre_name == "Classic Rock"


def test_editing_tracks_column_is_a_no_op(session, qapp, controller_sbc):
    rock = _make_genre_sbc(session, "Rock")
    _add_tracks_with_genre_sbc(session, rock, 4)

    view = GenreView(controller_sbc)
    item = view.tree.topLevelItem(0)
    original_count_text = item.text(1)

    item.setText(1, "garbage")
    view.on_item_edited(item, 1)

    session.expire(rock)
    assert rock.genre_name == "Rock"  # untouched
    assert item.text(0) == "Rock"
    item.setText(1, original_count_text)  # cosmetic cleanup, not asserted


def test_loading_state_shown_and_duplicate_load_is_guarded(session, qapp, controller_sbc):
    _make_genre_sbc(session, "Rock")

    view = GenreView(controller_sbc)
    # After the (synchronous, per conftest) worker finishes, the loading
    # state must be cleared and the tree usable again.
    assert view.loading_label.isVisible() is False
    assert view.tree.isEnabled() is True

    # Simulate "still running" and confirm a second load_genres() call
    # doesn't spawn a second worker while one is (nominally) in flight.
    class _FakeRunningThread:
        def isRunning(self):
            return True

    view._loader_thread = _FakeRunningThread()
    previous_thread = view._loader_thread
    view.load_genres()
    assert view._loader_thread is previous_thread  # no new worker started


def test_flat_view_toggle_uses_cached_counts_no_database_calls(
    session, qapp, controller_sbc, monkeypatch
):
    rock = _make_genre_sbc(session, "Rock")
    punk = _make_genre_sbc(session, "Punk", parent=rock)
    _add_tracks_with_genre_sbc(session, rock, 1)
    _add_tracks_with_genre_sbc(session, punk, 2)

    view = GenreView(controller_sbc)

    calls = _count_execute_calls(session, monkeypatch)
    view.flat_view_button.setChecked(True)
    view.toggle_flat_view()
    view.flat_view_button.setChecked(False)
    view.toggle_flat_view()
    assert calls["n"] == 0


def test_tooltip_build_does_not_touch_orm_parent_relationship_when_detached(
    session, qapp, controller_sbc
):
    rock = _make_genre_sbc(session, "Rock")
    punk = _make_genre_sbc(session, "Punk", parent=rock)
    _add_tracks_with_genre_sbc(session, rock, 1)
    _add_tracks_with_genre_sbc(session, punk, 2)

    view = GenreView(controller_sbc)  # initial load, still session-bound: fine

    # Now simulate what a real background-thread load hands back: genres
    # whose session has already been released.
    genres = list(session.query(Genre))
    for genre in genres:
        session.expunge(genre)

    direct = {rock.genre_id: 1, punk.genre_id: 2}
    recursive = {rock.genre_id: 3, punk.genre_id: 2}

    # Must not raise sqlalchemy.orm.exc.DetachedInstanceError.
    view._on_genres_loaded(genres, direct, recursive)

    names = _top_level_names(view.tree)
    rock_item = view.tree.topLevelItem(names.index("Rock"))
    punk_item = rock_item.child(0)
    assert punk_item.text(0) == "Punk"
    assert "Parent: Rock" in punk_item.toolTip(0)
