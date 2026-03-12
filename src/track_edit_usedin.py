# ---------------------------------------------------------------------------
# UsedInTab — display contexts where this track has been used
# ---------------------------------------------------------------------------
from __future__ import annotations


from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from src.logger_config import logger
from src.track_edit_basetab import _BaseTab


class UsedInTab(_BaseTab):
    """
    Shows a read-only table of TrackUsedIn records for this track.

    The TrackUsedIn table is described in bugs.txt as a planned feature:
      "implement 'used in' feature for tracks, showing contexts where the
       track was used — Soundtracks, events, etc."

    This tab gracefully handles the case where the table / relationship
    does not exist yet: it shows a friendly "not yet available" message
    instead of crashing.

    When the feature IS available, the track object is expected to expose
    a `used_in` relationship (list of objects with at least these attrs):
        context_type  — e.g. "Soundtrack", "Event", "Commercial"
        context_name  — e.g. "The Matrix", "Glastonbury 2005"
        year          — optional int
        notes         — optional str

    Add / Remove are also supported when the controller supports
    add_entity("TrackUsedIn", ...) and delete_entity("TrackUsedIn", ...).
    """

    def __init__(self, tracks: list, controller, parent=None):
        super().__init__(tracks, controller, parent)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        # ── Add row ───────────────────────────────────────────────────────
        add_row = QHBoxLayout()

        self._ctx_type = QLineEdit()
        self._ctx_type.setPlaceholderText(
            "Context type (Soundtrack, Event, Commercial…)"
        )
        add_row.addWidget(self._ctx_type)

        self._ctx_name = QLineEdit()
        self._ctx_name.setPlaceholderText("Context name (e.g. The Matrix)")
        add_row.addWidget(self._ctx_name)

        self._ctx_year = QSpinBox()
        self._ctx_year.setRange(0, 2200)
        self._ctx_year.setSpecialValueText("Year")
        add_row.addWidget(self._ctx_year)

        self._add_btn = QPushButton("Add")
        self._add_btn.clicked.connect(self._add_entry)
        add_row.addWidget(self._add_btn)
        layout.addLayout(add_row)

        # ── Table ─────────────────────────────────────────────────────────
        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(
            ["Context Type", "Context Name", "Year", ""]
        )
        self._table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeToContents
        )
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeToContents
        )
        self._table.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.ResizeToContents
        )
        layout.addWidget(self._table)

        self._status_label = QLabel("")
        self._status_label.setStyleSheet("color: #888; font-style: italic;")
        layout.addWidget(self._status_label)

    def load(self, tracks: list) -> None:
        self.tracks = tracks
        self._table.setRowCount(0)
        self._status_label.setText("")

        if self.is_multi:
            self._status_label.setText(
                "(Select a single track to view 'Used In' entries)"
            )
            self._add_btn.setEnabled(False)
            return

        self._add_btn.setEnabled(True)

        # Try to read used_in from the track object
        used_in_list = getattr(self.track, "used_in", None)
        if used_in_list is None:
            self._status_label.setText(
                "The 'Used In' feature is not yet available. "
                "It will appear here once the database table has been created."
            )
            self._add_btn.setEnabled(False)
            return

        for entry in used_in_list:
            self._add_table_row(entry)

        if self._table.rowCount() == 0:
            self._status_label.setText("No 'Used In' entries recorded for this track.")

    def _add_table_row(self, entry):
        row = self._table.rowCount()
        self._table.insertRow(row)

        ctx_type = getattr(entry, "context_type", "") or ""
        ctx_name = getattr(entry, "context_name", "") or ""
        year = getattr(entry, "year", None)
        entry_id = getattr(entry, "used_in_id", None)

        type_item = QTableWidgetItem(ctx_type)
        type_item.setFlags(type_item.flags() & ~Qt.ItemIsEditable)
        self._table.setItem(row, 0, type_item)

        name_item = QTableWidgetItem(ctx_name)
        name_item.setFlags(name_item.flags() & ~Qt.ItemIsEditable)
        name_item.setData(Qt.UserRole, entry_id)
        self._table.setItem(row, 1, name_item)

        year_item = QTableWidgetItem(str(year) if year else "")
        year_item.setFlags(year_item.flags() & ~Qt.ItemIsEditable)
        self._table.setItem(row, 2, year_item)

        rm_btn = QPushButton("Remove")
        rm_btn.clicked.connect(lambda _c, eid=entry_id: self._remove_entry(eid))
        self._table.setCellWidget(row, 3, rm_btn)

    def _add_entry(self):
        ctx_type = self._ctx_type.text().strip()
        ctx_name = self._ctx_name.text().strip()
        year = self._ctx_year.value() or None

        if not ctx_name:
            QMessageBox.warning(self, "Input Required", "Please enter a context name.")
            return

        try:
            self.controller.add.add_entity(
                "TrackUsedIn",
                track_id=self.track.track_id,
                context_type=ctx_type or None,
                context_name=ctx_name,
                year=year,
            )
        except Exception as e:
            logger.error(f"Failed to add UsedIn entry: {e}")
            QMessageBox.warning(self, "Error", f"Failed to add entry:\n{e}")
            return

        self._ctx_type.clear()
        self._ctx_name.clear()
        self._ctx_year.setValue(0)

        updated = self.controller.get.get_entity_object(
            "Track", track_id=self.track.track_id
        )
        if updated:
            self.tracks = [updated]
        self.load(self.tracks)

    def _remove_entry(self, entry_id):
        if entry_id is None:
            return
        try:
            self.controller.delete.delete_entity("TrackUsedIn", used_in_id=entry_id)
        except Exception as e:
            logger.error(f"Failed to remove UsedIn entry: {e}")
            QMessageBox.warning(self, "Error", f"Failed to remove entry:\n{e}")
            return

        updated = self.controller.get.get_entity_object(
            "Track", track_id=self.track.track_id
        )
        if updated:
            self.tracks = [updated]
        self.load(self.tracks)
