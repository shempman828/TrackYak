# ══════════════════════════════════════════════════════════════════════════════
# Tab: Influences
# ══════════════════════════════════════════════════════════════════════════════
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.logger_config import logger


def _make_table(headers, editable=True):
    """Create a standard QTableWidget with consistent styling."""
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.horizontalHeader().setStretchLastSection(True)
    t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
    t.horizontalHeader().setSectionResizeMode(len(headers) - 1, QHeaderView.Stretch)
    t.setSelectionBehavior(QAbstractItemView.SelectRows)
    t.verticalHeader().setVisible(False)
    t.setAlternatingRowColors(True)
    if not editable:
        t.setEditTriggers(QAbstractItemView.NoEditTriggers)
    return t


def _set_item(table, row, col, text, user_data=None):
    item = QTableWidgetItem(str(text) if text is not None else "")
    if user_data is not None:
        item.setData(Qt.UserRole, user_data)
    table.setItem(row, col, item)


def _append_row(table, values, user_data=None):
    row = table.rowCount()
    table.insertRow(row)
    for col, val in enumerate(values):
        _set_item(table, row, col, val, user_data if col == 0 else None)
    return row


def _find_or_create_artist(controller, name, **create_kwargs):
    """
    Look up an artist by name; create one if none is found.
    Raises on error — let the caller catch and show a dialog.
    """
    result = controller.get.get_entity_object("Artist", artist_name=name)
    if result:
        return result[0] if isinstance(result, list) else result
    return controller.add.add_entity("Artist", artist_name=name, **create_kwargs)


class _InfluenceRelationPanel(QGroupBox):
    """
    One side of an influence relationship ("influenced" or "influencer").
    Table + a single-row add/remove toolbar underneath — no wasted rows.
    """

    def __init__(self, title, column_label, on_add, on_remove, parent=None):
        super().__init__(title, parent)
        self._on_add = on_add
        self._on_remove = on_remove

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)

        self.table = _make_table([column_label, "Description"], editable=False)
        layout.addWidget(self.table)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(4)

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("Artist name…")
        self.desc_edit = QLineEdit()
        self.desc_edit.setPlaceholderText("Description (optional)")

        add_btn = QPushButton("Add")
        add_btn.setFixedWidth(60)
        remove_btn = QPushButton("Remove")
        remove_btn.setFixedWidth(70)

        add_btn.clicked.connect(self._handle_add)
        remove_btn.clicked.connect(self._handle_remove)
        self.name_edit.returnPressed.connect(self._handle_add)
        self.desc_edit.returnPressed.connect(self._handle_add)

        toolbar.addWidget(self.name_edit, 2)
        toolbar.addWidget(self.desc_edit, 2)
        toolbar.addWidget(add_btn)
        toolbar.addWidget(remove_btn)
        layout.addLayout(toolbar)

    def _handle_add(self):
        name = self.name_edit.text().strip()
        if not name:
            return
        self._on_add(name, self.desc_edit.text().strip() or None)

    def _handle_remove(self):
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            QMessageBox.information(self, "No Selection", "Please select a row first.")
            return
        self._on_remove(rows[0].row())

    def clear_inputs(self):
        self.name_edit.clear()
        self.desc_edit.clear()

    def populate(self, rows):
        """rows: iterable of (label, description, user_data)."""
        self.table.setRowCount(0)
        for label, description, user_data in rows:
            _append_row(self.table, [label, description], user_data=user_data)


class InfluencesTab(QWidget):
    def __init__(self, controller, artist, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.artist = artist
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        splitter = QSplitter(Qt.Vertical)

        self.influenced_panel = _InfluenceRelationPanel(
            "This Artist Influenced",
            "Influenced Artist",
            on_add=self._add_influenced,
            on_remove=self._remove_influenced,
        )
        self.influencer_panel = _InfluenceRelationPanel(
            "Artists Who Influenced This Artist",
            "Influencer Artist",
            on_add=self._add_influencer,
            on_remove=self._remove_influencer,
        )

        splitter.addWidget(self.influenced_panel)
        splitter.addWidget(self.influencer_panel)
        layout.addWidget(splitter)

    def load(self, artist):
        self.artist = artist

        self.influenced_panel.populate(
            (
                rel.influenced.artist_name,
                rel.description or "",
                getattr(rel, "influence_id", None),
            )
            for rel in getattr(artist, "influencer_relations", [])
            if rel.influenced is not None
        )
        self.influencer_panel.populate(
            (
                rel.influencer.artist_name,
                rel.description or "",
                getattr(rel, "influence_id", None),
            )
            for rel in getattr(artist, "influenced_relations", [])
            if rel.influencer is not None
        )

    def _reload_and_refresh(self):
        try:
            refreshed = self.controller.get.get_entity_object(
                "Artist", artist_id=self.artist.artist_id
            )
            if refreshed:
                self.artist = refreshed
        except Exception as e:
            logger.warning(f"Could not reload artist: {e}")
        self.load(self.artist)

    # ── shared add/remove, parameterized by direction ──────────────────────

    def _add_relation(self, name, description, *, this_artist_is_influencer):
        try:
            other = _find_or_create_artist(self.controller, name)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Could not find/create artist:\n{e}")
            return

        if this_artist_is_influencer:
            kwargs = dict(
                influencer_id=self.artist.artist_id, influenced_id=other.artist_id
            )
            panel = self.influenced_panel
        else:
            kwargs = dict(
                influencer_id=other.artist_id, influenced_id=self.artist.artist_id
            )
            panel = self.influencer_panel

        try:
            self.controller.add.add_entity(
                "ArtistInfluence", description=description, **kwargs
            )
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Could not add influence:\n{e}")
            return

        self._reload_and_refresh()
        panel.clear_inputs()

    def _remove_relation(self, panel, row):
        item = panel.table.item(row, 0)
        influence_id = item.data(Qt.UserRole) if item else None
        if influence_id is None:
            return
        try:
            self.controller.delete.delete_entity("ArtistInfluence", influence_id)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Could not remove influence:\n{e}")
            return
        self._reload_and_refresh()

    # ── thin direction-specific wrappers (kept for clear callback signatures) ──

    def _add_influenced(self, name, description):
        self._add_relation(name, description, this_artist_is_influencer=True)

    def _add_influencer(self, name, description):
        self._add_relation(name, description, this_artist_is_influencer=False)

    def _remove_influenced(self, row):
        self._remove_relation(self.influenced_panel, row)

    def _remove_influencer(self, row):
        self._remove_relation(self.influencer_panel, row)
