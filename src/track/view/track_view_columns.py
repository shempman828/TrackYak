"""Column setup, visibility, ordering and saved state for the track views."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QHeaderView, QMenu, QTableView

from src.foundation.config_setup import app_config
from src.foundation.logger_config import logger
from src.track.view.track_columns import ColumnCustomizationDialog

# Hidden by default in addition to the "Technical" category.
_HIDDEN_BY_DEFAULT = frozenset({"file_size", "bit_rate", "sample_rate", "track_id", "track_file_path"})


class TrackViewColumnsMixin:
    """Column model setup, header behavior, visibility, and persisted state."""

    def default_hidden_columns(self) -> set[str]:
        """Return the field names that are hidden by default."""
        hidden = set()
        for field_name in self.columns:
            field_config = self.track_fields.get(field_name)
            if (field_config and field_config.category == "Technical") or field_name in _HIDDEN_BY_DEFAULT:
                hidden.add(field_name)
        return hidden

    def _initialize_columns(self):
        """Build self.columns from TRACK_FIELDS and fill the search-scope menu."""
        self.columns = {}
        for field_name, field_config in self.track_fields.items():
            if field_config.friendly:
                self.columns[field_name] = field_config.friendly
        # Now that columns are known, populate the search combo
        self._populate_search_combo()

    def _setup_table(self):
        """Configure the model columns, header, selection and drag behavior."""
        self.model.setColumnCount(len(self.columns))
        self.model.setHorizontalHeaderLabels(list(self.columns.values()))

        self.table.setSortingEnabled(False)  # We handle sorting ourselves

        # Interactive resizing so users can drag column edges
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setStretchLastSection(False)
        # Give a sensible default width; saved widths will override this
        header.setDefaultSectionSize(120)
        header.setSortIndicatorShown(True)

        # Track current sort state so repeated clicks toggle direction
        self._sort_column_index: int = -1
        self._sort_ascending: bool = True
        header.sectionClicked.connect(self._on_header_clicked)

        self.table.setSelectionBehavior(QTableView.SelectRows)
        self.table.setSelectionMode(QTableView.ExtendedSelection)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableView.NoEditTriggers)

        self.table.setDragEnabled(True)
        self.table.setDragDropMode(QTableView.DragOnly)
        self.table.setDefaultDropAction(Qt.CopyAction)
        self.table.startDrag = self.startDrag

        self._set_initial_column_visibility()

    def _set_initial_column_visibility(self):
        """Hide the default-hidden columns."""
        hidden = self.default_hidden_columns()
        for i, field_name in enumerate(self.columns):
            if field_name in hidden:
                self.table.setColumnHidden(i, True)

    # =========================================================================
    #  Column state persistence
    # =========================================================================

    def get_column_state(self) -> dict:
        """Return current visible columns and their visual order."""
        col_keys = list(self.columns.keys())
        header = self.table.horizontalHeader()
        visible = [k for i, k in enumerate(col_keys) if not self.table.isColumnHidden(i)]
        order = [col_keys[header.logicalIndex(v)] for v in range(header.count())]
        return {"visible": visible, "order": order}

    def load_column_state(self):
        """Apply the saved column visibility, order and widths."""
        try:
            visible = app_config.get_track_view_visible_columns()
            order = app_config.get_track_view_column_order()
            widths = app_config.get_track_view_column_widths()
            col_keys = list(self.columns.keys())

            if visible:
                for i, key in enumerate(col_keys):
                    self.table.setColumnHidden(i, key not in visible)

            if order:
                header = self.table.horizontalHeader()
                for target_visual, key in enumerate(order):
                    if key in col_keys:
                        current_visual = header.visualIndex(col_keys.index(key))
                        if current_visual != target_visual:
                            header.moveSection(current_visual, target_visual)

            if widths:
                for i, w in enumerate(widths):
                    if i < len(col_keys) and w > 0:
                        self.table.setColumnWidth(i, w)
        except (ValueError, RuntimeError) as e:
            logger.error(f"Error loading column state: {e}")

    def save_column_state(self):
        """Save the column visibility, order and widths."""
        try:
            col_keys = list(self.columns.keys())
            header = self.table.horizontalHeader()
            visible = [k for i, k in enumerate(col_keys) if not self.table.isColumnHidden(i)]
            order = [col_keys[header.logicalIndex(v)] for v in range(header.count())]
            widths = [self.table.columnWidth(i) for i in range(len(col_keys))]

            app_config.set_track_view_visible_columns(visible)
            app_config.set_track_view_column_order(order)
            app_config.set_track_view_column_widths(widths)
        except (RuntimeError, IndexError) as e:
            logger.error(f"Error saving column state: {e}")

    def show_column_menu(self):
        """Toggle Columns menu, grouped by FieldSpec category into submenus."""
        menu = QMenu(self)

        # Build a dict of  category → list of (index, field_name, label)
        category_groups: dict[str, list] = {}
        for i, (key, label) in enumerate(self.columns.items()):
            field_config = self.track_fields.get(key)
            cat = (field_config.category or "Other") if field_config else "Other"
            category_groups.setdefault(cat, []).append((i, key, label))

        for cat, fields in sorted(category_groups.items()):
            submenu = QMenu(cat, menu)
            for i, _key, label in fields:
                action = QAction(label, submenu)
                action.setCheckable(True)
                action.setChecked(not self.table.isColumnHidden(i))
                action.setData(i)
                action.triggered.connect(self._toggle_column)
                submenu.addAction(action)
            menu.addMenu(submenu)

        menu.exec_(self.cursor().pos())

    def _toggle_column(self):
        """Show or hide the column of the triggered menu action, keeping at least one visible."""
        action = self.sender()
        if action:
            i = action.data()
            visible = [c for c in range(len(self.columns)) if not self.table.isColumnHidden(c)]
            if not action.isChecked() and visible == [i]:
                action.setChecked(True)  # hiding the last column would leave no header to right-click
                return
            self.table.setColumnHidden(i, not action.isChecked())
            self.save_column_state()

    def show_column_customization(self):
        """Open the column order and visibility dialog."""
        dialog = ColumnCustomizationDialog(self, self)
        dialog.exec_()

    def _reorder_columns(self, new_order: list):
        """Move columns to match the requested logical order."""
        col_keys = list(self.columns.keys())
        header = self.table.horizontalHeader()
        for target_visual, key in enumerate(new_order):
            if key in col_keys:
                logical = col_keys.index(key)
                current_visual = header.visualIndex(logical)
                if current_visual != target_visual:
                    header.moveSection(current_visual, target_visual)
