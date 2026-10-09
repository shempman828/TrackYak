"""Connected Music dialog that the map's marker popups open for one place."""

import html

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout
from sqlalchemy.exc import SQLAlchemyError

from src.common.widgets.segmented_control import SegmentedControl
from src.foundation.logger_config import logger
from src.place.place_associations import GROUP_AUTO_EXPAND_THRESHOLD, entity_display_name, entity_tooltip, group_associations
from src.place.place_types import type_label

__all__ = ["GROUP_AUTO_EXPAND_THRESHOLD", "AssociationDetailsDialog"]


class AssociationDetailsDialog(QDialog):
    """Modal list of the music connected to a place, directly or through its child places."""

    def __init__(self, controller, place, parent=None, recursive=False):
        super().__init__(parent)
        self.controller = controller
        self.place = place
        self.recursive_mode = recursive
        self.setWindowTitle(f"Connected Music: {place.place_name}")
        self.setModal(True)
        self.init_ui()
        self.adjust_size()

    def init_ui(self):
        """Build the header, scope control, filter box, and music tree."""
        layout = QVBoxLayout(self)

        header_layout = QHBoxLayout()
        place_info = QLabel(f"<h3>{html.escape(self.place.place_name or '')} ({html.escape(type_label(self.place.place_type))})</h3>")
        header_layout.addWidget(place_info)
        header_layout.addStretch()
        self.scope_control = SegmentedControl(["Direct", "With children"])
        self.scope_control.setItemToolTip(0, "Music connected to this place only")
        self.scope_control.setItemToolTip(1, "Also include music connected to places inside this one")
        self.scope_control.setCurrentIndex(1 if self.recursive_mode else 0)
        self.scope_control.currentIndexChanged.connect(self._on_scope_changed)
        header_layout.addWidget(self.scope_control)
        layout.addLayout(header_layout)

        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("Filter connected music…")
        self.filter_edit.setClearButtonEnabled(True)
        self.filter_edit.textChanged.connect(self.filter_associations)
        layout.addWidget(self.filter_edit)

        self.associations_tree = QTreeWidget()
        self.associations_tree.setHeaderLabels(["Name", "Type", "Connection", "Via"])
        self.associations_tree.setSortingEnabled(True)
        self.associations_tree.setAlternatingRowColors(True)
        self.associations_tree.setSelectionMode(QTreeWidget.SingleSelection)
        layout.addWidget(self.associations_tree)

        close_button = QPushButton("Close")
        close_button.clicked.connect(self.accept)
        layout.addWidget(close_button)

        self.load_associations()

    def adjust_size(self):
        """Fit the dialog to its content, within 70% x 80% of the screen, centered on the parent."""
        self.adjustSize()
        screen_geometry = self.screen().availableGeometry()
        max_width = int(screen_geometry.width() * 0.7)
        max_height = int(screen_geometry.height() * 0.8)
        current_size = self.size()
        self.resize(min(max(500, current_size.width()), max_width), min(max(400, current_size.height()), max_height))
        if self.parent():
            parent_center = self.parent().geometry().center()
            self.move(parent_center - self.rect().center())

    def filter_associations(self, text):
        """Show only groups/rows matching the filter text; restore defaults when empty."""
        text = text.strip().lower()

        for i in range(self.associations_tree.topLevelItemCount()):
            type_item = self.associations_tree.topLevelItem(i)
            child_count = type_item.childCount()

            if not text:
                type_item.setHidden(False)
                type_item.setExpanded(child_count <= GROUP_AUTO_EXPAND_THRESHOLD)
                for c in range(child_count):
                    type_item.child(c).setHidden(False)
                continue

            any_visible = False
            for c in range(child_count):
                child_item = type_item.child(c)
                haystack = " ".join(child_item.text(col) for col in range(child_item.columnCount())).lower()
                matches = text in haystack
                child_item.setHidden(not matches)
                any_visible = any_visible or matches

            type_item.setHidden(not any_visible)
            type_item.setExpanded(any_visible)

    def _on_scope_changed(self, index):
        """Reload for the Direct / With children choice."""
        self.recursive_mode = index == 1
        self.load_associations()
        self.adjust_size()

    def load_associations(self):
        """Load the connected music, grouped by entity type."""
        self.associations_tree.clear()
        self.associations_tree.setColumnHidden(3, not self.recursive_mode)
        try:
            groups = group_associations(self.controller, self.place.place_id, recursive=self.recursive_mode)
        except (SQLAlchemyError, RuntimeError):
            logger.exception("Error loading associations")
            self.associations_tree.addTopLevelItem(QTreeWidgetItem(["Could not load connected music. See the log for details.", "", "", ""]))
            return

        if not groups:
            self.associations_tree.addTopLevelItem(QTreeWidgetItem(["No music is connected to this place.", "", "", ""]))
            return

        for entity_type, rows in sorted(groups.items()):
            type_item = QTreeWidgetItem([f"{entity_type.title()}s ({len(rows)})", "", "", ""])
            font = type_item.font(0)
            font.setBold(True)
            type_item.setFont(0, font)
            self.associations_tree.addTopLevelItem(type_item)
            type_item.setExpanded(len(rows) <= GROUP_AUTO_EXPAND_THRESHOLD)

            for assoc, entity in rows:
                name = entity_display_name(entity, assoc.entity_type) if entity else f"Unknown {assoc.entity_type} (ID: {assoc.entity_id})"
                via = getattr(assoc, "place_path", "")
                child_item = QTreeWidgetItem([str(name), assoc.entity_type.title(), assoc.association_type.type_name if assoc.association_type else "", via])
                if entity:
                    child_item.setData(0, Qt.UserRole, entity)
                    child_item.setToolTip(0, entity_tooltip(entity, assoc.entity_type) + (f"\nVia: {via}" if via else ""))
                type_item.addChild(child_item)

        for i in range(self.associations_tree.columnCount()):
            self.associations_tree.resizeColumnToContents(i)

        if self.filter_edit.text():
            self.filter_associations(self.filter_edit.text())
