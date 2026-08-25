"""Tests for docs/specs/genre_hierarchy_export.md: the genre tree's
right-click context menu gains an "Export Hierarchy..." action that writes
the full genre hierarchy as a box-drawing tree to a .txt or .md file,
independent of the current Flat View toggle or search filter, ordered by
the tree's active sort column/direction. Each test maps to one numbered
acceptance criterion in that spec (criteria 1-3 are covered directly
against render_hierarchy_as_text in tests/common/test_hierarchy_tree_render.py).

tests/genre/conftest.py patches GenreLoaderWorker.start to run synchronously,
so GenreView(controller) can be used exactly as before this feature.
"""

from unittest.mock import patch

import pytest
from PySide6.QtCore import Qt
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.db_helpers.delete import DeleteDB
from src.db.db_helpers.get import GetFromDB
from src.db.db_helpers.update import UpdateDB
from src.db.db_tables.associations import TrackGenre
from src.db.db_tables.base import Base
from src.db.db_tables.genre import Genre
from src.db.db_tables.track import Track
from src.genre.genre_view import GenreView


class _Controller:
    def __init__(self, session):
        self.get = GetFromDB(session)
        self.update = UpdateDB(session)
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


def _make_genre(session, name, parent=None):
    genre = Genre(genre_name=name, parent=parent)
    session.add(genre)
    session.commit()
    return genre


def _add_tracks_with_genre(session, genre, n):
    for i in range(n):
        track = Track(track_name=f"{genre.genre_name} track {i}")
        session.add(track)
        session.flush()
        session.add(TrackGenre(track_id=track.track_id, genre_id=genre.genre_id))
    session.commit()


# ---------------------------------------------------------------------------
# AC4 -- context menu includes "Export Hierarchy..." both on an item and on
# empty tree space
# ---------------------------------------------------------------------------


def test_context_menu_has_export_action_on_item_and_empty_space(session, qapp, controller):
    rock = _make_genre(session, "Rock")
    view = GenreView(controller)
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


# ---------------------------------------------------------------------------
# AC5 -- no genres loaded -> status message, no file dialog
# ---------------------------------------------------------------------------


def test_export_with_no_genres_shows_status_and_skips_dialog(qapp, controller):
    view = GenreView(controller)
    assert view._all_genres == []

    with patch("src.genre.genre_view.QFileDialog.getSaveFileName") as mock_dialog:
        with patch("src.genre.genre_view.show_status_message") as mock_status:
            view.export_hierarchy()

    mock_dialog.assert_not_called()
    mock_status.assert_called_once()
    assert "No genres available" in mock_status.call_args.args[1]


# ---------------------------------------------------------------------------
# AC6 -- file dialog opened with dual filter and default filename
# ---------------------------------------------------------------------------


def test_export_opens_save_dialog_with_txt_md_filter(session, qapp, controller, tmp_path):
    _make_genre(session, "Rock")
    view = GenreView(controller)

    with patch(
        "src.genre.genre_view.QFileDialog.getSaveFileName", return_value=("", "")
    ) as mock_dialog:
        view.export_hierarchy()

    args, kwargs = mock_dialog.call_args
    assert args[2] == "genre_hierarchy.txt"
    assert "*.txt" in args[3]
    assert "*.md" in args[3]


# ---------------------------------------------------------------------------
# AC7 / AC8 -- .txt writes plain text, .md wraps it in a fenced code block
# ---------------------------------------------------------------------------


def test_txt_export_writes_plain_box_drawing_text(session, qapp, controller, tmp_path):
    rock = _make_genre(session, "Rock")
    _make_genre(session, "Punk", parent=rock)
    view = GenreView(controller)

    out_path = tmp_path / "hierarchy.txt"
    with patch(
        "src.genre.genre_view.QFileDialog.getSaveFileName",
        return_value=(str(out_path), "Text Files (*.txt)"),
    ):
        view.export_hierarchy()

    content = out_path.read_text(encoding="utf-8")
    assert content == "Rock\n└── Punk"


def test_md_export_wraps_content_in_code_fence(session, qapp, controller, tmp_path):
    rock = _make_genre(session, "Rock")
    _make_genre(session, "Punk", parent=rock)
    view = GenreView(controller)

    out_path = tmp_path / "hierarchy.md"
    with patch(
        "src.genre.genre_view.QFileDialog.getSaveFileName",
        return_value=(str(out_path), "Markdown Files (*.md)"),
    ):
        view.export_hierarchy()

    content = out_path.read_text(encoding="utf-8")
    assert content == "```\nRock\n└── Punk\n```"


