# ---------------------------------------------------------------------------
# AlbumsTab — manage a track's album relationships
# ---------------------------------------------------------------------------
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.core.logger_config import logger
from src.track.track_edit_basetab import _BaseTab


class AlbumsTab(_BaseTab):
    def __init__(self, tracks: list, controller, parent=None):
        super().__init__(tracks, controller, parent)
        self._build_ui()

    # ── UI ────────────────────────────────────────────────────────────────

    def _build_ui(self):
        layout = QVBoxLayout(self)

        # ── Current album group ─────────────────────────────────────────
        current_group = QGroupBox("Current Album")
        current_layout = QVBoxLayout(current_group)

        primary_row = QHBoxLayout()
        self._primary_label = QLabel("—")
        self._primary_label.setProperty("textRole", "note")
        primary_row.addWidget(self._primary_label, stretch=1)

        self._open_primary_btn = QPushButton("Edit Album")
        self._open_primary_btn.setEnabled(False)
        self._open_primary_btn.clicked.connect(self._open_primary_album)
        primary_row.addWidget(self._open_primary_btn)

        self._remove_primary_btn = QPushButton("Remove Relationship")
        self._remove_primary_btn.setEnabled(False)
        self._remove_primary_btn.setToolTip(
            "Detaches this track from its album (track stays in library)"
        )
        self._remove_primary_btn.clicked.connect(self._remove_primary_album)
        primary_row.addWidget(self._remove_primary_btn)
        current_layout.addLayout(primary_row)
        layout.addWidget(current_group)

        # ── Set current album group ────────────────────────────────────
        set_group = QGroupBox("Set Current Album (search existing or create new)")
        set_layout = QVBoxLayout(set_group)

        add_row = QHBoxLayout()
        self._album_search = QLineEdit()
        self._album_search.setPlaceholderText("Search albums… (min 2 chars)")
        self._album_search.textChanged.connect(self._on_album_search)
        add_row.addWidget(self._album_search)

        self._album_combo = QComboBox()
        self._album_combo.setVisible(False)
        self._album_combo.currentIndexChanged.connect(self._on_album_selected)
        add_row.addWidget(self._album_combo)

        self._set_primary_btn = QPushButton("Set as Current Album")
        self._set_primary_btn.setEnabled(False)
        self._set_primary_btn.clicked.connect(self._set_primary_album)
        add_row.addWidget(self._set_primary_btn)
        set_layout.addLayout(add_row)
        layout.addWidget(set_group)

        # ── Virtual appearances group ─────────────────────────────────────
        virtual_group = QGroupBox("Virtual Appearances (track borrowed by other albums)")
        virtual_layout = QVBoxLayout(virtual_group)

        self._virtual_table = QTableWidget(0, 5)
        self._virtual_table.setHorizontalHeaderLabels(
            ["Album", "Track #", "Disc #", "Side", ""]
        )
        self._virtual_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.Stretch
        )
        self._virtual_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeToContents
        )
        self._virtual_table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeToContents
        )
        self._virtual_table.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.ResizeToContents
        )
        self._virtual_table.horizontalHeader().setSectionResizeMode(
            4, QHeaderView.ResizeToContents
        )
        self._virtual_table.verticalHeader().setVisible(False)
        # Sized by row count (see _update_virtual_table_height) rather than
        # expanding to fill the tab — this section is empty for most tracks.
        self._virtual_table.setSizePolicy(
            QSizePolicy.Expanding, QSizePolicy.Fixed
        )
        virtual_layout.addWidget(self._virtual_table)

        # ── Add virtual appearance ────────────────────────────────────────
        virt_add_row = QHBoxLayout()
        self._virt_search = QLineEdit()
        self._virt_search.setPlaceholderText("Search albums… (min 2 chars)")
        self._virt_search.textChanged.connect(self._on_virt_search)
        virt_add_row.addWidget(self._virt_search)

        self._virt_combo = QComboBox()
        self._virt_combo.setVisible(False)
        self._virt_combo.currentIndexChanged.connect(self._on_virt_selected)
        virt_add_row.addWidget(self._virt_combo)

        self._virt_track_num = QSpinBox()
        self._virt_track_num.setRange(0, 999)
        self._virt_track_num.setSpecialValueText("Track #")
        self._virt_track_num.setToolTip("Track number in this virtual appearance")
        virt_add_row.addWidget(self._virt_track_num)

        self._virt_disc_num = QSpinBox()
        self._virt_disc_num.setRange(0, 99)
        self._virt_disc_num.setSpecialValueText("Disc #")
        self._virt_disc_num.setToolTip("Disc number in this virtual appearance")
        virt_add_row.addWidget(self._virt_disc_num)

        self._virt_add_btn = QPushButton("Add Virtual Appearance")
        self._virt_add_btn.setEnabled(False)
        self._virt_add_btn.clicked.connect(self._add_virtual)
        virt_add_row.addWidget(self._virt_add_btn)
        virtual_layout.addLayout(virt_add_row)
        layout.addWidget(virtual_group)

        layout.addStretch(1)
        self._update_virtual_table_height()

    # ── Loading ───────────────────────────────────────────────────────────

    def load(self, tracks: list) -> None:
        self.tracks = tracks

        if self.is_multi:
            track_albums = [getattr(t, "album", None) for t in self.tracks]
            album_ids = {a.album_id if a else None for a in track_albums}
            if len(album_ids) == 1:
                album = track_albums[0]
                self._primary_label.setText(
                    album.album_name if album else "— (none)"
                )
                self._remove_primary_btn.setEnabled(bool(album))
            else:
                self._primary_label.setText(
                    f"(multiple albums across {len(self.tracks)} tracks)"
                )
                self._remove_primary_btn.setEnabled(True)
            self._open_primary_btn.setEnabled(False)
            self._set_primary_btn.setEnabled(len(self._album_search.text().strip()) >= 2)
            self._virt_add_btn.setEnabled(False)
            self._virtual_table.setRowCount(0)
            self._update_virtual_table_height()
            return

        # Primary album
        album = getattr(self.track, "album", None)
        if album:
            self._primary_label.setText(album.album_name)
            self._open_primary_btn.setEnabled(True)
            self._remove_primary_btn.setEnabled(True)
        else:
            self._primary_label.setText("— (none)")
            self._open_primary_btn.setEnabled(False)
            self._remove_primary_btn.setEnabled(False)

        # Virtual appearances
        self._virtual_table.setRowCount(0)
        for link in getattr(self.track, "virtual_appearances", []):
            alb = getattr(link, "album", None)
            if alb:
                self._add_virtual_row(
                    virtual_id=link.virtual_id,
                    album_name=alb.album_name,
                    album_id=alb.album_id,
                    track_num=link.virtual_track_number,
                    disc_num=link.virtual_disc_number,
                    side=link.virtual_side,
                )
        self._update_virtual_table_height()

    def _update_virtual_table_height(self) -> None:
        """Keep the table sized to its contents so an empty/short list of
        virtual appearances doesn't reserve a big block of the tab."""
        header_height = self._virtual_table.horizontalHeader().height()
        row_height = self._virtual_table.verticalHeader().defaultSectionSize()
        row_count = self._virtual_table.rowCount()
        visible_rows = max(row_count, 1)  # room for the "no rows" empty state
        visible_rows = min(visible_rows, 4)  # cap height; extra rows scroll
        frame = 2 * self._virtual_table.frameWidth()
        height = header_height + visible_rows * row_height + frame
        self._virtual_table.setFixedHeight(height)

    def _add_virtual_row(
        self, virtual_id, album_name, album_id, track_num, disc_num, side
    ):
        row = self._virtual_table.rowCount()
        self._virtual_table.insertRow(row)

        alb_item = QTableWidgetItem(album_name)
        alb_item.setData(Qt.UserRole, album_id)
        alb_item.setData(Qt.UserRole + 1, virtual_id)
        alb_item.setFlags(alb_item.flags() & ~Qt.ItemIsEditable)
        self._virtual_table.setItem(row, 0, alb_item)

        self._virtual_table.setItem(
            row, 1, QTableWidgetItem(str(track_num) if track_num else "")
        )
        self._virtual_table.setItem(
            row, 2, QTableWidgetItem(str(disc_num) if disc_num else "")
        )
        self._virtual_table.setItem(row, 3, QTableWidgetItem(side or ""))

        btn_widget = QWidget()
        btn_layout = QHBoxLayout(btn_widget)
        btn_layout.setContentsMargins(2, 2, 2, 2)

        edit_btn = QPushButton("Edit Album")
        edit_btn.clicked.connect(lambda _c, aid=album_id: self._open_album_by_id(aid))
        btn_layout.addWidget(edit_btn)

        rm_btn = QPushButton("Remove")
        rm_btn.clicked.connect(lambda _c, vid=virtual_id: self._remove_virtual(vid))
        btn_layout.addWidget(rm_btn)

        self._virtual_table.setCellWidget(row, 4, btn_widget)

    # ── Primary album search / set / remove ──────────────────────────────

    def _on_album_search(self, text: str):
        text = text.strip()
        self._album_combo.blockSignals(True)
        self._album_combo.clear()
        if len(text) >= 2:
            results = self.controller.get.get_entity_object("Album", album_name=text)
            self._album_combo.addItem(f"Create new: '{text}'", "new")
            if results is not None:
                items = results if isinstance(results, list) else [results]
                for a in items:
                    self._album_combo.addItem(a.album_name, a.album_id)
            self._album_combo.setVisible(self._album_combo.count() > 1)
        else:
            self._album_combo.setVisible(False)
        self._album_combo.blockSignals(False)
        self._set_primary_btn.setEnabled(len(text) >= 2)

    def _on_album_selected(self, index: int):
        if index > 0:
            self._album_search.blockSignals(True)
            self._album_search.setText(self._album_combo.currentText())
            self._album_search.blockSignals(False)

    def _set_primary_album(self):
        album_name = self._album_search.text().strip()
        if not album_name:
            return
        combo_data = (
            self._album_combo.currentData() if self._album_combo.isVisible() else None
        )
        if combo_data and combo_data != "new":
            album = self.controller.get.get_entity_object("Album", album_id=combo_data)
        else:
            existing = self.controller.get.get_entity_object(
                "Album", album_name=album_name
            )
            if existing:
                album = existing if not isinstance(existing, list) else existing[0]
            else:
                album = self.controller.add.add_entity("Album", album_name=album_name)
        if not album:
            QMessageBox.warning(self, "Error", "Could not resolve or create album.")
            return

        if self.is_multi:
            confirm = QMessageBox.question(
                self,
                "Set Primary Album",
                f"Set '{album.album_name}' as the primary album for all "
                f"{len(self.tracks)} selected tracks?",
                QMessageBox.Yes | QMessageBox.No,
            )
            if confirm != QMessageBox.Yes:
                return
            track_ids = [track.track_id for track in self.tracks]
            if not self.controller.update.update_entities(
                "Track", track_ids, album_id=album.album_id
            ):
                QMessageBox.warning(
                    self, "Error", "Failed to set album for the selected tracks."
                )
        else:
            try:
                self.controller.update.update_entity(
                    "Track", self.track.track_id, album_id=album.album_id
                )
            except Exception as e:
                logger.error(f"Failed to set primary album: {e}")
                QMessageBox.warning(self, "Error", f"Failed to set album:\n{e}")
                return

        self._album_search.clear()
        self._album_combo.setVisible(False)
        self._refresh_tracks()
        self.load(self.tracks)

    def _remove_primary_album(self):
        if self.is_multi:
            question = (
                f"Detach all {len(self.tracks)} selected tracks from their "
                "primary album?\nThe tracks will remain in the library but "
                "will have no album."
            )
        else:
            question = (
                "Detach this track from its primary album?\n"
                "The track will remain in the library but will have no album."
            )
        confirm = QMessageBox.question(
            self,
            "Remove Album Relationship",
            question,
            QMessageBox.Yes | QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return

        if self.is_multi:
            track_ids = [track.track_id for track in self.tracks]
            if not self.controller.update.update_entities(
                "Track", track_ids, album_id=None
            ):
                QMessageBox.warning(
                    self, "Error", "Failed to remove album for the selected tracks."
                )
        else:
            try:
                self.controller.update.update_entity(
                    "Track", self.track.track_id, album_id=None
                )
            except Exception as e:
                logger.error(f"Failed to remove primary album: {e}")
                QMessageBox.warning(self, "Error", f"Failed to remove album:\n{e}")
                return

        self._refresh_tracks()
        self.load(self.tracks)

    def _refresh_tracks(self):
        updated_tracks = []
        for track in self.tracks:
            updated = self.controller.get.get_entity_object(
                "Track", track_id=track.track_id
            )
            updated_tracks.append(updated if updated else track)
        self.tracks = updated_tracks

    def _open_primary_album(self):
        album = getattr(self.track, "album", None)
        if album:
            self._open_album_by_id(album.album_id)

    # ── Virtual appearance search / add / remove ──────────────────────────

    def _on_virt_search(self, text: str):
        text = text.strip()
        self._virt_combo.blockSignals(True)
        self._virt_combo.clear()
        if len(text) >= 2:
            results = self.controller.get.get_entity_object("Album", album_name=text)
            self._virt_combo.addItem(f"Create new: '{text}'", "new")
            if results is not None:
                items = results if isinstance(results, list) else [results]
                for a in items:
                    self._virt_combo.addItem(a.album_name, a.album_id)
            self._virt_combo.setVisible(self._virt_combo.count() > 1)
        else:
            self._virt_combo.setVisible(False)
        self._virt_combo.blockSignals(False)
        self._virt_add_btn.setEnabled(len(text) >= 2)

    def _on_virt_selected(self, index: int):
        if index > 0:
            self._virt_search.blockSignals(True)
            self._virt_search.setText(self._virt_combo.currentText())
            self._virt_search.blockSignals(False)

    def _add_virtual(self):
        album_name = self._virt_search.text().strip()
        if not album_name:
            return
        combo_data = (
            self._virt_combo.currentData() if self._virt_combo.isVisible() else None
        )
        if combo_data and combo_data != "new":
            album = self.controller.get.get_entity_object("Album", album_id=combo_data)
        else:
            existing = self.controller.get.get_entity_object(
                "Album", album_name=album_name
            )
            if existing:
                album = existing if not isinstance(existing, list) else existing[0]
            else:
                album = self.controller.add.add_entity("Album", album_name=album_name)
        if not album:
            QMessageBox.warning(self, "Error", "Could not resolve or create album.")
            return
        track_num = self._virt_track_num.value() or None
        disc_num = self._virt_disc_num.value() or None
        try:
            self.controller.add.add_entity(
                "AlbumVirtualTrack",
                album_id=album.album_id,
                track_id=self.track.track_id,
                virtual_track_number=track_num,
                virtual_disc_number=disc_num,
            )
        except Exception as e:
            logger.error(f"Failed to add virtual appearance: {e}")
            QMessageBox.warning(
                self, "Error", f"Failed to add virtual appearance:\n{e}"
            )
            return
        self._virt_search.clear()
        self._virt_combo.setVisible(False)
        self._virt_track_num.setValue(0)
        self._virt_disc_num.setValue(0)
        updated = self.controller.get.get_entity_object(
            "Track", track_id=self.track.track_id
        )
        if updated:
            self.tracks = [updated]
        self.load(self.tracks)

    def _remove_virtual(self, virtual_id: int):
        try:
            self.controller.delete.delete_entity(
                "AlbumVirtualTrack", virtual_id=virtual_id
            )
        except Exception as e:
            logger.error(f"Failed to remove virtual appearance: {e}")
            QMessageBox.warning(self, "Error", f"Failed to remove:\n{e}")
            return
        updated = self.controller.get.get_entity_object(
            "Track", track_id=self.track.track_id
        )
        if updated:
            self.tracks = [updated]
        self.load(self.tracks)

    # ── Open album editor ─────────────────────────────────────────────────

    def _open_album_by_id(self, album_id: int):
        try:
            from src.album.base_album_edit import AlbumEditor

            album = self.controller.get.get_entity_object("Album", album_id=album_id)
            if album:
                dlg = AlbumEditor(self.controller, album, self)
                dlg.exec()
                # Refresh track data after album edit closes
                updated = self.controller.get.get_entity_object(
                    "Track", track_id=self.track.track_id
                )
                if updated:
                    self.tracks = [updated]
                self.load(self.tracks)
        except Exception as e:
            logger.error(f"Failed to open album editor: {e}")
            QMessageBox.warning(self, "Error", f"Could not open album editor:\n{e}")
