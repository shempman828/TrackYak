"""Regression test: library scan actions live in the Tools menu, not File."""

from PySide6.QtWidgets import QMainWindow

from src.core.menu_bar import MenuBar


class _Window(MenuBar, QMainWindow):
    def _reset_ui_layout(self):
        pass


def _menu_labels(window, title):
    for action in window.menuBar().actions():
        if action.text() == title:
            return [a.text() for a in action.menu().actions() if not a.isSeparator()]
    raise AssertionError(f"menu {title!r} not found")


def test_duplicate_and_missing_track_finders_are_in_tools_menu(qapp):
    window = _Window()
    window._init_menu_bar()

    tools = _menu_labels(window, "Tools")
    file_ = _menu_labels(window, "File")

    for label in ("Find Duplicate Tracks", "Find Missing Tracks"):
        assert label in tools
        assert label not in file_
