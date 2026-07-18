"""Generic base for track-association tabs (genres, moods): a QListWidget
of "this track is tagged with X" rows backed by a simple {track_id, x_id}
association table, with search/add/remove and a context menu.

Covers the Genre/Mood shape specifically -- both use QListWidget + a
context menu, over a track_id-keyed association row. Places and Awards have
extra per-association fields and a polymorphic entity_id/entity_type key,
so they stay on their own bespoke tab implementations rather than being
forced into this shape.
"""

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

from src.common.entity_completer_edit import find_or_create_by_name
from src.core.logger_config import logger
from src.track.track_edit_basetab import _BaseTab

# Search results are capped and fetched on demand (min 2 chars) instead of
# preloading every row of the model's table into a completer index on tab
# open -- see track_edit_samples.py for the tab that made this expensive
# with the library's Track table; Genre/Mood are small today but would hit
# the same wall as they grow.
_MAX_SEARCH_RESULTS = 50


class _BaseTrackAssociationTab(_BaseTab):
    """
    Subclasses must set these class attributes:
      model_name        -- e.g. "Genre"
      id_field           -- e.g. "genre_id"
      name_field         -- e.g. "genre_name"
      assoc_model        -- e.g. "TrackGenre"
      placeholder_text   -- e.g. "Search genres…"
      add_button_text    -- e.g. "Add Genre"

    Optionally override `_load_track_items` (default: get_entity_links +
    get_entity_object) for a faster ORM-relationship shortcut, and
    `_find_or_create` to add an extra lookup step before creating.
    """

    model_name: str = ""
    id_field: str = ""
    name_field: str = ""
    assoc_model: str = ""
    placeholder_text: str = ""
    add_button_text: str = "Add"

    def __init__(self, tracks: list, controller, parent=None):
        super().__init__(tracks, controller, parent)
        self._known_entities: list = []
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        search_row = QHBoxLayout()
        self._search = QLineEdit()
        self._search.setPlaceholderText(f"{self.placeholder_text} (min 2 chars)")
        self._search.textChanged.connect(self._on_search_text_changed)
        self._search.returnPressed.connect(self._add)
        search_row.addWidget(self._search)

        self._combo = QComboBox()
        self._combo.setVisible(False)
        self._combo.currentIndexChanged.connect(self._on_combo_selected)
        search_row.addWidget(self._combo)

        self._add_btn = QPushButton(self.add_button_text)
        self._add_btn.setEnabled(False)
        self._add_btn.clicked.connect(self._add)
        search_row.addWidget(self._add_btn)
        layout.addLayout(search_row)

        self._list = QListWidget()
        layout.addWidget(self._list)

    def _search_entities(self, text: str):
        """Bounded, on-demand lookup of entities whose name contains
        `text` -- used both for the search dropdown and as the
        duplicate-check candidate set at add time (an exact match, if one
        exists, always contains the searched text as a substring)."""
        try:
            matches = (
                self.controller.get.get_all_entities(
                    self.model_name, **{f"{self.name_field}__contains": text}
                )
                or []
            )
            return matches[:_MAX_SEARCH_RESULTS]
        except Exception as e:
            logger.warning(f"Could not search {self.model_name}: {e}")
            return []

    def _on_search_text_changed(self, text: str):
        text = text.strip()
        self._add_btn.setEnabled(bool(text))
        self._combo.blockSignals(True)
        self._combo.clear()
        if len(text) >= 2:
            for e in self._search_entities(text):
                name = getattr(e, self.name_field, None)
                if name:
                    self._combo.addItem(name, getattr(e, self.id_field))
            self._combo.setVisible(self._combo.count() > 0)
        else:
            self._combo.setVisible(False)
        self._combo.blockSignals(False)

    def _on_combo_selected(self, index: int):
        if index >= 0:
            self._search.blockSignals(True)
            self._search.setText(self._combo.currentText())
            self._search.blockSignals(False)

    def _load_track_items(self, track):
        """Return [(id, name), ...] tagged on a single track."""
        assocs = self.controller.get.get_entity_links(
            self.assoc_model, track_id=track.track_id
        )
        items = []
        for a in assocs:
            entity_id = getattr(a, self.id_field)
            entity = self.controller.get.get_entity_object(
                self.model_name, **{self.id_field: entity_id}
            )
            if entity:
                items.append(
                    (getattr(entity, self.id_field), getattr(entity, self.name_field))
                )
        return items

    def load(self, tracks: list) -> None:
        self.tracks = tracks
        self._list.clear()
        if self.is_multi:
            items = self._common_items()
        else:
            items = self._load_track_items(self.track)
        for entity_id, name in items:
            item = QListWidgetItem(name)
            item.setData(Qt.UserRole, entity_id)
            self._list.addItem(item)

    def _common_items(self):
        all_sets = [set(self._load_track_items(t)) for t in self.tracks]
        common = all_sets[0]
        for s in all_sets[1:]:
            common &= s
        return list(common)

    def _find_or_create(self, name: str):
        return find_or_create_by_name(
            self.controller,
            self.model_name,
            self.name_field,
            name,
            self._known_entities,
        )

    def _add(self):
        name = self._search.text().strip()
        if not name:
            return

        combo_data = self._combo.currentData() if self._combo.isVisible() else None
        # Bounded candidate set for _find_or_create's duplicate check --
        # fetched fresh here rather than kept as a stale full-table cache.
        self._known_entities = self._search_entities(name)
        try:
            if combo_data is not None:
                entity = self.controller.get.get_entity_object(
                    self.model_name, **{self.id_field: combo_data}
                )
            else:
                entity = self._find_or_create(name)
        except Exception as e:
            logger.error(f"Failed to find/create {self.model_name}: {e}")
            return
        if not entity:
            return

        entity_id = getattr(entity, self.id_field)
        rows = [
            {"track_id": track.track_id, self.id_field: entity_id}
            for track in self.tracks
        ]
        try:
            self.controller.add.add_entities(self.assoc_model, rows)
        except Exception as e:
            logger.error(f"Failed to add {self.model_name} to tracks: {e}")
        self._search.clear()
        self._combo.blockSignals(True)
        self._combo.clear()
        self._combo.setVisible(False)
        self._combo.blockSignals(False)
        self.load(self.tracks)

    def _remove_selected(self):
        item = self._list.currentItem()
        if not item:
            return
        entity_id = item.data(Qt.UserRole)
        track_ids = [track.track_id for track in self.tracks]
        try:
            self.controller.delete.delete_entity(
                self.assoc_model, track_id=track_ids, **{self.id_field: entity_id}
            )
        except Exception as e:
            logger.error(f"Failed to remove {self.model_name} from tracks: {e}")
        self.load(self.tracks)

    def contextMenuEvent(self, event):
        if self._list.currentItem():
            menu = QMenu(self)
            menu.addAction("Remove", self._remove_selected)
            menu.exec(event.globalPos())
