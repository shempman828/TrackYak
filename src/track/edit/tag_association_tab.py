"""Generic base for the Genres and Moods tabs: a list of tags backed by a {track_id, x_id} table."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QHBoxLayout, QListWidget, QListWidgetItem, QMenu, QMessageBox, QPushButton, QVBoxLayout

from src.common.widgets.entity_completer_edit import build_entity_search_widget, find_or_create_by_name, get_cached_entities, register_cached_entity
from src.foundation.logger_config import logger
from src.track.edit.track_edit_basetab import _BaseTab


class _BaseTrackAssociationTab(_BaseTab):
    """Search, add and remove tags of one kind on the edited track(s)."""

    # Subclasses set model_name ("Genre"), id_field ("genre_id"), name_field ("genre_name"),
    # assoc_model ("TrackGenre"), relationship ("genres" on Track), placeholder_text and add_button_text.
    model_name: str = ""
    id_field: str = ""
    name_field: str = ""
    assoc_model: str = ""
    relationship: str = ""
    placeholder_text: str = ""
    add_button_text: str = "Add"
    saves_immediately = True

    def __init__(self, tracks: list, controller, parent=None):
        super().__init__(tracks, controller, parent)
        self._build_ui()

    def _build_ui(self):
        """Build the search row, the tag list and the Remove button."""
        layout = QVBoxLayout(self)

        search_row = QHBoxLayout()
        self._search = build_entity_search_widget(self.controller, self.model_name, self.name_field, self.id_field, self.placeholder_text)
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
        self._list.itemSelectionChanged.connect(self._on_selection_changed)
        layout.addWidget(self._list)

        remove_row = QHBoxLayout()
        remove_row.addStretch()
        self._remove_btn = QPushButton("Remove Selected")
        self._remove_btn.setToolTip("Remove the selected items from the edited track(s) (Delete)")
        self._remove_btn.setEnabled(False)
        self._remove_btn.clicked.connect(self._remove_selected)
        remove_row.addWidget(self._remove_btn)
        layout.addLayout(remove_row)

        delete_shortcut = QShortcut(QKeySequence.Delete, self._list)
        delete_shortcut.setContext(Qt.WidgetShortcut)
        delete_shortcut.activated.connect(self._remove_selected)

    def _on_selection_changed(self):
        """Enable "Remove Selected" when items are selected."""
        self._remove_btn.setEnabled(bool(self._list.selectedItems()))

    def _known_entities(self) -> list:
        """Return the entities to check for a case-insensitive duplicate name."""
        # The full cached table when small enough, else the widget's last query.
        cached = get_cached_entities(self.controller, self.model_name)
        if cached is not None:
            return cached
        return self._search.known_matches()

    def _on_search_text_changed(self, text: str):
        """Enable the add button when the search has text."""
        self._add_btn.setEnabled(bool(text.strip()))

    def _load_track_items(self, track):
        """Return [(id, name), ...] tagged on a single track."""
        return [(getattr(e, self.id_field), getattr(e, self.name_field)) for e in getattr(track, self.relationship)]

    def load(self, tracks: list) -> None:
        """Show the tags shared by every edited track."""
        self.tracks = tracks
        self._list.clear()
        items = self._common_items() if self.is_multi else self._load_track_items(self.track)
        for entity_id, name in sorted(items, key=lambda i: (i[1] or "").lower()):
            item = QListWidgetItem(name)
            item.setData(Qt.UserRole, entity_id)
            self._list.addItem(item)
        self._on_selection_changed()

    def _common_items(self):
        """Return the (id, name) tags present on every edited track."""
        all_sets = [set(self._load_track_items(t)) for t in self.tracks]
        return list(set.intersection(*all_sets)) if all_sets else []

    def _invalidate_cache(self) -> None:
        """Expire the cached tag relationship on every edited track."""
        # expire_on_commit=False: a commit does not refresh the cached relationship.
        session = self.controller.get.session
        for track in self.tracks:
            session.expire(track, [self.relationship])

    def _find_or_create(self, name: str):
        """Return the entity named `name`, creating it if needed."""
        return find_or_create_by_name(self.controller, self.model_name, self.name_field, name, self._known_entities())

    def _add(self):
        """Add every typed tag to every edited track."""
        names = self._search.split_names()
        if not names:
            return

        # matched_id only names a single typed entry; several names are each resolved by name.
        single_matched_id = self._search.matched_id() if len(names) == 1 else None

        entities = []
        for name in names:
            entity = self.controller.get.get_entity_object(self.model_name, **{self.id_field: single_matched_id}) if single_matched_id is not None else self._find_or_create(name)
            # A subclass may return a list (a split-alias rule expands one name into several).
            if isinstance(entity, list):
                entities.extend((name, e) for e in entity)
            elif entity:
                entities.append((name, entity))
        if not entities:
            QMessageBox.warning(self, "Error", f"Could not find or create the {self.model_name.lower()}.")
            return

        rows = [{"track_id": track.track_id, self.id_field: getattr(entity, self.id_field)} for _name, entity in entities for track in self.tracks]
        _, failed = self.controller.add.add_entities_with_fallback(self.assoc_model, rows)
        if failed:
            bad_track_ids = ", ".join(dict.fromkeys(str(row["track_id"]) for row in failed))
            logger.warning(f"Failed to tag {len(failed)} track(s) with {self.model_name}: track_id(s) {bad_track_ids}")
            QMessageBox.warning(
                self,
                "Some tracks not updated",
                f"Could not add to {len(failed)} of {len(rows)} track(s) (track_id(s) {bad_track_ids}). They may have been deleted or changed since this tab was opened; try closing and reopening it.",
            )

        if single_matched_id is None:
            search = self._search
            for _name, entity in entities:
                entity_id = getattr(entity, self.id_field)
                display = getattr(entity, self.name_field, None)
                if display:
                    # Deferred: add_to_index() rebuilds the completer, which crashes if done
                    # mid key-dispatch (Enter -> returnPressed fires inside keyPressEvent).
                    QTimer.singleShot(0, lambda d=display, i=entity_id, s=search: s.add_to_index(d, i))
                register_cached_entity(self.model_name, entity)

        self._search.reset()
        self._invalidate_cache()
        self.load(self.tracks)

    def _remove_selected(self):
        """Remove the selected tags from every edited track."""
        items = self._list.selectedItems()
        if not items:
            return
        track_ids = [track.track_id for track in self.tracks]
        failed = [item.text() for item in items if not self.controller.delete.delete_entity(self.assoc_model, track_id=track_ids, **{self.id_field: item.data(Qt.UserRole)})]
        if failed:
            logger.error(f"Failed to remove {self.model_name}(s) {failed} from tracks {track_ids}")
            QMessageBox.warning(self, "Error", "Could not remove:\n" + "\n".join(failed))
        self._invalidate_cache()
        self.load(self.tracks)

    def contextMenuEvent(self, event):
        """Offer "Remove" for the selected items."""
        if self._list.selectedItems():
            menu = QMenu(self)
            menu.addAction("Remove", self._remove_selected)
            menu.exec(event.globalPos())
