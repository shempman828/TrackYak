"""
track_view_toolbar.py — toolbar construction and column-search picker for TrackView.
"""

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QMenu, QToolButton

from src.core.logger_config import logger
from src.track.track_view_filter import SEARCH_ALL


class TrackViewToolbarMixin:
    """Builds the search bar, column-search picker, and action drop-downs."""

    def _build_toolbar(self):
        """Build a compact single-row toolbar replacing the old button rows."""
        toolbar_row = QHBoxLayout()
        toolbar_row.setSpacing(4)

        # Search field with built-in clear (✕) button
        self.search_bar = QLineEdit(self)
        self.search_bar.setPlaceholderText("Search tracks…")
        self.search_bar.setClearButtonEnabled(True)

        self.search_bar.returnPressed.connect(self._apply_search_filter)
        self.search_bar.textChanged.connect(self._on_search_text_changed)

        # Column selector for targeted search — shown as a drop-down button with
        # category submenus (populated after _initialize_columns() via _populate_search_combo).
        self._search_field_name: str = SEARCH_ALL  # tracks the active field

        self.search_column_btn = QToolButton(self)
        self.search_column_btn.setText("All Columns \u25be")
        self.search_column_btn.setToolTip("Choose which column to search")
        self.search_column_btn.setPopupMode(QToolButton.InstantPopup)

        self._search_column_menu = QMenu(self.search_column_btn)
        self.search_column_btn.setMenu(self._search_column_menu)
        # Menu is filled once columns are known — see _populate_search_combo()

        toolbar_row.addWidget(self.search_bar, stretch=1)
        toolbar_row.addWidget(self.search_column_btn)

        # ── "⋮ Queue" drop-down button ────────────────────────────────────
        queue_btn = QToolButton(self)
        queue_btn.setText("＋ Queue")
        queue_btn.setToolTip("Add tracks to the playback queue")
        queue_btn.setPopupMode(QToolButton.InstantPopup)

        queue_menu = QMenu(queue_btn)
        queue_menu.addAction("Add Filtered to Queue", self._add_filtered_to_queue)
        queue_menu.addAction(
            "Shuffle Filtered to Queue",
            lambda: self._add_filtered_to_queue(shuffle=True),
        )
        queue_menu.addSeparator()
        queue_menu.addAction("Shuffle Entire Library", self._add_all_to_queue)
        queue_btn.setMenu(queue_menu)

        # ── "⋮ View" drop-down button ─────────────────────────────────────
        view_btn = QToolButton(self)
        view_btn.setText("⚙ View")
        view_btn.setToolTip("Column visibility, order, and other options")
        view_btn.setPopupMode(QToolButton.InstantPopup)

        view_menu = QMenu(view_btn)
        view_menu.addAction("Toggle Columns", self.show_column_menu)
        view_menu.addAction(
            "Column Order && Visibility", self.show_column_customization
        )
        view_menu.addSeparator()
        view_menu.addAction("Refresh Library", self._force_reload)
        view_btn.setMenu(view_menu)

        toolbar_row.addWidget(queue_btn)
        toolbar_row.addWidget(view_btn)

        # Status label sits right-aligned after the buttons
        self.status_label = QLabel("")
        self.status_label.setProperty("textRole", "muted")
        toolbar_row.addWidget(self.status_label)

        self.layout.addLayout(toolbar_row)

    def _populate_search_combo(self):
        """
        Build the column-search drop-down menu with category submenus.

        Layout:
            [All Columns]          ← top-level, always present
            ─────────────
            Basic Info  ►          ← submenu per category
            Technical   ►
            …
        """
        menu = self._search_column_menu
        menu.clear()

        # "All Columns" sits at the top level
        all_action = QAction("All Columns", menu)
        all_action.setData(SEARCH_ALL)
        all_action.triggered.connect(self._on_search_column_selected)
        menu.addAction(all_action)
        menu.addSeparator()

        # Group columns by their TrackField category
        category_groups: dict[str, list] = {}
        for field_name, friendly in self.columns.items():
            field_config = self.track_fields.get(field_name)
            cat = (field_config.category or "Other") if field_config else "Other"
            category_groups.setdefault(cat, []).append((field_name, friendly))

        for cat, fields in sorted(category_groups.items()):
            submenu = QMenu(cat, menu)
            for field_name, friendly in fields:
                act = QAction(friendly, submenu)
                act.setData(field_name)
                act.triggered.connect(self._on_search_column_selected)
                submenu.addAction(act)
            menu.addMenu(submenu)

    def _on_search_column_selected(self):
        """Called when the user picks a column (or 'All Columns') from the search menu."""
        action = self.sender()
        if not action:
            return
        self._search_field_name = action.data() or SEARCH_ALL
        label = (
            action.text() if self._search_field_name != SEARCH_ALL else "All Columns"
        )
        self.search_column_btn.setText(f"{label} \u25be")
        logger.debug(f"Search column changed to '{label}'")
        # Re-run the search immediately with the new column choice
        self._apply_search_filter()
