from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)
from sqlalchemy.exc import SQLAlchemyError

from src.core.logger_config import logger

# Groups with more associations than this start collapsed, since an
# expanded group this size is what makes the dialog unwieldy to scan.
GROUP_AUTO_EXPAND_THRESHOLD = 10


class AssociationDetailsDialog(QDialog):
    def __init__(self, controller, place, parent=None, recursive=False):
        super().__init__(parent)
        self.controller = controller
        self.place = place
        self.recursive_mode = recursive
        self.setWindowTitle(f"Associations for {place.place_name}")
        self.setModal(True)
        self.init_ui()
        self.adjust_size()

    def init_ui(self):
        layout = QVBoxLayout(self)

        # Place info and toggle
        header_layout = QHBoxLayout()
        place_info = QLabel(f"<h3>{self.place.place_name} ({self.place.place_type})</h3>")
        header_layout.addWidget(place_info)

        self.recursive_toggle = QPushButton(
            "Show Recursive Associations" if not self.recursive_mode else "Show Direct Associations"
        )
        self.recursive_toggle.clicked.connect(self.toggle_recursive_mode)
        header_layout.addWidget(self.recursive_toggle)

        layout.addLayout(header_layout)

        # Filter box
        filter_layout = QHBoxLayout()
        filter_layout.addWidget(QLabel("Filter:"))
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("Type to filter by name, type, association, or path...")
        self.filter_edit.textChanged.connect(self.filter_associations)
        filter_layout.addWidget(self.filter_edit)
        layout.addLayout(filter_layout)

        # Associations tree
        associations_label = QLabel("<b>Associated Entities:</b>")
        layout.addWidget(associations_label)

        # Create tree widget with columns
        self.associations_tree = QTreeWidget()
        self.associations_tree.setHeaderLabels(["Entity", "Type", "Association Type", "Path"])
        self.associations_tree.setSortingEnabled(True)
        self.associations_tree.setAlternatingRowColors(True)
        self.associations_tree.setSelectionMode(QTreeWidget.SingleSelection)

        # Set column widths
        self.associations_tree.setColumnWidth(0, 200)  # Entity name
        self.associations_tree.setColumnWidth(1, 100)  # Entity type
        self.associations_tree.setColumnWidth(2, 120)  # Association type
        self.associations_tree.setColumnWidth(3, 150)  # Path (for recursive mode)

        layout.addWidget(self.associations_tree)

        # Close button
        close_button = QPushButton("Close")
        close_button.clicked.connect(self.accept)
        layout.addWidget(close_button)

        # Load associations
        self.load_associations()

    def adjust_size(self):
        """Auto-adjust the dialog size to fit contents."""
        # Calculate ideal size
        self.adjustSize()

        # Set reasonable maximum size
        screen_geometry = self.screen().availableGeometry()
        max_width = screen_geometry.width() * 0.7
        max_height = screen_geometry.height() * 0.8

        current_size = self.size()
        new_width = min(max(500, current_size.width()), max_width)
        new_height = min(max(400, current_size.height()), max_height)

        self.resize(new_width, new_height)

        # Ensure the dialog is centered relative to parent
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
                haystack = " ".join(
                    child_item.text(col) for col in range(child_item.columnCount())
                ).lower()
                matches = text in haystack
                child_item.setHidden(not matches)
                any_visible = any_visible or matches

            type_item.setHidden(not any_visible)
            type_item.setExpanded(any_visible)

    def toggle_recursive_mode(self):
        """Toggle between direct and recursive association views."""
        self.recursive_mode = not self.recursive_mode
        self.recursive_toggle.setText(
            "Show Recursive Associations" if not self.recursive_mode else "Show Direct Associations"
        )
        self.load_associations()
        self.adjust_size()

    def load_associations(self):
        """Load associations grouped by entity type."""
        self.associations_tree.clear()

        try:
            if self.recursive_mode:
                associations = self.get_recursive_associations(self.place.place_id)
            else:
                associations = self.controller.get.get_all_entities(
                    "PlaceAssociation", place_id=self.place.place_id
                )

            if not associations:
                no_assoc_item = QTreeWidgetItem(["No associations found", "", "", ""])
                self.associations_tree.addTopLevelItem(no_assoc_item)
                return

            # Group associations by entity_type
            associations_by_type = {}
            for assoc in associations:
                entity_type = assoc.entity_type or "Unknown"
                if entity_type not in associations_by_type:
                    associations_by_type[entity_type] = []
                associations_by_type[entity_type].append(assoc)

            # Create tree structure grouped by entity type
            for entity_type, type_associations in sorted(associations_by_type.items()):
                type_item = QTreeWidgetItem([f"{entity_type.title()}s", "", "", ""])

                # Set bold font for group headers
                font = type_item.font(0)
                font.setBold(True)
                type_item.setFont(0, font)

                # Add count to group header
                type_item.setText(0, f"{entity_type.title()}s ({len(type_associations)})")

                self.associations_tree.addTopLevelItem(type_item)
                type_item.setExpanded(len(type_associations) <= GROUP_AUTO_EXPAND_THRESHOLD)

                for assoc in type_associations:
                    entity = self.get_entity_details(assoc.entity_type, assoc.entity_id)
                    if entity:
                        display_name = self.get_entity_display_name(entity, assoc.entity_type)

                        # Create child item
                        child_item = QTreeWidgetItem(
                            [
                                display_name,
                                assoc.entity_type.title(),
                                assoc.association_type.type_name if assoc.association_type else "",
                                assoc.place_path if hasattr(assoc, "place_path") else "Direct",
                            ]
                        )

                        # Store entity data for potential future use
                        child_item.setData(0, Qt.UserRole, entity)
                        child_item.setData(0, Qt.UserRole + 1, assoc)

                        # Add tooltip with more details
                        tooltip = self.create_entity_tooltip(entity, assoc.entity_type)
                        if hasattr(assoc, "place_path"):
                            tooltip += f"\nPath: {assoc.place_path}"
                        child_item.setToolTip(0, tooltip)

                        type_item.addChild(child_item)
                    else:
                        # Entity not found
                        child_item = QTreeWidgetItem(
                            [
                                f"Unknown {assoc.entity_type} (ID: {assoc.entity_id})",
                                assoc.entity_type.title(),
                                assoc.association_type.type_name if assoc.association_type else "",
                                assoc.place_path if hasattr(assoc, "place_path") else "Direct",
                            ]
                        )
                        type_item.addChild(child_item)

            # Auto-resize columns to content
            for i in range(self.associations_tree.columnCount()):
                self.associations_tree.resizeColumnToContents(i)

            if self.filter_edit.text():
                self.filter_associations(self.filter_edit.text())

        except (SQLAlchemyError, RuntimeError) as e:
            logger.exception("Error loading associations")
            error_item = QTreeWidgetItem([f"Error loading associations: {e!s}", "", "", ""])
            self.associations_tree.addTopLevelItem(error_item)

    def create_entity_tooltip(self, entity, entity_type):
        """Create detailed tooltip for different entity types."""
        tooltip = ""
        if entity_type == "artist" and hasattr(entity, "artist_name"):
            tooltip = f"Artist: {entity.artist_name}\n"
            tooltip += f"Type: {'Group' if entity.isgroup else 'Person'}\n"
            if entity.begin_year:
                tooltip += f"Born: {entity.begin_year}"
                if entity.end_year:
                    tooltip += f" - Died: {entity.end_year}"
        elif entity_type == "track" and hasattr(entity, "track_name"):
            tooltip = f"Track: {entity.track_name}\n"
            if hasattr(entity, "album") and entity.album:
                tooltip += f"Album: {entity.album.album_name}\n"
            if entity.duration:
                tooltip += f"Duration: {entity.duration_formatted}"
        elif entity_type == "album" and hasattr(entity, "album_name"):
            tooltip = f"Album: {entity.album_name}\n"
            if entity.release_year:
                tooltip += f"Released: {entity.release_year}"
        elif entity_type == "publisher" and hasattr(entity, "publisher_name"):
            tooltip = f"Publisher: {entity.publisher_name}"
        elif entity_type == "playlist" and hasattr(entity, "playlist_name"):
            tooltip = f"Playlist: {entity.playlist_name}\n"
            if entity.playlist_description:
                tooltip += f"Description: {entity.playlist_description}"
        else:
            tooltip = f"{entity_type.title()}: {getattr(entity, 'name', 'Unknown')}"

        return tooltip

    def get_recursive_associations(self, place_id, current_path=None):
        """Get associations for this place and all child places recursively."""
        if current_path is None:
            current_path = []

        associations = []
        current_place = self.controller.get.get_entity_object("Place", place_id=place_id)

        if not current_place:
            return associations

        # Add current place to path
        new_path = current_path + [current_place.place_name]
        path_str = " → ".join(new_path)

        # Get direct associations for this place
        direct_associations = self.controller.get.get_all_entities(
            "PlaceAssociation", place_id=place_id
        )

        # Add path information to each association
        for assoc in direct_associations:
            assoc.place_path = path_str
            associations.append(assoc)

        # Get child places and their associations recursively
        child_places = self.controller.get.get_all_entities("Place", parent_id=place_id)
        for child in child_places:
            child_associations = self.get_recursive_associations(child.place_id, new_path)
            associations.extend(child_associations)

        return associations

    def get_entity_details(self, entity_type, entity_id):
        """
        Generic fetch for entity objects.
        Assumes controller.get.get_entity_object(entity_name, <entity_lower>_id=...) works.
        """
        try:
            if not entity_type:
                return None

            # normalize casing: "Track" -> "Track", "track" -> "Track"
            entity_name = entity_type.title()
            id_kwarg = f"{entity_type.lower()}_id"

            # call the controller getter with a dynamic kwarg
            return self.controller.get.get_entity_object(entity_name, **{id_kwarg: entity_id})

        except SQLAlchemyError as e:
            logger.exception(
                "Error getting entity details for %s id=%s: %s", entity_type, entity_id, e
            )
            return None

    def get_entity_display_name(self, entity, entity_type):
        """
        Generic display-name resolution.
        Assumes attribute is named '<entity_lower>_name', e.g. 'track_name'.
        """
        try:
            if not entity or not entity_type:
                return f"Unknown {entity_type or 'entity'}"

            attr = f"{entity_type.lower()}_name"
            # getattr fallback to a generic `name` or to a string indicating unknown
            return getattr(entity, attr, getattr(entity, "name", f"Unknown {entity_type}"))
        except SQLAlchemyError:
            logger.exception("Error retrieving display name for %s", entity_type)
            return f"Unknown {entity_type}"
