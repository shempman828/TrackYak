"""
Global manager for Religion rows: rename, describe, reparent (via drag-and-drop),
add, and delete the shared religion/denomination vocabulary usable across all
artists, independent of any single artist's edit dialog.
"""

from collections import defaultdict

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QMessageBox,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from src.common.hierarchy_tree_style import (
    collect_expanded_ids,
    icon_for_depth,
    is_hierarchy_descendant,
    restore_expanded_ids_or_expand_all,
)
from src.core.logger_config import logger
from src.db.db_tables import Artist

_NAME_COL = 0
_DESC_COL = 1
_COUNT_COL = 2


class ReligionManagerDialog(QDialog):
    """Tree of every Religion in the library, nested by parent/child, with
    inline rename/description editing, drag-and-drop reparenting, add, and
    delete."""

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.setWindowTitle("Manage Religions")
        self.resize(640, 480)
        self._build_ui()
        self._load()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        self._tree = QTreeWidget()
        self._tree.setColumnCount(3)
        self._tree.setHeaderLabels(["Name", "Description", "# Artists"])
        header = self._tree.header()
        header.setSectionResizeMode(_NAME_COL, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(_DESC_COL, QHeaderView.Stretch)
        header.setSectionResizeMode(_COUNT_COL, QHeaderView.ResizeToContents)
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
        layout.addWidget(self._tree)

        btn_row = QHBoxLayout()
        add_btn = QPushButton("Add Religion")
        add_btn.clicked.connect(self._add_religion)
        btn_row.addWidget(add_btn)

        delete_btn = QPushButton("Delete Selected")
        delete_btn.clicked.connect(self._delete_selected)
        btn_row.addWidget(delete_btn)

        btn_row.addStretch()

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)

        layout.addLayout(btn_row)

    # ── Loading ───────────────────────────────────────────────────────────

    def _fetch_counts(self) -> dict:
        try:
            rows = self.controller.get.session.execute(
                select(Artist.religion_id, func.count())
                .where(Artist.religion_id.isnot(None))
                .group_by(Artist.religion_id)
            ).all()
            return dict(rows)
        except SQLAlchemyError as e:
            logger.warning(f"Failed to fetch religion usage counts: {e}")
            return {}

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
        item.setText(_NAME_COL, religion.religion_name)
        item.setText(_DESC_COL, religion.description or "")
        item.setText(_COUNT_COL, str(counts.get(religion.religion_id, 0)))
        item.setData(_NAME_COL, Qt.UserRole, religion.religion_id)
        item.setIcon(_NAME_COL, icon_for_depth(depth))
        item.setFlags(item.flags() | Qt.ItemIsEditable)
        return item

    # ── Editing ───────────────────────────────────────────────────────────

    def _on_item_changed(self, item: QTreeWidgetItem, column: int):
        religion_id = item.data(_NAME_COL, Qt.UserRole)
        if religion_id is None:
            return

        if column == _NAME_COL:
            new_name = item.text(_NAME_COL).strip()
            if not new_name:
                QMessageBox.warning(self, "Invalid Name", "Religion name cannot be empty.")
                self._load()
                return

            existing = self.controller.get.get_entity_object(
                "Religion", religion_name=new_name
            )
            if existing and existing.religion_id != religion_id:
                QMessageBox.warning(
                    self,
                    "Duplicate Name",
                    f"A religion named '{new_name}' already exists.",
                )
                self._load()
                return

            self.controller.update.update_entity(
                "Religion", religion_id, religion_name=new_name
            )

        elif column == _DESC_COL:
            self.controller.update.update_entity(
                "Religion", religion_id, description=item.text(_DESC_COL).strip() or None
            )

        elif column == _COUNT_COL:
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
        new_parent_id = target_item.data(_NAME_COL, Qt.UserRole) if target_item else None

        try:
            moved_any = False
            for item in selected_items:
                religion_id = item.data(_NAME_COL, Qt.UserRole)
                if religion_id is None or religion_id == new_parent_id:
                    continue

                if is_hierarchy_descendant(
                    religion_id, new_parent_id, self._religions, id_attr="religion_id"
                ):
                    QMessageBox.warning(
                        self,
                        "Invalid Move",
                        f"Moving '{item.text(_NAME_COL)}' there would create a "
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

    def _add_religion(self):
        name, ok = QInputDialog.getText(self, "Add Religion", "Name:")
        name = name.strip()
        if not ok or not name:
            return

        existing = self.controller.get.get_entity_object("Religion", religion_name=name)
        if existing:
            QMessageBox.warning(
                self, "Duplicate Name", f"A religion named '{name}' already exists."
            )
            return

        self.controller.add.add_entity("Religion", religion_name=name)
        self._load()

    def _delete_selected(self):
        items = self._tree.selectedItems()
        if not items:
            QMessageBox.information(
                self, "Delete Religion", "Select one or more religions first."
            )
            return

        entries = [
            (item.data(_NAME_COL, Qt.UserRole), item.text(_NAME_COL), item.text(_COUNT_COL))
            for item in items
        ]

        lines = [f"• {name} ({count} artist(s))" for _id, name, count in entries]
        reply = QMessageBox.question(
            self,
            "Delete Religion(s)",
            "Delete the following religion(s)? Any artists carrying them will simply "
            "lose that affiliation, and any child religions will lose their parent.\n\n"
            + "\n".join(lines),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        for religion_id, _name, _count in entries:
            self.controller.delete.delete_entity("Religion", religion_id=religion_id)
        self._load()
