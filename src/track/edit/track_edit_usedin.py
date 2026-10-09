"""UsedInTab: external works (film, TV, games, ...) that used the track outside its releases."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QHeaderView, QLineEdit, QMessageBox, QPushButton, QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout

from src.foundation.logger_config import logger
from src.track.edit.track_edit_basetab import _BaseTab

# Kept in sync with the CheckConstraint on TrackUsage.usage_type
USAGE_TYPES = ["Film", "TV Show", "Video Game", "Live Event", "Commercial", "Other"]


def _usage_key(u) -> tuple:
    """Return the comparable (type, title, year, description, link) key of a TrackUsage."""
    return (u.usage_type, u.title, u.year or 0, u.description or "", u.wikipedia_link or "")


class UsedInTab(_BaseTab):
    """Add and remove external usages; in multi mode, entries common to every track."""

    saves_immediately = True  # add/remove write to the DB at once

    def __init__(self, tracks: list, controller, parent=None):
        super().__init__(tracks, controller, parent)
        self._build_ui()

    def _build_ui(self):
        """Build the entry fields and the usage table."""
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
        self._description_edit.setPlaceholderText("Description (e.g. Plays during the credits scene)")
        detail_row.addWidget(self._description_edit)

        self._wiki_edit = QLineEdit()
        self._wiki_edit.setPlaceholderText("Wikipedia link (optional)")
        detail_row.addWidget(self._wiki_edit)
        layout.addLayout(detail_row)

        # ── Table ─────────────────────────────────────────────────────────
        self._table = QTableWidget(0, 6)
        self._table.setHorizontalHeaderLabels(["Type", "Title", "Year", "Description", "Wikipedia", ""])
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeToContents)
        layout.addWidget(self._table)

    def load(self, tracks: list) -> None:
        """Show the usages of the track (or the ones every edited track shares)."""
        self.tracks = tracks
        self._table.setRowCount(0)

        rows = self._common_usages() if self.is_multi else [(u.usage_id, u.usage_type, u.title, u.year, u.description, u.wikipedia_link) for u in self.track.usages]

        for usage_id, usage_type, title, year, description, wikipedia_link in rows:
            self._add_row(usage_id, usage_type, title, year, description, wikipedia_link)

    def _common_usages(self):
        """Return the usage entries present on every edited track."""
        common = set.intersection(*({_usage_key(u) for u in t.usages} for t in self.tracks))
        return [(None, usage_type, title, year or None, description or None, wiki or None) for usage_type, title, year, description, wiki in common]

    def _add_row(self, usage_id, usage_type, title, year, description, wikipedia_link):
        """Append one read-only usage row with a Remove button."""
        row = self._table.rowCount()
        self._table.insertRow(row)

        type_item = QTableWidgetItem(usage_type or "")
        type_item.setFlags(type_item.flags() & ~Qt.ItemIsEditable)
        type_item.setData(Qt.UserRole, usage_id)
        type_item.setData(Qt.UserRole + 1, (usage_type, title, year or 0, description or "", wikipedia_link or ""))
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
        """Add the typed usage to every edited track."""
        usage_type = self._type_combo.currentText()
        title = self._title_edit.text().strip()
        year = self._year_spin.value() or None
        description = self._description_edit.text().strip() or None
        wikipedia_link = self._wiki_edit.text().strip() or None

        if not title:
            QMessageBox.warning(self, "Input Required", "Please enter a title.")
            return

        rows = [{"track_id": track.track_id, "usage_type": usage_type, "title": title, "year": year, "description": description, "wikipedia_link": wikipedia_link} for track in self.tracks]

        # add_entities catches its own DB errors and returns [] on failure.
        if not self.controller.add.add_entities("TrackUsage", rows):
            logger.error(f"Failed to add TrackUsage {title!r} to tracks {[r['track_id'] for r in rows]}")
            QMessageBox.warning(self, "Error", "Could not add the entry. See the log for details.")
            return

        self._title_edit.clear()
        self._year_spin.setValue(0)
        self._description_edit.clear()
        self._wiki_edit.clear()

        self._invalidate_usages_cache()
        self.load(self.tracks)

    def _remove_row(self, row: int):
        """Remove the usage in table row `row` (from every edited track in multi mode)."""
        type_item = self._table.item(row, 0)
        if not type_item:
            return

        usage_id = type_item.data(Qt.UserRole)
        if usage_id is not None:
            usage_ids = [usage_id]
        else:
            # Multi-track row: each track owns its own copy; match the full key, as _common_usages does.
            key = type_item.data(Qt.UserRole + 1)
            usage_ids = [u.usage_id for t in self.tracks for u in t.usages if _usage_key(u) == key]
        if usage_ids and not self.controller.delete.delete_entity("TrackUsage", entity_ids=usage_ids):
            logger.error(f"Failed to remove TrackUsage row(s) {usage_ids}")
            QMessageBox.warning(self, "Error", "Could not remove the entry. See the log for details.")
            return

        self._invalidate_usages_cache()
        self.load(self.tracks)

    def _invalidate_usages_cache(self):
        """Expire the cached track.usages relationship on every edited track."""
        # expire_on_commit=False: a commit does not refresh the cached relationship.
        session = self.controller.get.session
        for track in self.tracks:
            session.expire(track, ["usages"])
