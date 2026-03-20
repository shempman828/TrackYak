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


def _remove_selected_row(parent_widget, table, remove_fn):
    rows = table.selectionModel().selectedRows()
    if not rows:
        QMessageBox.information(
            parent_widget, "No Selection", "Please select a row first."
        )
        return
    remove_fn(rows[0].row())


def _find_or_create_artist(controller, name, **create_kwargs):
    """
    Look up an artist by name; create one if none is found.
    Raises on error — let the caller catch and show a dialog.
    """
    result = controller.get.get_entity_object("Artist", artist_name=name)
    if result:
        return result[0] if isinstance(result, list) else result
    return controller.add.add_entity("Artist", artist_name=name, **create_kwargs)


class InfluencesTab(QWidget):
    def __init__(self, controller, artist, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.artist = artist
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        splitter = QSplitter(Qt.Vertical)

        # This artist influenced others
        inf_grp = QGroupBox("This Artist Influenced")
        inf_layout = QVBoxLayout(inf_grp)
        self.influenced_table = _make_table(
            ["Influenced Artist", "Description"], editable=False
        )
        inf_layout.addWidget(self.influenced_table)
        add_inf_row = QHBoxLayout()
        self.new_influenced_edit = QLineEdit()
        self.new_influenced_edit.setPlaceholderText("Artist name...")
        self.new_influenced_desc_edit = QLineEdit()
        self.new_influenced_desc_edit.setPlaceholderText("Description (optional)")
        add_inf_btn = QPushButton("Add")
        add_inf_btn.clicked.connect(self._add_influenced)
        rm_inf_btn = QPushButton("Remove Selected")
        rm_inf_btn.clicked.connect(
            lambda: _remove_selected_row(
                self, self.influenced_table, self._remove_influenced
            )
        )
        add_inf_row.addWidget(self.new_influenced_edit, 2)
        add_inf_row.addWidget(self.new_influenced_desc_edit, 2)
        add_inf_row.addWidget(add_inf_btn)
        inf_layout.addLayout(add_inf_row)
        inf_layout.addWidget(rm_inf_btn, alignment=Qt.AlignLeft)
        splitter.addWidget(inf_grp)

        # Artists who influenced this one
        infl_grp = QGroupBox("Artists Who Influenced This Artist")
        infl_layout = QVBoxLayout(infl_grp)
        self.influencer_table = _make_table(
            ["Influencer Artist", "Description"], editable=False
        )
        infl_layout.addWidget(self.influencer_table)
        add_infl_row = QHBoxLayout()
        self.new_influencer_edit = QLineEdit()
        self.new_influencer_edit.setPlaceholderText("Artist name...")
        self.new_influencer_desc_edit = QLineEdit()
        self.new_influencer_desc_edit.setPlaceholderText("Description (optional)")
        add_infl_btn = QPushButton("Add")
        add_infl_btn.clicked.connect(self._add_influencer)
        rm_infl_btn = QPushButton("Remove Selected")
        rm_infl_btn.clicked.connect(
            lambda: _remove_selected_row(
                self, self.influencer_table, self._remove_influencer
            )
        )
        add_infl_row.addWidget(self.new_influencer_edit, 2)
        add_infl_row.addWidget(self.new_influencer_desc_edit, 2)
        add_infl_row.addWidget(add_infl_btn)
        infl_layout.addLayout(add_infl_row)
        infl_layout.addWidget(rm_infl_btn, alignment=Qt.AlignLeft)
        splitter.addWidget(infl_grp)

        layout.addWidget(splitter)

    def load(self, artist):
        self.artist = artist

        self.influenced_table.setRowCount(0)
        for rel in getattr(artist, "influencer_relations", []):
            if rel.influenced is None:
                continue
            _append_row(
                self.influenced_table,
                [rel.influenced.artist_name, rel.description or ""],
                user_data=rel.influence_id if hasattr(rel, "influence_id") else None,
            )

        self.influencer_table.setRowCount(0)
        for rel in getattr(artist, "influenced_relations", []):
            if rel.influencer is None:
                continue
            _append_row(
                self.influencer_table,
                [rel.influencer.artist_name, rel.description or ""],
                user_data=rel.influence_id if hasattr(rel, "influence_id") else None,
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

    def _add_influenced(self):
        name = self.new_influenced_edit.text().strip()
        if not name:
            return
        try:
            influenced = _find_or_create_artist(self.controller, name)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Could not find/create artist:\n{e}")
            return
        try:
            self.controller.add.add_entity(
                "ArtistInfluence",
                influencer_id=self.artist.artist_id,
                influenced_id=influenced.artist_id,
                description=self.new_influenced_desc_edit.text().strip() or None,
            )
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Could not add influence:\n{e}")
            return
        self._reload_and_refresh()
        self.new_influenced_edit.clear()
        self.new_influenced_desc_edit.clear()

    def _remove_influenced(self, row):
        influence_id = self.influenced_table.item(row, 0).data(Qt.UserRole)
        if influence_id is None:
            return
        try:
            self.controller.delete.delete_entity("ArtistInfluence", influence_id)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Could not remove influence:\n{e}")
            return
        self._reload_and_refresh()

    def _add_influencer(self):
        name = self.new_influencer_edit.text().strip()
        if not name:
            return
        try:
            influencer = _find_or_create_artist(self.controller, name)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Could not find/create artist:\n{e}")
            return
        try:
            self.controller.add.add_entity(
                "ArtistInfluence",
                influencer_id=influencer.artist_id,
                influenced_id=self.artist.artist_id,
                description=self.new_influencer_desc_edit.text().strip() or None,
            )
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Could not add influence:\n{e}")
            return
        self._reload_and_refresh()
        self.new_influencer_edit.clear()
        self.new_influencer_desc_edit.clear()

    def _remove_influencer(self, row):
        influence_id = self.influencer_table.item(row, 0).data(Qt.UserRole)
        if influence_id is None:
            return
        try:
            self.controller.delete.delete_entity("ArtistInfluence", influence_id)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Could not remove influence:\n{e}")
            return
        self._reload_and_refresh()
