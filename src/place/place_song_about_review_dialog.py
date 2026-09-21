"""Tools-menu dialog for reviewing lyric-detected "Song About" place
candidates queued by src/mood/mood_autotag.py.

Lyric place detection (src/lyrics/place_matching.py) has no way to tell a
real "song about" place from a common word or name that happens to match a
place already in the library (e.g. "Bath", "England", a city name that's
also a person's name) -- so a fresh detection is queued here instead of
being written straight to place_associations. Approve, Change, or Reject
applies to every currently-queued track for that place name at once, and is
remembered in config/place_song_about_decisions.json so the same place name
resolves automatically -- without asking again -- on every later detection.
"""

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from sqlalchemy.exc import SQLAlchemyError

from src.common.widgets.entity_completer_context import place_context_map
from src.common.widgets.entity_completer_edit import build_entity_search_widget
from src.foundation.logger_config import logger
from src.foundation.status_utility import show_status_message
from src.mood.mood_autotag import SONG_ABOUT_TYPE_NAME
from src.place import place_song_about_store
from src.place.place_association_types import (
    fetch_association_types,
    find_or_create_association_type,
)


class _ChangePlaceDialog(QDialog):
    """Small modal: pick the place a queued detection should actually
    resolve to, in place of the one lyric matching picked."""

    def __init__(self, controller, original_name: str, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.setWindowTitle(f'Change Place for "{original_name}"')
        self.setMinimumWidth(360)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f'Replace "{original_name}" with which place?'))

        self._search = build_entity_search_widget(
            controller,
            "Place",
            "place_name",
            "place_id",
            "Search places…",
            context_builder=place_context_map,
        )
        self._search.returnPressed.connect(self.accept)
        layout.addWidget(self._search)

        button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

    def resolve_place(self):
        """Return the chosen Place row, finding or creating it by name.
        None if nothing usable was typed."""
        names = self._search.split_names()
        if not names:
            return None
        name = names[0]
        matched_id = self._search.matched_id()
        if matched_id is not None:
            return self.controller.get.get_entity_object("Place", place_id=matched_id)
        known = self.controller.get.get_all_entities("Place") or []
        for place in known:
            if (place.place_name or "").strip().lower() == name.strip().lower():
                return place
        return self.controller.add.add_entity("Place", place_name=name)


