# ---------------------------------------------------------------------------
# RolesTab — artist / role relationships
# ---------------------------------------------------------------------------
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from src.logger_config import logger
from src.track_edit_basetab import _BaseTab


class RolesTab(_BaseTab):
    def __init__(self, tracks: list, controller, parent=None):
        super().__init__(tracks, controller, parent)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        # ── Search row ────────────────────────────────────────────────────
        search_row = QHBoxLayout()

        self._artist_search = QLineEdit()
        self._artist_search.setPlaceholderText("Search artists… (min 2 chars)")
        self._artist_search.textChanged.connect(self._on_artist_search)
        search_row.addWidget(self._artist_search)

        self._artist_combo = QComboBox()
        self._artist_combo.setVisible(False)
        self._artist_combo.currentIndexChanged.connect(self._on_artist_selected)
        search_row.addWidget(self._artist_combo)

        self._role_edit = QLineEdit()
        self._role_edit.setPlaceholderText("Role (e.g. Performer, Composer…)")
        self._role_edit.textChanged.connect(self._update_add_btn)
        search_row.addWidget(self._role_edit)

        self._add_btn = QPushButton("Add Role")
        self._add_btn.setEnabled(False)
        self._add_btn.clicked.connect(self._add_role)
        search_row.addWidget(self._add_btn)
        layout.addLayout(search_row)

        # ── Current roles table ───────────────────────────────────────────
        self._table = QTableWidget(0, 3)
        self._table.setHorizontalHeaderLabels(["Artist", "Role", ""])
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeToContents
        )
        self._table.horizontalHeader().setToolTip(
            "Double-click a role to edit it inline"
        )
        self._table.cellChanged.connect(self._on_role_cell_changed)
        layout.addWidget(self._table)

    # ── Loading ───────────────────────────────────────────────────────────

    def load(self, tracks: list) -> None:
        self.tracks = tracks
        self._table.setRowCount(0)
        if self.is_multi:
            self._load_common_roles()
        else:
            for role_assoc in self.track.artist_roles:
                self._add_table_row(
                    artist_name=role_assoc.artist.artist_name
                    if role_assoc.artist
                    else "?",
                    role_name=role_assoc.role.role_name if role_assoc.role else "?",
                    artist_id=role_assoc.artist.artist_id
                    if role_assoc.artist
                    else None,
                    role_id=role_assoc.role.role_id if role_assoc.role else None,
                )

    def _load_common_roles(self):
        """Show only roles shared by every track in the selection."""
        all_sets = []
        for t in self.tracks:
            s = set()
            for ra in t.artist_roles:
                if ra.artist and ra.role:
                    s.add(
                        (
                            ra.artist.artist_id,
                            ra.role.role_id,
                            ra.artist.artist_name,
                            ra.role.role_name,
                        )
                    )
            all_sets.append(s)
        common = all_sets[0]
        for s in all_sets[1:]:
            common &= s
        for artist_id, role_id, artist_name, role_name in common:
            self._add_table_row(artist_name, role_name, artist_id, role_id)

    def _add_table_row(self, artist_name, role_name, artist_id, role_id):
        row = self._table.rowCount()
        self._table.insertRow(row)

        artist_item = QTableWidgetItem(artist_name)
        artist_item.setData(Qt.UserRole, artist_id)
        artist_item.setFlags(artist_item.flags() & ~Qt.ItemIsEditable)  # read-only
        self._table.setItem(row, 0, artist_item)

        role_item = QTableWidgetItem(role_name)
        role_item.setData(Qt.UserRole, role_id)
        role_item.setData(Qt.UserRole + 1, role_name)  # stash original name for revert
        self._table.setItem(row, 1, role_item)

        btn = QPushButton("Remove")
        btn.clicked.connect(lambda _checked, r=row: self._remove_role(r))
        self._table.setCellWidget(row, 2, btn)

    # ── Search ────────────────────────────────────────────────────────────

    def _on_artist_search(self, text: str):
        text = text.strip()
        self._artist_combo.blockSignals(True)
        self._artist_combo.clear()
        if len(text) >= 2:
            results = self.controller.get.get_entity_object("Artist", artist_name=text)
            self._artist_combo.addItem(f"Create new: '{text}'", "new")
            if results is not None:
                items = results if isinstance(results, list) else [results]
                for a in items:
                    self._artist_combo.addItem(a.artist_name, a.artist_id)
                # Auto-select the first real match so "Create new" isn't the default
                # when matching artists already exist.
                if items:
                    self._artist_combo.setCurrentIndex(1)
            self._artist_combo.setVisible(self._artist_combo.count() > 1)
        else:
            self._artist_combo.setVisible(False)
        self._artist_combo.blockSignals(False)
        self._update_add_btn()

    def _on_artist_selected(self, index: int):
        if index > 0:
            self._artist_search.blockSignals(True)
            self._artist_search.setText(self._artist_combo.currentText())
            self._artist_search.blockSignals(False)
        self._update_add_btn()

    def _update_add_btn(self):
        artist_ok = len(self._artist_search.text().strip()) >= 2
        role_ok = len(self._role_edit.text().strip()) >= 2
        self._add_btn.setEnabled(artist_ok and role_ok)

    # ── Add / Remove ──────────────────────────────────────────────────────

    def _add_role(self):
        artist_name = self._artist_search.text().strip()
        role_name = self._role_edit.text().strip()
        if not artist_name or not role_name:
            return

        # Resolve or create artist
        combo_data = (
            self._artist_combo.currentData() if self._artist_combo.isVisible() else None
        )
        if combo_data and combo_data != "new":
            artist = self.controller.get.get_entity_object(
                "Artist", artist_id=combo_data
            )
        else:
            existing = self.controller.get.get_entity_object(
                "Artist", artist_name=artist_name
            )
            if existing:
                artist = existing if not isinstance(existing, list) else existing[0]
            else:
                artist = self.controller.add.add_entity(
                    "Artist", artist_name=artist_name
                )

        # Resolve or create role
        existing_role = self.controller.get.get_entity_object(
            "Role", role_name=role_name
        )
        if existing_role:
            role = (
                existing_role
                if not isinstance(existing_role, list)
                else existing_role[0]
            )
        else:
            role = self.controller.add.add_entity("Role", role_name=role_name)

        if not artist or not role:
            QMessageBox.warning(self, "Error", "Could not resolve artist or role.")
            return

        for track in self.tracks:
            try:
                self.controller.add.add_entity(
                    "TrackArtistRole",
                    track_id=track.track_id,
                    artist_id=artist.artist_id,
                    role_id=role.role_id,
                )
            except Exception as e:
                logger.error(f"Failed to add role to track {track.track_id}: {e}")

        self._artist_search.clear()
        self._role_edit.clear()
        self._artist_combo.setVisible(False)
        self.load(self.tracks)

    def _on_role_cell_changed(self, row: int, col: int):
        # Only care about the Role column (col 1)
        if col != 1:
            return

        role_item = self._table.item(row, 1)
        artist_item = self._table.item(row, 0)
        if not role_item or not artist_item:
            return

        new_role_name = role_item.text().strip()
        original_role_name = role_item.data(Qt.UserRole + 1)  # what we stashed on load
        old_role_id = role_item.data(Qt.UserRole)
        artist_id = artist_item.data(Qt.UserRole)

        # Nothing actually changed — ignore
        if new_role_name == original_role_name:
            return

        # Empty input — revert silently
        if not new_role_name:
            self._table.blockSignals(True)
            role_item.setText(original_role_name)
            self._table.blockSignals(False)
            return

        # Resolve or create the new role (same logic as _add_role)
        existing_role = self.controller.get.get_entity_object(
            "Role", role_name=new_role_name
        )
        if existing_role:
            new_role = (
                existing_role
                if not isinstance(existing_role, list)
                else existing_role[0]
            )
        else:
            new_role = self.controller.add.add_entity("Role", role_name=new_role_name)

        if not new_role:
            logger.error(f"Could not resolve or create role '{new_role_name}'")
            self._table.blockSignals(True)
            role_item.setText(original_role_name)
            self._table.blockSignals(False)
            return

        # For every track: delete the old TrackArtistRole, add the new one
        for track in self.tracks:
            try:
                self.controller.delete.delete_entity(
                    "TrackArtistRole",
                    track_id=track.track_id,
                    artist_id=artist_id,
                    role_id=old_role_id,
                )
                self.controller.add.add_entity(
                    "TrackArtistRole",
                    track_id=track.track_id,
                    artist_id=artist_id,
                    role_id=new_role.role_id,
                )
            except Exception as e:
                logger.error(f"Failed to update role on track {track.track_id}: {e}")

        # Reload the table to reflect the final state cleanly
        self.load(self.tracks)

    def _remove_role(self, row: int):
        artist_item = self._table.item(row, 0)
        role_item = self._table.item(row, 1)
        if not artist_item or not role_item:
            return
        artist_id = artist_item.data(Qt.UserRole)
        role_id = role_item.data(Qt.UserRole)
        for track in self.tracks:
            try:
                self.controller.delete.delete_entity(
                    "TrackArtistRole",
                    track_id=track.track_id,
                    artist_id=artist_id,
                    role_id=role_id,
                )
            except Exception as e:
                logger.error(f"Failed to remove role from track {track.track_id}: {e}")
        self.load(self.tracks)
