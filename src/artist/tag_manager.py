"""
Global manager for Tag rows: rename, describe, reparent (via drag-and-drop),
add, and delete the tag vocabulary for one TagType at a time, usable across
all artists, independent of any single artist's edit dialog. A combo box at
the top switches which TagType's tags are shown/edited -- a tag's hierarchy
is scoped to its own TagType, so only one type's tree is shown at once.
"""

from collections import defaultdict

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QMessageBox,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from src.common.dialogs.lookup_manager_dialog import (
    COUNT_COL,
    DESC_COL,
    NAME_COL,
    BaseLookupManagerDialog,
)
from src.common.widgets.hierarchy_tree_style import (
    collect_expanded_ids,
    icon_for_depth,
    is_hierarchy_descendant,
    restore_expanded_ids_or_expand_all,
)
from src.db.db_tables import ArtistTagAssociation
from src.foundation.logger_config import logger


class TagManagerDialog(BaseLookupManagerDialog):
    """Tree of every Tag under one selected TagType, nested by parent/child,
    with inline rename/description editing, drag-and-drop reparenting, add,
    and delete."""

    _ENTITY_TYPE = "Tag"
    _ID_ATTR = "tag_id"
    _NAME_ATTR = "tag_name"
    _DESC_ATTR = "description"
    _ENTITY_LABEL = "tag"
    _NAME_EMPTY_LABEL = "Tag name"
    _ADD_BUTTON_TEXT = "Add Tag"
    _ADD_DIALOG_TITLE = "Add Tag"
    _ADD_DIALOG_PROMPT = "Name:"
    _DELETE_SELECT_FIRST_MSG = "Select one or more tags first."
    _DELETE_DIALOG_TITLE = "Delete Tag(s)"
    _DELETE_INTRO = (
        "Delete the following tag(s)? Any artists carrying them will simply "
        "lose that tag, and any child tags will lose their parent."
    )

    def __init__(self, controller, parent=None, initial_tag_type_id: int | None = None):
        self._current_tag_type_id = initial_tag_type_id
        self._tags: list = []
        super().__init__(controller, "Manage Tags", (640, 520), parent)

    def _build_content_widget(self):
        container = QWidget()
        outer = QVBoxLayout(container)
        outer.setContentsMargins(0, 0, 0, 0)

        type_row = QHBoxLayout()
        type_row.addWidget(QLabel("Tag Type:"))
        self._type_combo = QComboBox()
        self._populate_type_combo()
        self._type_combo.currentIndexChanged.connect(self._on_type_changed)
        type_row.addWidget(self._type_combo, 1)
        outer.addLayout(type_row)

        self._tree = QTreeWidget()
        self._tree.setColumnCount(3)
        self._tree.setHeaderLabels(["Name", "Description", "# Artists"])
        header = self._tree.header()
        header.setSectionResizeMode(NAME_COL, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(DESC_COL, QHeaderView.Stretch)
        header.setSectionResizeMode(COUNT_COL, QHeaderView.ResizeToContents)
        self._tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._tree.setAlternatingRowColors(True)
        self._tree.setAnimated(True)
        self._tree.setDragEnabled(True)
        self._tree.setAcceptDrops(True)
        self._tree.setDropIndicatorShown(True)
        self._tree.setDragDropMode(QAbstractItemView.InternalMove)
        self._tree.itemChanged.connect(self._on_item_changed)
        # Wrapper to keep `self` context inside the drop event, same trick
        # used by the Role/Genre/Mood/Religion hierarchy trees.
        self._tree.dropEvent = lambda event: self._on_drop_event(event)
        outer.addWidget(self._tree)

        return container

    def _populate_type_combo(self):
        try:
            self._tag_types = sorted(
                self.controller.get.get_all_entities("TagType") or [],
                key=lambda t: t.type_name.lower(),
            )
        except SQLAlchemyError as e:
            logger.warning(f"Could not fetch TagType for tag manager: {e}")
            self._tag_types = []

        self._type_combo.blockSignals(True)
        self._type_combo.clear()
        for t in self._tag_types:
            self._type_combo.addItem(t.type_name, t.tag_type_id)
        self._type_combo.blockSignals(False)

        if not self._tag_types:
            self._current_tag_type_id = None
            return

        ids = [t.tag_type_id for t in self._tag_types]
        if self._current_tag_type_id not in ids:
            self._current_tag_type_id = ids[0]
        self._type_combo.setCurrentIndex(ids.index(self._current_tag_type_id))

    def _on_type_changed(self, index: int):
        tag_type_id = self._type_combo.itemData(index) if index >= 0 else None
        if tag_type_id == self._current_tag_type_id:
            return
        self._current_tag_type_id = tag_type_id
        self._load()

    # ── Loading ───────────────────────────────────────────────────────────

    def _fetch_counts(self) -> dict:
        if self._current_tag_type_id is None:
            return {}
        return self._safe_fetch_counts(
            select(ArtistTagAssociation.tag_id, func.count())
            .where(ArtistTagAssociation.tag_id.in_([t.tag_id for t in self._tags]))
            .group_by(ArtistTagAssociation.tag_id)
        )

    def _load(self):
        expanded_ids = collect_expanded_ids(self._tree)
        is_initial_load = self._tree.topLevelItemCount() == 0

        self._tree.blockSignals(True)
        try:
            self._tree.clear()
            if self._current_tag_type_id is None:
                self._tags = []
                return

            self._tags = sorted(
                self.controller.get.get_all_entities("Tag", tag_type_id=self._current_tag_type_id)
                or [],
                key=lambda t: t.tag_name.lower(),
            )
            counts = self._fetch_counts()

            tag_ids = {t.tag_id for t in self._tags}
            children_map = defaultdict(list)
            for t in self._tags:
                parent_key = t.parent_id if t.parent_id in tag_ids else None
                children_map[parent_key].append(t)

            self._build_level(None, children_map, counts, 0, None)

            restore_expanded_ids_or_expand_all(self._tree, expanded_ids, is_initial_load)
        finally:
            self._tree.blockSignals(False)

    def _build_level(self, parent_id, children_map, counts, depth, parent_item):
        for t in sorted(children_map.get(parent_id, []), key=lambda t: t.tag_name.lower()):
            item = self._make_item(t, depth, counts)
            if parent_item is None:
                self._tree.addTopLevelItem(item)
            else:
                parent_item.addChild(item)
            self._build_level(t.tag_id, children_map, counts, depth + 1, item)

    def _make_item(self, tag, depth, counts) -> QTreeWidgetItem:
        item = QTreeWidgetItem()
        item.setText(NAME_COL, tag.tag_name)
        item.setText(DESC_COL, tag.description or "")
        item.setText(COUNT_COL, str(counts.get(tag.tag_id, 0)))
        item.setData(NAME_COL, Qt.UserRole, tag.tag_id)
        item.setIcon(NAME_COL, icon_for_depth(depth))
        item.setFlags(item.flags() | Qt.ItemIsEditable)
        return item

    def _selected_entries(self):
        return [
            (item.data(NAME_COL, Qt.UserRole), item.text(NAME_COL), item.text(COUNT_COL))
            for item in self._tree.selectedItems()
        ]

    # ── Add ──────────────────────────────────────────────────────────────

    def _add(self):
        if self._current_tag_type_id is None:
            QMessageBox.information(
                self, self._ADD_DIALOG_TITLE, "Create a tag type first (Manage Tag Types)."
            )
            return

        name, ok = QInputDialog.getText(self, self._ADD_DIALOG_TITLE, self._ADD_DIALOG_PROMPT)
        name = name.strip()
        if not ok or not name:
            return

        if self._find_by_name(name) is not None:
            QMessageBox.warning(
                self, "Duplicate Name", f"A tag named '{name}' already exists in this tag type."
            )
            return

        self.controller.add.add_entity("Tag", tag_name=name, tag_type_id=self._current_tag_type_id)
        self._load()

    def _find_by_name(self, name: str):
        lowered = name.strip().lower()
        for t in self._tags:
            if t.tag_name.strip().lower() == lowered:
                return t
        return None

    # ── Editing ───────────────────────────────────────────────────────────

    def _validate_and_rename(self, entity_id, new_name: str) -> bool:
        new_name = new_name.strip()
        if not new_name:
            QMessageBox.warning(self, "Invalid Name", f"{self._NAME_EMPTY_LABEL} cannot be empty.")
            self._load()
            return False

        existing = self._find_by_name(new_name)
        if existing and existing.tag_id != entity_id:
            QMessageBox.warning(
                self, "Duplicate Name", f"A tag named '{new_name}' already exists in this tag type."
            )
            self._load()
            return False

        self.controller.update.update_entity("Tag", entity_id, tag_name=new_name)
        return True

    def _on_item_changed(self, item: QTreeWidgetItem, column: int):
        tag_id = item.data(NAME_COL, Qt.UserRole)
        if tag_id is None:
            return

        if column == NAME_COL:
            self._validate_and_rename(tag_id, item.text(NAME_COL))
        elif column == DESC_COL:
            self._save_description(tag_id, item.text(DESC_COL))
        elif column == COUNT_COL:
            # Not a user-editable field; discard any accidental edit.
            self._load()

    def _on_drop_event(self, event):
        """Reparent the dragged tag(s) onto whatever item they're dropped
        on (or to the root if dropped on empty space). Both the dragged
        tag(s) and the drop target are always within the current TagType,
        since the tree only ever shows one type's tags at a time."""
        selected_items = self._tree.selectedItems()
        if not selected_items:
            event.ignore()
            return

        target_item = self._tree.itemAt(event.pos())
        new_parent_id = target_item.data(NAME_COL, Qt.UserRole) if target_item else None

        try:
            moved_any = False
            for item in selected_items:
                tag_id = item.data(NAME_COL, Qt.UserRole)
                if tag_id is None or tag_id == new_parent_id:
                    continue

                if is_hierarchy_descendant(tag_id, new_parent_id, self._tags, id_attr="tag_id"):
                    QMessageBox.warning(
                        self,
                        "Invalid Move",
                        f"Moving '{item.text(NAME_COL)}' there would create a "
                        "circular reference in the hierarchy.",
                    )
                    continue

                self.controller.update.update_entity("Tag", tag_id, parent_id=new_parent_id)
                moved_any = True

            if moved_any:
                self._load()
                event.accept()
            else:
                event.ignore()
        except SQLAlchemyError as e:
            logger.error(f"Error moving tag: {e!s}")
            event.ignore()
