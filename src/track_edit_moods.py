# ---------------------------------------------------------------------------
# MoodsTab
# ---------------------------------------------------------------------------
from __future__ import annotations


from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QPushButton,
    QVBoxLayout,
    QMenu,
    QListWidgetItem,
    QComboBox,
)

from src.logger_config import logger
from src.track_edit_basetab import _BaseTab


class MoodsTab(_BaseTab):
    def __init__(self, tracks: list, controller, parent=None):
        super().__init__(tracks, controller, parent)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        search_row = QHBoxLayout()
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search moods… (min 2 chars)")
        self._search.textChanged.connect(self._on_search)
        search_row.addWidget(self._search)

        self._combo = QComboBox()
        self._combo.setVisible(False)
        self._combo.currentIndexChanged.connect(self._on_selected)
        search_row.addWidget(self._combo)

        self._add_btn = QPushButton("Add Mood")
        self._add_btn.setEnabled(False)
        self._add_btn.clicked.connect(self._add)
        search_row.addWidget(self._add_btn)
        layout.addLayout(search_row)

        self._list = QListWidget()
        layout.addWidget(self._list)

    def load(self, tracks: list) -> None:
        self.tracks = tracks
        self._list.clear()
        if self.is_multi:
            moods = self._common_moods()
        else:
            assocs = self.controller.get.get_entity_links(
                "MoodTrackAssociation", track_id=self.track.track_id
            )
            moods = []
            for a in assocs:
                mood = self.controller.get.get_entity_object("Mood", mood_id=a.mood_id)
                if mood:
                    moods.append((mood.mood_id, mood.mood_name))
        for mood_id, mood_name in moods:
            item = QListWidgetItem(mood_name)
            item.setData(Qt.UserRole, mood_id)
            self._list.addItem(item)

    def _common_moods(self):
        all_sets = []
        for t in self.tracks:
            s = set()
            assocs = self.controller.get.get_entity_links(
                "MoodTrackAssociation", track_id=t.track_id
            )
            for a in assocs:
                mood = self.controller.get.get_entity_object("Mood", mood_id=a.mood_id)
                if mood:
                    s.add((mood.mood_id, mood.mood_name))
            all_sets.append(s)
        common = all_sets[0]
        for s in all_sets[1:]:
            common &= s
        return list(common)

    def _on_search(self, text: str):
        text = text.strip()
        self._combo.blockSignals(True)
        self._combo.clear()
        if len(text) >= 2:
            results = self.controller.get.get_entity_object("Mood", mood_name=text)
            self._combo.addItem(f"Create new: '{text}'", "new")
            if results is not None:
                items = results if isinstance(results, list) else [results]
                for m in items:
                    self._combo.addItem(m.mood_name, m.mood_id)
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
        mood_name = self._search.text().strip()
        if not mood_name:
            return
        combo_data = self._combo.currentData() if self._combo.isVisible() else None
        if combo_data and combo_data != "new":
            mood = self.controller.get.get_entity_object("Mood", mood_id=combo_data)
        else:
            existing = self.controller.get.get_entity_object(
                "Mood", mood_name=mood_name
            )
            if existing:
                mood = existing if not isinstance(existing, list) else existing[0]
            else:
                mood = self.controller.add.add_entity("Mood", mood_name=mood_name)
        if not mood:
            return
        for track in self.tracks:
            try:
                self.controller.add.add_entity_link(
                    "MoodTrackAssociation",
                    track_id=track.track_id,
                    mood_id=mood.mood_id,
                )
            except Exception as e:
                logger.error(f"Failed to add mood to track {track.track_id}: {e}")
        self._search.clear()
        self._combo.setVisible(False)
        self.load(self.tracks)

    def _remove_selected(self):
        item = self._list.currentItem()
        if not item:
            return
        mood_id = item.data(Qt.UserRole)
        for track in self.tracks:
            try:
                self.controller.delete.delete_entity(
                    "MoodTrackAssociation", track_id=track.track_id, mood_id=mood_id
                )
            except Exception as e:
                logger.error(f"Failed to remove mood from track {track.track_id}: {e}")
        self.load(self.tracks)

    def contextMenuEvent(self, event):
        if self._list.currentItem():
            menu = QMenu(self)
            menu.addAction("Remove", self._remove_selected)
            menu.exec(event.globalPos())
