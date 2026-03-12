# ---------------------------------------------------------------------------
# AwardsTab
# ---------------------------------------------------------------------------
from __future__ import annotations


from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QHeaderView,
    QTableWidgetItem,
    QTableWidget,
    QComboBox,
)

from src.logger_config import logger
from src.track_edit_basetab import _BaseTab


class AwardsTab(_BaseTab):
    def __init__(self, tracks: list, controller, parent=None):
        super().__init__(tracks, controller, parent)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        search_row = QHBoxLayout()
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search awards… (min 2 chars)")
        self._search.textChanged.connect(self._on_search)
        search_row.addWidget(self._search)

        self._combo = QComboBox()
        self._combo.setVisible(False)
        self._combo.currentIndexChanged.connect(self._on_selected)
        search_row.addWidget(self._combo)

        self._cat_edit = QLineEdit()
        self._cat_edit.setPlaceholderText("Category (optional)")
        search_row.addWidget(self._cat_edit)

        self._year_spin = QSpinBox()
        self._year_spin.setRange(0, 2200)
        self._year_spin.setSpecialValueText("Year")
        search_row.addWidget(self._year_spin)

        self._add_btn = QPushButton("Add Award")
        self._add_btn.setEnabled(False)
        self._add_btn.clicked.connect(self._add)
        search_row.addWidget(self._add_btn)
        layout.addLayout(search_row)

        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(["Award", "Category", "Year", ""])
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeToContents
        )
        self._table.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.ResizeToContents
        )
        layout.addWidget(self._table)

    def load(self, tracks: list) -> None:
        self.tracks = tracks
        self._table.setRowCount(0)
        if self.is_multi:
            rows = self._common_awards()
        else:
            assocs = self.controller.get.get_entity_links(
                "AwardAssociation", entity_id=self.track.track_id, entity_type="Track"
            )
            rows = []
            for a in assocs:
                award = self.controller.get.get_entity_object(
                    "Award", award_id=a.award_id
                )
                if award:
                    rows.append(
                        (award.award_id, award.award_name, a.category or "", a.year)
                    )
        for award_id, award_name, category, year in rows:
            self._add_row(award_id, award_name, category, year)

    def _common_awards(self):
        all_sets = []
        for t in self.tracks:
            s = set()
            assocs = self.controller.get.get_entity_links(
                "AwardAssociation", entity_id=t.track_id, entity_type="Track"
            )
            for a in assocs:
                award = self.controller.get.get_entity_object(
                    "Award", award_id=a.award_id
                )
                if award:
                    s.add(
                        (
                            award.award_id,
                            award.award_name,
                            a.category or "",
                            a.year or 0,
                        )
                    )
            all_sets.append(s)
        common = all_sets[0]
        for s in all_sets[1:]:
            common &= s
        return [
            (aid, aname, cat, yr if yr != 0 else None) for aid, aname, cat, yr in common
        ]

    def _add_row(self, award_id, award_name, category, year):
        row = self._table.rowCount()
        self._table.insertRow(row)
        ai = QTableWidgetItem(award_name)
        ai.setData(Qt.UserRole, award_id)
        self._table.setItem(row, 0, ai)
        self._table.setItem(row, 1, QTableWidgetItem(category))
        self._table.setItem(row, 2, QTableWidgetItem(str(year) if year else ""))
        btn = QPushButton("Remove")
        btn.clicked.connect(lambda _c, r=row: self._remove_row(r))
        self._table.setCellWidget(row, 3, btn)

    def _on_search(self, text: str):
        text = text.strip()
        self._combo.blockSignals(True)
        self._combo.clear()
        if len(text) >= 2:
            results = self.controller.get.get_entity_object("Award", award_name=text)
            self._combo.addItem(f"Create new: '{text}'", "new")
            if results is not None:
                items = results if isinstance(results, list) else [results]
                for a in items:
                    self._combo.addItem(a.award_name, a.award_id)
            self._combo.setVisible(self._combo.count() > 1)
        else:
            self._combo.setVisible(False)
        self._combo.blockSignals(False)
        self._add_btn.setEnabled(len(text) >= 2)

    def _on_selected(self, index: int):
        if index > 0:
            self._search.blockSignals(True)
            self._search.setText(self._combo.currentText())
            self._search.blockSignals(False)

    def _add(self):
        award_name = self._search.text().strip()
        category = self._cat_edit.text().strip() or None
        year = self._year_spin.value() or None
        if not award_name:
            return
        combo_data = self._combo.currentData() if self._combo.isVisible() else None
        if combo_data and combo_data != "new":
            award = self.controller.get.get_entity_object("Award", award_id=combo_data)
        else:
            existing = self.controller.get.get_entity_object(
                "Award", award_name=award_name
            )
            if existing:
                award = existing if not isinstance(existing, list) else existing[0]
            else:
                award = self.controller.add.add_entity("Award", award_name=award_name)
        if not award:
            return
        for track in self.tracks:
            try:
                self.controller.add.add_entity(
                    "AwardAssociation",
                    entity_id=track.track_id,
                    entity_type="Track",
                    award_id=award.award_id,
                    category=category,
                    year=year,
                )
            except Exception as e:
                logger.error(f"Failed to add award to track {track.track_id}: {e}")
        self._search.clear()
        self._cat_edit.clear()
        self._year_spin.setValue(0)
        self._combo.setVisible(False)
        self.load(self.tracks)

    def _remove_row(self, row: int):
        award_item = self._table.item(row, 0)
        if not award_item:
            return
        award_id = award_item.data(Qt.UserRole)
        for track in self.tracks:
            try:
                self.controller.delete.delete_entity(
                    "AwardAssociation",
                    entity_id=track.track_id,
                    entity_type="Track",
                    award_id=award_id,
                )
            except Exception as e:
                logger.error(f"Failed to remove award from track {track.track_id}: {e}")
        self.load(self.tracks)
