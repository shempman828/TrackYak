from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox
from sqlalchemy.exc import SQLAlchemyError

from src.common.base_merge_dialog import MergeDBDialog
from src.foundation.logger_config import logger


class PlaceMergeDialog(MergeDBDialog):
    """Specialized dialog for merging places."""

    def __init__(self, controller, parent=None, place_obj=None):
        # Initialize with "Place" as the model name
        super().__init__(controller, "Place", parent)

        # Make the dialog float independently of the main app window
        self.setWindowFlags(self.windowFlags() | Qt.Dialog | Qt.WindowStaysOnTopHint)

        # If a place was already selected when the dialog was opened,
        # pre-populate the source side and auto-suggest merge targets.
        if place_obj is not None:
            self._prepopulate_source(place_obj)

    def _prepopulate_source(self, place_obj):
        """Fill in the source side with the given place and suggest targets."""
        try:
            # Set the source entity directly
            self.source_entity = place_obj
            place_name = getattr(place_obj, self.name_attr, "")

            # Update the info label so the user can see the place details
            self.source_info.setText(self._build_entity_info(place_obj, "source"))

            # Populate the source list and highlight the selected item
            self.source_search.setText(place_name)
            self._update_list(place_name, "source")
            self._highlight_selected_entities()

            # Enable the "Find Similar" button on the target side
            self.target_find_similar_btn.setEnabled(True)

            # Auto-populate the target list with similarity suggestions
            self._auto_suggest_similar(place_obj, "target")

            # Refresh button states (e.g. enable Next button if both sides filled)
            self._update_action_buttons()

        except (AttributeError, RuntimeError) as e:
            logger.error(f"Error pre-populating source place: {e!s}")

    def _get_related_count(self, place_id):
        """Get the number of entities associated with a place and its descendants."""
        try:
            place = self.controller.get.get_entity_object("Place", place_id=place_id)
            if place is None:
                return 0
            return place.recursive_association_count
        except SQLAlchemyError as e:
            logger.error(f"Error getting association count for place {place_id}: {e!s}")
            return 0

    def _get_child_count(self, place):
        """Get the number of direct child places, without assuming the
        relationship collection is loaded/fresh on a detached instance."""
        try:
            return len(place.children)
        except (AttributeError, SQLAlchemyError):
            return 0

    def _build_entity_info(self, entity, side):
        """Enhanced info display for places."""
        if not entity:
            return "No place selected"

        name = getattr(entity, self.name_attr, "Unknown")
        place_id = getattr(entity, self.id_attr)

        info = f"<b>{name}</b><br>"

        if getattr(entity, "place_type", None):
            info += f"Type: {entity.place_type}<br>"

        # Include descendants, since merging affects the whole subtree
        info += f"Associations (incl. children): {self._get_related_count(place_id)}<br>"

        child_count = self._get_child_count(entity)
        if child_count:
            info += f"Child places: {child_count}<br>"

        return info

    def _on_merge(self):
        """Override merge to add place-specific confirmation."""
        source_name = getattr(self.source_entity, self.name_attr)
        target_name = getattr(self.target_entity, self.name_attr)
        source_id = getattr(self.source_entity, self.id_attr)
        source_assoc = self._get_related_count(source_id)
        source_children = self._get_child_count(self.source_entity)

        transfer_bits = [f"{source_assoc} association(s)"]
        if source_children:
            transfer_bits.append(f"{source_children} child place(s)")

        reply = QMessageBox.question(
            self,
            "Confirm Place Merge",
            f"Merge '{source_name}' into '{target_name}'?\n\n"
            f"This will transfer {' and '.join(transfer_bits)} from "
            f"'{source_name}' to '{target_name}' and delete '{source_name}'.\n\n"
            f"Duplicate associations already on '{target_name}' will be dropped "
            f"rather than duplicated.\n\n"
            f"This action cannot be undone.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )

        if reply == QMessageBox.Yes:
            # Call parent merge logic, skipping its generic confirmation
            # since we've already shown a place-specific one above.
            super()._execute_merge()
