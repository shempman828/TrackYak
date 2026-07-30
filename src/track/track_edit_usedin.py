# ---------------------------------------------------------------------------
# UsedInTab — external contexts where this track has been used (soundtracks
# outside official releases, TV shows, games, commercials, etc.)
# ---------------------------------------------------------------------------
from __future__ import annotations

from sqlalchemy.exc import SQLAlchemyError

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from src.core.logger_config import logger
from src.track.track_edit_basetab import _BaseTab

# Kept in sync with the CheckConstraint on TrackUsage.usage_type
USAGE_TYPES = ["Film", "TV Show", "Video Game", "Live Event", "Commercial", "Other"]


class UsedInTab(_BaseTab):
    """
    Shows a table of TrackUsage records for this track -- external contexts
    (a film, TV show, game, etc.) the track was used in, distinct from its
    official album/soundtrack release. E.g. a song used in "Life is Strange"
    or "Person of Interest" without appearing on either show's soundtrack
    album.

    For multi-track editing, the table shows usage entries common to every
    selected track, and Add applies the new entry to all selected tracks at
    once (each track gets its own TrackUsage row).
    """

    def __init__(self, tracks: list, controller, parent=None):
        super().__init__(tracks, controller, parent)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        # ── Add row ───────────────────────────────────────────────────────
        add_row = QHBoxLayout()

        self._type_combo = QComboBox()
        self._type_combo.addItems(USAGE_TYPES)
        add_row.addWidget(self._type_combo)

        self._title_edit = QLineEdit()
        self._title_edit.setPlaceholderText("Title (e.g. Life is Strange)")
        add_row.addWidget(self._title_edit)

        self._year_spin = QSpinBox()
        self._year_spin.setRange(0, 2200)
        self._year_spin.setSpecialValueText("Year")
        add_row.addWidget(self._year_spin)

        self._add_btn = QPushButton("Add")
        self._add_btn.clicked.connect(self._add_entry)
        add_row.addWidget(self._add_btn)
        layout.addLayout(add_row)

        detail_row = QHBoxLayout()
        self._description_edit = QLineEdit()
        self._description_edit.setPlaceholderText(
            "Description (e.g. Plays during the credits scene)"
        )
        detail_row.addWidget(self._description_edit)

        self._wiki_edit = QLineEdit()
        self._wiki_edit.setPlaceholderText("Wikipedia link (optional)")
        detail_row.addWidget(self._wiki_edit)
        layout.addLayout(detail_row)

        # ── Table ─────────────────────────────────────────────────────────
        self._table = QTableWidget(0, 6)
        self._table.setHorizontalHeaderLabels(
            ["Type", "Title", "Year", "Description", "Wikipedia", ""]
        )
        self._table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeToContents
        )
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeToContents
        )
        self._table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(
            4, QHeaderView.ResizeToContents
        )
        self._table.horizontalHeader().setSectionResizeMode(
            5, QHeaderView.ResizeToContents
        )
        layout.addWidget(self._table)

    def load(self, tracks: list) -> None:
        self.tracks = tracks
        self._table.setRowCount(0)

        if self.is_multi:
            rows = self._common_usages()
        else:
            rows = [
                (
                    u.usage_id,
                    u.usage_type,
                    u.title,
                    u.year,
                    u.description,
                    u.wikipedia_link,
                )
                for u in self.track.usages
            ]

        for usage_id, usage_type, title, year, description, wikipedia_link in rows:
            self._add_row(usage_id, usage_type, title, year, description, wikipedia_link)

    def _common_usages(self):
        """Usage entries (matched by type/title/year) present on every selected track."""
        all_sets = []
        for t in self.tracks:
            s = {
                (
                    u.usage_type,
                    u.title,
                    u.year or 0,
                    u.description or "",
                    u.wikipedia_link or "",
                )
                for u in t.usages
            }
            all_sets.append(s)
        common = all_sets[0]
        for s in all_sets[1:]:
            common &= s
        return [
            (None, usage_type, title, year or None, description or None, wiki or None)
            for usage_type, title, year, description, wiki in common
        ]

    def _add_row(self, usage_id, usage_type, title, year, description, wikipedia_link):
        row = self._table.rowCount()
        self._table.insertRow(row)

        type_item = QTableWidgetItem(usage_type or "")
        type_item.setFlags(type_item.flags() & ~Qt.ItemIsEditable)
        type_item.setData(Qt.UserRole, usage_id)
        self._table.setItem(row, 0, type_item)

        title_item = QTableWidgetItem(title or "")
        title_item.setFlags(title_item.flags() & ~Qt.ItemIsEditable)
        self._table.setItem(row, 1, title_item)

        year_item = QTableWidgetItem(str(year) if year else "")
        year_item.setFlags(year_item.flags() & ~Qt.ItemIsEditable)
        self._table.setItem(row, 2, year_item)

        desc_item = QTableWidgetItem(description or "")
        desc_item.setFlags(desc_item.flags() & ~Qt.ItemIsEditable)
        self._table.setItem(row, 3, desc_item)

        wiki_item = QTableWidgetItem(wikipedia_link or "")
        wiki_item.setFlags(wiki_item.flags() & ~Qt.ItemIsEditable)
        self._table.setItem(row, 4, wiki_item)

        rm_btn = QPushButton("Remove")
        rm_btn.clicked.connect(lambda _c, r=row: self._remove_row(r))
        self._table.setCellWidget(row, 5, rm_btn)

    def _add_entry(self):
        usage_type = self._type_combo.currentText()
        title = self._title_edit.text().strip()
        year = self._year_spin.value() or None
        description = self._description_edit.text().strip() or None
        wikipedia_link = self._wiki_edit.text().strip() or None

        if not title:
            QMessageBox.warning(self, "Input Required", "Please enter a title.")
            return

        rows = [
            {
                "track_id": track.track_id,
                "usage_type": usage_type,
                "title": title,
                "year": year,
                "description": description,
                "wikipedia_link": wikipedia_link,
            }
            for track in self.tracks
        ]

        try:
            self.controller.add.add_entities("TrackUsage", rows)
        except SQLAlchemyError as e:
            logger.error(f"Failed to add TrackUsage entry: {e}")
            QMessageBox.warning(self, "Error", f"Failed to add entry:\n{e}")
            return

        self._title_edit.clear()
        self._year_spin.setValue(0)
        self._description_edit.clear()
        self._wiki_edit.clear()

        self.load(self.tracks)

    def _remove_row(self, row: int):
        type_item = self._table.item(row, 0)
        title_item = self._table.item(row, 1)
        year_item = self._table.item(row, 2)
        if not type_item or not title_item:
            return

        usage_id = type_item.data(Qt.UserRole)
        try:
            if usage_id is not None:
                self.controller.delete.delete_entity("TrackUsage", entity_id=usage_id)
            else:
                # Multi-track batch row -- each track owns its own row, so
                # remove the matching entry from every selected track.
                track_ids = [track.track_id for track in self.tracks]
                year_text = year_item.text() if year_item else ""
                self.controller.delete.delete_entity(
                    "TrackUsage",
                    track_id=track_ids,
                    usage_type=type_item.text(),
                    title=title_item.text(),
                    year=int(year_text) if year_text else None,
                )
        except (SQLAlchemyError, ValueError) as e:
            logger.error(f"Failed to remove TrackUsage entry: {e}")
            QMessageBox.warning(self, "Error", f"Failed to remove entry:\n{e}")
            return

        self.load(self.tracks)
