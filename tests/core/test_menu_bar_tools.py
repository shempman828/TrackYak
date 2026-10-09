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


def test_hide_timer_keeps_bar_while_a_menu_is_open(qapp, monkeypatch):
    window = _Window()
    window._init_menu_bar()
    monkeypatch.setattr(window, "_get_display_settings_auto_hide", lambda: True)
    monkeypatch.setattr(window, "_cursor_in_menu_bar_region", lambda: False)
    window.menuBar().show()
    monkeypatch.setattr(window.menuBar(), "activeAction", lambda: window.menuBar().actions()[0])

    window._hide_menu_bar_if_mouse_gone()

    assert not window.menuBar().isHidden()


def test_singleton_dialog_recreated_after_qt_deletes_it(qapp):
    from PySide6.QtWidgets import QDialog
    from shiboken6 import delete

    window = _Window()
    built = []

    def factory():
        dialog = QDialog(window)
        built.append(dialog)
        return dialog

    first = window._show_singleton_dialog("some_dialog", factory)
    assert window._show_singleton_dialog("some_dialog", factory) is first
    delete(first)
    second = window._show_singleton_dialog("some_dialog", factory)

    assert len(built) == 2
    assert window.some_dialog is second
    second.close()


def test_miniplayer_shortcut_toggles_closed_when_open(qapp):
    window = _Window()

    class _Mini:
        closed = False

        def isVisible(self):
            return True

        def close(self):
            self.closed = True

        def deleteLater(self):
            pass

    mini = _Mini()
    window._mini_player = mini

    window.open_miniplayer()

    assert mini.closed
    assert window._mini_player is None


def test_about_license_link_points_at_project_license_file():
    from src.core.startup_dialog import LICENSE_FILE
    from src.foundation.asset_paths import BASE_DIR

    assert LICENSE_FILE == BASE_DIR / "license.md"
