"""AwardsTab: link awards to the edited track(s)."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QHBoxLayout, QHeaderView, QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout

from src.common.widgets.entity_completer_edit import build_entity_search_widget, find_or_create_by_name, get_cached_entities, register_cached_entity
from src.foundation.logger_config import logger
from src.track.edit.track_edit_basetab import _BaseTab


def _read_only_item(text: str) -> QTableWidgetItem:
    """Return a table item the user cannot edit."""
    item = QTableWidgetItem(text)
    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
    return item


class AwardsTab(_BaseTab):
    """Link and unlink awards; category and year come from the award itself."""

    saves_immediately = True  # add/remove write to the DB at once

    def __init__(self, tracks: list, controller, parent=None):
        super().__init__(tracks, controller, parent)
        self._build_ui()

    def _build_ui(self):
        """Build the award search row and the linked-awards table."""
        layout = QVBoxLayout(self)

        search_row = QHBoxLayout()
        self._search = build_entity_search_widget(self.controller, "Award", "award_name", "award_id", "Search awards… (new names are created)")
        self._search.textChanged.connect(self._on_search_text_changed)
        self._search.returnPressed.connect(self._add)
        search_row.addWidget(self._search)

        self._add_btn = QPushButton("Link Award")
        self._add_btn.setEnabled(False)
        self._add_btn.clicked.connect(self._add)
        search_row.addWidget(self._add_btn)
        layout.addLayout(search_row)

        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(["Award", "Category", "Year", ""])
        self._table.verticalHeader().setVisible(False)
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        layout.addWidget(self._table)

    def _on_search_text_changed(self, text: str):
        """Enable "Link Award" when the search has text."""
        self._add_btn.setEnabled(bool(text.strip()))

    def _track_ids(self) -> list[int]:
        """Return the ids of the edited tracks."""
        return [t.track_id for t in self.tracks]

    def _associations(self) -> list:
        """Return every award association of the edited tracks, in one query."""
        return self.controller.get.get_entity_links("AwardAssociation", entity_type="Track", entity_id__in=self._track_ids()) or []

    def load(self, tracks: list) -> None:
        """Show the awards linked to every edited track."""
        self.tracks = tracks
        self._table.setRowCount(0)

        award_ids_by_track: dict[int, set[int]] = {tid: set() for tid in self._track_ids()}
        for assoc in self._associations():
            award_ids_by_track.setdefault(assoc.entity_id, set()).add(assoc.award_id)
        common_ids = set.intersection(*award_ids_by_track.values()) if award_ids_by_track else set()
        if not common_ids:
            return

        awards = self.controller.get.get_all_entities("Award", award_id__in=sorted(common_ids)) or []
        for award in sorted(awards, key=lambda a: (a.award_name or "").lower()):
            self._add_row(award)

    def _add_row(self, award) -> None:
        """Append one award row with a Remove button."""
        row = self._table.rowCount()
        self._table.insertRow(row)
        name_item = _read_only_item(award.award_name or "")
        name_item.setData(Qt.UserRole, award.award_id)
        self._table.setItem(row, 0, name_item)
        self._table.setItem(row, 1, _read_only_item(award.award_category or ""))
        self._table.setItem(row, 2, _read_only_item(str(award.award_year) if award.award_year else ""))
        btn = QPushButton("Remove")
        btn.setToolTip(f"Unlink '{award.award_name}' from the edited track(s)")
        btn.clicked.connect(lambda _c, aid=award.award_id: self._remove_award(aid))
        self._table.setCellWidget(row, 3, btn)

    def _resolve_award(self):
        """Return the award named in the search field: the completer pick, else find-or-create."""
        matched_id = self._search.matched_id()
        if matched_id is not None:
            return self.controller.get.get_entity_object("Award", award_id=matched_id)
        name = self._search.text().strip()
        if not name:
            return None
        cached = get_cached_entities(self.controller, "Award")
        known = cached if cached is not None else self._search.known_matches()
        award = find_or_create_by_name(self.controller, "Award", "award_name", name, known)
        if award is not None and award not in known:
            # Deferred: add_to_index() rebuilds the completer, which is unsafe mid key-dispatch (Enter).
            search, aname, aid = self._search, award.award_name, award.award_id
            QTimer.singleShot(0, lambda: search.add_to_index(aname, aid))
            register_cached_entity("Award", award)
        return award

    def _add(self):
        """Link the searched award to every edited track that does not have it yet."""
        if not self._search.text().strip():
            return
        award = self._resolve_award()
        if award is None:
            QMessageBox.warning(self, "Error", "Could not find or create the award.")
            return

        # AwardAssociation has a surrogate key, so add_entities cannot skip duplicates itself.
        linked = {a.entity_id for a in self._associations() if a.award_id == award.award_id}
        rows = [{"entity_id": tid, "entity_type": "Track", "award_id": award.award_id} for tid in self._track_ids() if tid not in linked]
        if rows and not self.controller.add.add_entities("AwardAssociation", rows):
            logger.error(f"Failed to link award {award.award_id} to tracks {[r['entity_id'] for r in rows]}")
            QMessageBox.warning(self, "Error", "Could not link the award. See the log for details.")
            return

        self._search.reset()
        self.load(self.tracks)

    def _remove_award(self, award_id: int):
        """Unlink one award from every edited track."""
        assoc_ids = [a.association_id for a in self._associations() if a.award_id == award_id]
        if assoc_ids and not self.controller.delete.delete_entity("AwardAssociation", entity_ids=assoc_ids):
            logger.error(f"Failed to unlink award {award_id} from tracks {self._track_ids()}")
            QMessageBox.warning(self, "Error", "Could not unlink the award. See the log for details.")
            return
        self.load(self.tracks)
