"""
Global manager for Religion rows: rename, describe, reparent (via drag-and-drop),
add, and delete the shared religion/denomination vocabulary usable across all
artists, independent of any single artist's edit dialog.
"""

from collections import defaultdict

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QMessageBox,
    QTreeWidget,
    QTreeWidgetItem,
)
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from src.common.hierarchy_tree_style import (
    collect_expanded_ids,
    icon_for_depth,
    is_hierarchy_descendant,
    restore_expanded_ids_or_expand_all,
)
from src.common.lookup_manager_dialog import COUNT_COL, DESC_COL, NAME_COL, BaseLookupManagerDialog
from src.db.db_tables import Artist
from src.foundation.logger_config import logger


class ReligionManagerDialog(BaseLookupManagerDialog):
    """Tree of every Religion in the library, nested by parent/child, with
    inline rename/description editing, drag-and-drop reparenting, add, and
    delete."""

    _ENTITY_TYPE = "Religion"
    _ID_ATTR = "religion_id"
    _NAME_ATTR = "religion_name"
    _DESC_ATTR = "description"
    _ENTITY_LABEL = "religion"
    _NAME_EMPTY_LABEL = "Religion name"
    _ADD_BUTTON_TEXT = "Add Religion"
    _ADD_DIALOG_TITLE = "Add Religion"
    _ADD_DIALOG_PROMPT = "Name:"
    _DELETE_SELECT_FIRST_MSG = "Select one or more religions first."
    _DELETE_DIALOG_TITLE = "Delete Religion(s)"
    _DELETE_INTRO = (
        "Delete the following religion(s)? Any artists carrying them will simply "
        "lose that affiliation, and any child religions will lose their parent."
    )

    def __init__(self, controller, parent=None):
        super().__init__(controller, "Manage Religions", (640, 480), parent)

    def _build_content_widget(self):
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
        # used by the Role/Genre/Mood hierarchy trees.
        self._tree.dropEvent = lambda event: self._on_drop_event(event)
        return self._tree

    # ── Loading ───────────────────────────────────────────────────────────

    def _fetch_counts(self) -> dict:
        return self._safe_fetch_counts(
            select(Artist.religion_id, func.count())
            .where(Artist.religion_id.isnot(None))
            .group_by(Artist.religion_id)
        )

    def _load(self):
        expanded_ids = collect_expanded_ids(self._tree)
        is_initial_load = self._tree.topLevelItemCount() == 0

        self._tree.blockSignals(True)
        try:
            self._religions = sorted(
                self.controller.get.get_all_entities("Religion") or [],
                key=lambda r: r.religion_name.lower(),
            )
            counts = self._fetch_counts()

            religion_ids = {r.religion_id for r in self._religions}
            children_map = defaultdict(list)
            for r in self._religions:
                parent_key = r.parent_id if r.parent_id in religion_ids else None
                children_map[parent_key].append(r)

            self._tree.clear()
            self._build_level(None, children_map, counts, 0, None)

            restore_expanded_ids_or_expand_all(self._tree, expanded_ids, is_initial_load)
        finally:
            self._tree.blockSignals(False)

    def _build_level(self, parent_id, children_map, counts, depth, parent_item):
        for r in sorted(children_map.get(parent_id, []), key=lambda r: r.religion_name.lower()):
            item = self._make_item(r, depth, counts)
            if parent_item is None:
                self._tree.addTopLevelItem(item)
            else:
                parent_item.addChild(item)
            self._build_level(r.religion_id, children_map, counts, depth + 1, item)

    def _make_item(self, religion, depth, counts) -> QTreeWidgetItem:
        item = QTreeWidgetItem()
        item.setText(NAME_COL, religion.religion_name)
        item.setText(DESC_COL, religion.description or "")
        item.setText(COUNT_COL, str(counts.get(religion.religion_id, 0)))
        item.setData(NAME_COL, Qt.UserRole, religion.religion_id)
        item.setIcon(NAME_COL, icon_for_depth(depth))
        item.setFlags(item.flags() | Qt.ItemIsEditable)
        return item

    def _selected_entries(self):
        return [
            (item.data(NAME_COL, Qt.UserRole), item.text(NAME_COL), item.text(COUNT_COL))
            for item in self._tree.selectedItems()
        ]

    # ── Editing ───────────────────────────────────────────────────────────

    def _on_item_changed(self, item: QTreeWidgetItem, column: int):
        religion_id = item.data(NAME_COL, Qt.UserRole)
        if religion_id is None:
            return

        if column == NAME_COL:
            self._validate_and_rename(religion_id, item.text(NAME_COL))
        elif column == DESC_COL:
            self._save_description(religion_id, item.text(DESC_COL))
        elif column == COUNT_COL:
            # Not a user-editable field; discard any accidental edit.
            self._load()

    def _on_drop_event(self, event):
        """Reparent the dragged religion(s) onto whatever item they're dropped
        on (or to the root if dropped on empty space)."""
        selected_items = self._tree.selectedItems()
        if not selected_items:
            event.ignore()
            return

        target_item = self._tree.itemAt(event.pos())
        new_parent_id = target_item.data(NAME_COL, Qt.UserRole) if target_item else None

        try:
            moved_any = False
            for item in selected_items:
                religion_id = item.data(NAME_COL, Qt.UserRole)
                if religion_id is None or religion_id == new_parent_id:
                    continue

                if is_hierarchy_descendant(
                    religion_id, new_parent_id, self._religions, id_attr="religion_id"
                ):
                    QMessageBox.warning(
                        self,
                        "Invalid Move",
                        f"Moving '{item.text(NAME_COL)}' there would create a "
                        "circular reference in the hierarchy.",
                    )
                    continue

                self.controller.update.update_entity(
                    "Religion", religion_id, parent_id=new_parent_id
                )
                moved_any = True

            if moved_any:
                self._load()
                event.accept()
            else:
                event.ignore()
        except SQLAlchemyError as e:
            logger.error(f"Error moving religion: {e!s}")
            event.ignore()