class PlaceSongAboutReviewDialog(QDialog):
    """Tools-menu dialog listing pending "Song About" place detections,
    grouped by place name, for a one-click Approve/Change/Reject that
    applies to every track currently queued under that name."""

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self._song_about_type_id = None
        self.setWindowTitle("Review Song-About Places")
        self.setMinimumWidth(640)
        self.setMinimumHeight(420)
        self._build_ui()
        self.refresh()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(
                "Places lyric-detected in your tracks' lyrics but not yet confirmed. "
                "Approve, Change, or Reject a place once -- your choice is remembered, so "
                "the same place name never needs reviewing again."
            )
        )

        top_row = QHBoxLayout()
        self._status_label = QLabel("")
        top_row.addWidget(self._status_label)
        top_row.addStretch()
        self._refresh_btn = QPushButton("Refresh")
        self._refresh_btn.clicked.connect(self.refresh)
        top_row.addWidget(self._refresh_btn)
        layout.addLayout(top_row)

        self._table = QTableWidget(0, 3)
        self._table.setHorizontalHeaderLabels(["Place", "Tracks", ""])
        self._table.horizontalHeader().setStretchLastSection(False)
        self._table.setColumnWidth(0, 220)
        self._table.setColumnWidth(1, 100)
        layout.addWidget(self._table)

        button_box = QDialogButtonBox(QDialogButtonBox.Close)
        button_box.rejected.connect(self.reject)
        button_box.accepted.connect(self.accept)
        layout.addWidget(button_box)

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def refresh(self):
        queue = place_song_about_store.load_queue()
        grouped: dict[str, list] = {}
        for entry in queue:
            grouped.setdefault(entry["place_name"], []).append(entry)

        self._table.setRowCount(0)
        for place_name in sorted(grouped.keys(), key=str.lower):
            self._add_row(place_name, grouped[place_name])

        count = len(grouped)
        self._status_label.setText(
            f"{count} place{'s' if count != 1 else ''} awaiting review"
            if count
            else "Nothing to review."
        )

    def _add_row(self, place_name: str, entries: list):
        row = self._table.rowCount()
        self._table.insertRow(row)
        self._table.setItem(row, 0, QTableWidgetItem(place_name))
        self._table.setItem(row, 1, QTableWidgetItem(str(len(entries))))
        self._table.setCellWidget(row, 2, self._build_action_cell(place_name))

    def _build_action_cell(self, place_name: str) -> QWidget:
        cell = QWidget()
        row_layout = QHBoxLayout(cell)
        row_layout.setContentsMargins(4, 2, 4, 2)

        approve_btn = QPushButton("Approve")
        approve_btn.clicked.connect(lambda _c, name=place_name: self._approve(name))
        row_layout.addWidget(approve_btn)

        change_btn = QPushButton("Change…")
        change_btn.clicked.connect(lambda _c, name=place_name: self._change(name))
        row_layout.addWidget(change_btn)

        reject_btn = QPushButton("Reject")
        reject_btn.clicked.connect(lambda _c, name=place_name: self._reject(name))
        row_layout.addWidget(reject_btn)

        return cell

    # ------------------------------------------------------------------
    # Decisions
    # ------------------------------------------------------------------

    def _song_about_type(self):
        if self._song_about_type_id is None:
            known_types = fetch_association_types(self.controller)
            song_about = find_or_create_association_type(
                self.controller, SONG_ABOUT_TYPE_NAME, known_types
            )
            self._song_about_type_id = song_about.association_type_id if song_about else None
        return self._song_about_type_id

    def _write_associations(self, entries: list, place_id: int) -> int:
        """Write a PlaceAssociation for every entry not already linked to
        `place_id`. Returns how many rows were written."""
        type_id = self._song_about_type()
        if type_id is None:
            return 0
        rows = []
        for entry in entries:
            track_id = entry["track_id"]
            existing = self.controller.get.get_entity_links(
                "PlaceAssociation", entity_id=track_id, entity_type="Track"
            )
            if any(a.place_id == place_id for a in existing):
                continue
            rows.append(
                {
                    "place_id": place_id,
                    "entity_id": track_id,
                    "entity_type": "Track",
                    "association_type_id": type_id,
                }
            )
        if rows:
            self.controller.add.add_entities("PlaceAssociation", rows)
        return len(rows)

    def _approve(self, place_name: str):
        entries = place_song_about_store.load_queue()
        entries = [e for e in entries if e["place_name"] == place_name]
        if not entries:
            return
        place_id = entries[0]["place_id"]
        try:
            written = self._write_associations(entries, place_id)
        except SQLAlchemyError as e:
            logger.error(f"Failed to approve place '{place_name}': {e}")
            QMessageBox.critical(self, "Error", f"Failed to save place association: {e}")
            return
        place_song_about_store.save_decision(place_name, place_song_about_store.DECISION_APPROVED)
        place_song_about_store.remove_place_from_queue(place_name)
        show_status_message(self, f'Approved "{place_name}" for {written} track(s).')
        self.refresh()

    def _reject(self, place_name: str):
        place_song_about_store.save_decision(place_name, place_song_about_store.DECISION_REJECTED)
        removed = place_song_about_store.remove_place_from_queue(place_name)
        show_status_message(self, f'Rejected "{place_name}" ({len(removed)} track(s)).')
        self.refresh()

    def _change(self, place_name: str):
        dialog = _ChangePlaceDialog(self.controller, place_name, self)
        if dialog.exec_() != QDialog.Accepted:
            return
        try:
            new_place = dialog.resolve_place()
        except SQLAlchemyError as e:
            logger.error(f"Failed to resolve replacement place for '{place_name}': {e}")
            QMessageBox.critical(self, "Error", f"Failed to look up place: {e}")
            return
        if new_place is None:
            return

        entries = place_song_about_store.load_queue()
        entries = [e for e in entries if e["place_name"] == place_name]
        if not entries:
            return
        try:
            written = self._write_associations(entries, new_place.place_id)
        except SQLAlchemyError as e:
            logger.error(f"Failed to change place '{place_name}': {e}")
            QMessageBox.critical(self, "Error", f"Failed to save place association: {e}")
            return
        place_song_about_store.save_decision(
            place_name,
            place_song_about_store.DECISION_REMAPPED,
            place_id=new_place.place_id,
            remap_place_name=new_place.place_name,
        )
        place_song_about_store.remove_place_from_queue(place_name)
        show_status_message(
            self, f'Changed "{place_name}" to "{new_place.place_name}" for {written} track(s).'
        )
        self.refresh()
