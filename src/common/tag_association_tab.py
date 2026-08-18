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

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)
from sqlalchemy.exc import SQLAlchemyError

from src.common.entity_completer_edit import (
    build_entity_search_widget,
    find_or_create_by_name,
    get_cached_entities,
    register_cached_entity,
)
from src.core.logger_config import logger
from src.track.track_edit_basetab import _BaseTab


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
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        search_row = QHBoxLayout()
        self._search = build_entity_search_widget(
            self.controller,
            self.model_name,
            self.name_field,
            self.id_field,
            self.placeholder_text,
        )
        self._search.textChanged.connect(self._on_search_text_changed)
        self._search.returnPressed.connect(self._add)
        search_row.addWidget(self._search)

        self._add_btn = QPushButton(self.add_button_text)
        self._add_btn.setEnabled(False)
        self._add_btn.clicked.connect(self._add)
        search_row.addWidget(self._add_btn)
        layout.addLayout(search_row)

        self._list = QListWidget()
        self._list.setSelectionMode(QListWidget.ExtendedSelection)
        layout.addWidget(self._list)

    def _known_entities(self) -> list:
        """Candidate set for _find_or_create's case-insensitive duplicate
        check: the full cached table when small enough to preload, or the
        bounded search widget's last on-demand query otherwise."""
        cached = get_cached_entities(self.controller, self.model_name)
        if cached is not None:
            return cached
        return self._search.known_matches()

    def _on_search_text_changed(self, text: str):
        self._add_btn.setEnabled(bool(text.strip()))

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

    def _invalidate_cache(self) -> None:
        """Hook for subclasses whose `_load_track_items` reads a cached ORM
        relationship instead of querying fresh (see GenresTab). The base
        implementation here (get_entity_links) always queries fresh, so
        this is a no-op by default; override wherever a relationship
        attribute could have already been loaded and cached on `self.track`
        objects before the add/remove below writes past it -- the session
        is opened with expire_on_commit=False, so commit() alone won't
        invalidate it (see src/db/db_engine.py)."""

    def _find_or_create(self, name: str):
        return find_or_create_by_name(
            self.controller,
            self.model_name,
            self.name_field,
            name,
            self._known_entities(),
        )

    def _add(self):
        names = self._search.split_names()
        if not names:
            return

        # matched_id only names a single typed entry -- with several typed
        # at once (e.g. "Rock;Pop") each is resolved by name instead of
        # relying on that one-shot completer pick.
        single_matched_id = self._search.matched_id() if len(names) == 1 else None

        entities = []
        try:
            for name in names:
                if single_matched_id is not None:
                    entity = self.controller.get.get_entity_object(
                        self.model_name, **{self.id_field: single_matched_id}
                    )
                else:
                    entity = self._find_or_create(name)
                if entity:
                    entities.append((name, entity))
        except SQLAlchemyError as e:
            logger.error(f"Failed to find/create {self.model_name}: {e}")
            return
        if not entities:
            return

        rows = [
            {"track_id": track.track_id, self.id_field: getattr(entity, self.id_field)}
            for _name, entity in entities
            for track in self.tracks
        ]
        try:
            _, failed = self.controller.add.add_entities_with_fallback(
                self.assoc_model, rows
            )
        except SQLAlchemyError as e:
            logger.error(f"Failed to add {self.model_name} to tracks: {e}")
            failed = []
        if failed:
            bad_track_ids = ", ".join(
                dict.fromkeys(str(row["track_id"]) for row in failed)
            )
            logger.warning(
                f"Failed to tag {len(failed)} track(s) with {self.model_name}: "
                f"track_id(s) {bad_track_ids}"
            )
            QMessageBox.warning(
                self,
                "Some tracks not updated",
                f"Could not add to {len(failed)} of {len(rows)} track(s) "
                f"(track_id(s) {bad_track_ids}). They may have been deleted or "
                f"changed since this tab was opened; try closing and reopening it.",
            )

        if single_matched_id is None:
            for _name, entity in entities:
                entity_id = getattr(entity, self.id_field)
                display = getattr(entity, self.name_field, None)
                if display:
                    # Deferred: _add() can run nested inside EntityCompleterEdit's
                    # own keyPressEvent (Enter -> returnPressed fires *during*
                    # that native call). add_to_index() replaces the QCompleter
                    # object in place, and doing that while Qt's own key handling
                    # is still mid-execution on that same completer corrupts its
                    # internals and crashes the process. See artist_edit_types.py
                    # _flush_new_chip_paint() for the same hazard.
                    search = self._search
                    QTimer.singleShot(0, lambda d=display, i=entity_id: search.add_to_index(d, i))
                register_cached_entity(self.model_name, entity)

        self._search.reset()
        self._invalidate_cache()
        self.load(self.tracks)

    def _remove_selected(self):
        items = self._list.selectedItems()
        if not items:
            return
        track_ids = [track.track_id for track in self.tracks]
        for item in items:
            entity_id = item.data(Qt.UserRole)
            try:
                self.controller.delete.delete_entity(
                    self.assoc_model, track_id=track_ids, **{self.id_field: entity_id}
                )
            except SQLAlchemyError as e:
                logger.error(f"Failed to remove {self.model_name} from tracks: {e}")
        self._invalidate_cache()
        self.load(self.tracks)

    def contextMenuEvent(self, event):
        if self._list.selectedItems():
            menu = QMenu(self)
            menu.addAction("Remove", self._remove_selected)
            menu.exec(event.globalPos())