# ---------------------------------------------------------------------------
# AC9 -- export is identical regardless of Flat View toggle state
# ---------------------------------------------------------------------------


def test_export_identical_regardless_of_flat_view(session, qapp, controller, tmp_path):
    rock = _make_genre(session, "Rock")
    _make_genre(session, "Punk", parent=rock)
    view = GenreView(controller)

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


# ---------------------------------------------------------------------------
# AC10 -- export is identical regardless of an active search filter
# ---------------------------------------------------------------------------


def test_export_identical_regardless_of_search_filter(session, qapp, controller, tmp_path):
    rock = _make_genre(session, "Rock")
    _make_genre(session, "Punk", parent=rock)
    _make_genre(session, "Jazz")
    view = GenreView(controller)

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


# ---------------------------------------------------------------------------
# AC11 -- sibling order follows the tree's active sort column/direction
# ---------------------------------------------------------------------------


def test_export_follows_name_ascending_sort(session, qapp, controller, tmp_path):
    _make_genre(session, "Zydeco")
    _make_genre(session, "Alpha")
    view = GenreView(controller)
    view.tree.sortByColumn(0, Qt.SortOrder.AscendingOrder)

    out_path = tmp_path / "out.txt"
    with patch(
        "src.genre.genre_view.QFileDialog.getSaveFileName",
        return_value=(str(out_path), "Text Files (*.txt)"),
    ):
        view.export_hierarchy()

    assert out_path.read_text(encoding="utf-8") == "Alpha\nZydeco"


def test_export_follows_tracks_descending_sort_with_alphabetical_tiebreak(
    session, qapp, controller, tmp_path
):
    rock = _make_genre(session, "Rock")
    punk = _make_genre(session, "Punk", parent=rock)
    jazz = _make_genre(session, "Jazz")
    _add_tracks_with_genre(session, rock, 1)
    _add_tracks_with_genre(session, punk, 5)  # rock recursive = 6
    _add_tracks_with_genre(session, jazz, 2)
    alt = _make_genre(session, "Alt")
    _add_tracks_with_genre(session, alt, 2)  # ties with Jazz at 2

    view = GenreView(controller)
    view.tree.sortByColumn(1, Qt.SortOrder.DescendingOrder)

    out_path = tmp_path / "out.txt"
    with patch(
        "src.genre.genre_view.QFileDialog.getSaveFileName",
        return_value=(str(out_path), "Text Files (*.txt)"),
    ):
        view.export_hierarchy()

    # Rock (6) first, then Alt/Jazz tied at 2 (alphabetical tie-break).
    assert out_path.read_text(encoding="utf-8") == "Rock\n└── Punk\nAlt\nJazz"


# ---------------------------------------------------------------------------
# AC12 -- success status message reports path and genre count
# ---------------------------------------------------------------------------


def test_export_success_shows_status_message_with_count_and_path(
    session, qapp, controller, tmp_path
):
    _make_genre(session, "Rock")
    _make_genre(session, "Jazz")
    view = GenreView(controller)

    out_path = tmp_path / "out.txt"
    with patch(
        "src.genre.genre_view.QFileDialog.getSaveFileName",
        return_value=(str(out_path), "Text Files (*.txt)"),
    ):
        with patch("src.genre.genre_view.show_status_message") as mock_status:
            view.export_hierarchy()

    message = mock_status.call_args.args[1]
    assert "2" in message
    assert str(out_path) in message


# ---------------------------------------------------------------------------
# AC13 -- write failure shows a critical error dialog, not a success message
# ---------------------------------------------------------------------------


def test_export_write_failure_shows_error_dialog(session, qapp, controller, tmp_path):
    _make_genre(session, "Rock")
    view = GenreView(controller)

    out_path = tmp_path / "out.txt"
    with patch(
        "src.genre.genre_view.QFileDialog.getSaveFileName",
        return_value=(str(out_path), "Text Files (*.txt)"),
    ):
        with patch("builtins.open", side_effect=OSError("permission denied")):
            with patch("src.genre.genre_view.QMessageBox.critical") as mock_critical:
                with patch("src.genre.genre_view.show_status_message") as mock_status:
                    view.export_hierarchy()

    mock_critical.assert_called_once()
    mock_status.assert_not_called()


# ---------------------------------------------------------------------------
# AC14 -- only genre names appear in the export; no IDs/counts/descriptions
# ---------------------------------------------------------------------------


def test_export_contains_only_genre_names(session, qapp, controller, tmp_path):
    rock = _make_genre(session, "Rock")
    rock.description = "A genre about rocks, apparently"
    session.commit()
    _add_tracks_with_genre(session, rock, 42)
    view = GenreView(controller)

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
