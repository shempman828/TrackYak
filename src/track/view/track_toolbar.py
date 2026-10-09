"""
track_toolbar.py — widgets shared by TrackView and BaseTrackView:

    TrackToolbar   search field + column-scope picker, an action slot, and a
                   second line with the list summary and the scope chip
    SelectionBar   a strip under the table, shown for 2+ selected rows, with
                   the selection's size/length and its main actions

Both are plain widgets; TrackViewToolbarMixin (track_view_toolbar.py) wires
them to a host view.
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QLineEdit, QMenu, QPushButton, QToolButton, QVBoxLayout, QWidget


class TrackToolbar(QWidget):
    """
    ┌ 🔍 Search tracks…            [All Columns ▾] ┐   <actions…>
    12,345 tracks · 812 h   [Genre: jazz  ✕]
    """

    scope_cleared = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("TrackToolbar")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(4)

        top = QHBoxLayout()
        top.setSpacing(6)

        # Search field with built-in clear (✕) button
        self.search_bar = QLineEdit(self)
        self.search_bar.setObjectName("searchBarField")
        self.search_bar.setPlaceholderText("Search tracks…")
        self.search_bar.setClearButtonEnabled(True)

        # Column selector for targeted search -- a drop-down button with
        # category submenus, filled once the host knows its columns.
        self.search_column_btn = QToolButton(self)
        self.search_column_btn.setObjectName("searchColumnBtn")
        self.search_column_btn.setText("All Columns ▾")
        self.search_column_btn.setToolTip("Choose which column to search")
        self.search_column_btn.setPopupMode(QToolButton.InstantPopup)
        self.search_column_menu = QMenu(self.search_column_btn)
        self.search_column_btn.setMenu(self.search_column_menu)

        # Fuse the search field and its column-scope picker into a single
        # visual group (zero spacing, flush borders via QSS).
        search_group = QHBoxLayout()
        search_group.setSpacing(0)
        search_group.addWidget(self.search_bar, stretch=1)
        search_group.addWidget(self.search_column_btn)
        top.addLayout(search_group, stretch=1)
        top.addSpacing(10)

        self._actions = QHBoxLayout()
        self._actions.setSpacing(4)
        top.addLayout(self._actions)
        outer.addLayout(top)

        bottom = QHBoxLayout()
        bottom.setContentsMargins(2, 0, 2, 0)
        bottom.setSpacing(8)

        self.status_label = QLabel("")
        self.status_label.setObjectName("TrackListSummary")
        self.status_label.setProperty("textRole", "muted")
        bottom.addWidget(self.status_label)

        # Shown only while the search is scoped to one column; ✕ widens it
        # back to all columns.
        self.scope_chip = QPushButton()
        self.scope_chip.setObjectName("SearchScopeChip")
        self.scope_chip.setCursor(Qt.PointingHandCursor)
        self.scope_chip.setToolTip("Search all columns again")
        self.scope_chip.clicked.connect(self.scope_cleared)
        self.scope_chip.hide()
        bottom.addWidget(self.scope_chip)
        bottom.addStretch()
        outer.addLayout(bottom)

    def add_action(self, widget: QWidget) -> QWidget:
        """Append a button (or other widget) to the right of the search field."""
        self._actions.addWidget(widget)
        return widget

    def set_scope(self, label: str | None, query: str = "") -> None:
        """Show the scope chip for a single-column search (`label`), or hide
        it for an all-columns search (`label` None)."""
        if not label:
            self.scope_chip.hide()
            return
        self.scope_chip.setText(f"{label}: {query}   ✕" if query else f"{label}   ✕")
        self.scope_chip.show()


def make_primary_tool_button(text: str, tooltip: str, slot) -> QPushButton:
    """A compact toolbar button for the list's primary playback actions."""
    btn = QPushButton(text)
    btn.setProperty("toolbarPrimary", True)
    btn.setToolTip(tooltip)
    btn.setCursor(Qt.PointingHandCursor)
    btn.clicked.connect(lambda _checked=False: slot())
    return btn


class SelectionBar(QFrame):
    """'3 selected · 24:41   ▶ Play next  + Queue  ✎ Edit  🗑 Delete   ✕'"""

    def __init__(self, on_play_next, on_queue, on_edit, on_delete, on_clear, parent=None):
        super().__init__(parent)
        self.setObjectName("SelectionBar")
        row = QHBoxLayout(self)
        row.setContentsMargins(12, 6, 6, 6)
        row.setSpacing(6)

        self.summary = QLabel()
        self.summary.setObjectName("SelectionBarSummary")
        row.addWidget(self.summary)
        row.addStretch()

        for text, tip, slot, danger in (
            ("▶  Play Next", "Play the selected tracks after the current one", on_play_next, False),
            ("＋  Queue", "Add the selected tracks to the end of the queue", on_queue, False),  # noqa: RUF001
            ("✎  Edit", "Edit the selected tracks together", on_edit, False),
            ("🗑  Delete", "Delete the selected tracks", on_delete, True),
        ):
            btn = QPushButton(text)
            btn.setProperty("selectionAction", True)
            if danger:
                btn.setProperty("danger", True)
            btn.setToolTip(tip)
            btn.clicked.connect(lambda _checked=False, fn=slot: fn())
            row.addWidget(btn)

        close = QToolButton()
        close.setObjectName("SelectionBarClose")
        close.setText("✕")
        close.setToolTip("Clear selection")
        close.clicked.connect(on_clear)
        row.addWidget(close)
        self.hide()
