# ---------------------------------------------------------------------------
# GenresTab
# ---------------------------------------------------------------------------
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QVBoxLayout,
)

from src.logger_config import logger
from src.track_edit_basetab import _BaseTab


class GenresTab(_BaseTab):
    def __init__(self, tracks: list, controller, parent=None):
        super().__init__(tracks, controller, parent)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        search_row = QHBoxLayout()
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search genres… (min 2 chars)")
        self._search.textChanged.connect(self._on_search)
        search_row.addWidget(self._search)

        self._combo = QComboBox()
        self._combo.setVisible(False)
        self._combo.currentIndexChanged.connect(self._on_selected)
        search_row.addWidget(self._combo)

        self._add_btn = QPushButton("Add Genre")
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
            genres = self._common_genres()
        else:
            genres = [(g.genre_id, g.genre_name) for g in self.track.genres]
        for gid, gname in genres:
            item = QListWidgetItem(gname)
            item.setData(Qt.UserRole, gid)
            self._list.addItem(item)

    def _common_genres(self):
        all_sets = []
        for t in self.tracks:
            all_sets.append({(g.genre_id, g.genre_name) for g in t.genres})
        common = all_sets[0]
        for s in all_sets[1:]:
            common &= s
        return list(common)

    def _on_search(self, text: str):
        text = text.strip()
        self._combo.blockSignals(True)
        self._combo.clear()
        if len(text) >= 2:
            results = self.controller.get.get_entity_object("Genre", genre_name=text)
            self._combo.addItem(f"Create new: '{text}'", "new")
            if results is not None:
                items = results if isinstance(results, list) else [results]
                for g in items:
                    self._combo.addItem(g.genre_name, g.genre_id)
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
        genre_name = self._search.text().strip()
        if not genre_name:
            return
        combo_data = self._combo.currentData() if self._combo.isVisible() else None
        if combo_data and combo_data != "new":
            genre = self.controller.get.get_entity_object("Genre", genre_id=combo_data)
        else:
            existing = self.controller.get.get_entity_object(
                "Genre", genre_name=genre_name
            )
            if existing:
                genre = existing if not isinstance(existing, list) else existing[0]
            else:
                genre = self.controller.add.add_entity("Genre", genre_name=genre_name)
        if not genre:
            return
        for track in self.tracks:
            try:
                self.controller.add.add_entity(
                    "TrackGenre", track_id=track.track_id, genre_id=genre.genre_id
                )
            except Exception as e:
                logger.error(f"Failed to add genre to track {track.track_id}: {e}")
        self._search.clear()
        self._combo.setVisible(False)
        self.load(self.tracks)

    def _remove_selected(self):
        item = self._list.currentItem()
        if not item:
            return
        genre_id = item.data(Qt.UserRole)
        for track in self.tracks:
            try:
                self.controller.delete.delete_entity(
                    "TrackGenre", track_id=track.track_id, genre_id=genre_id
                )
            except Exception as e:
                logger.error(f"Failed to remove genre from track {track.track_id}: {e}")
        self.load(self.tracks)

    def contextMenuEvent(self, event):
        if self._list.currentItem():
            menu = QMenu(self)
            menu.addAction("Remove", self._remove_selected)
            menu.exec(event.globalPos())
