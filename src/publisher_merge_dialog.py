from PySide6.QtWidgets import QMessageBox

from src.base_merge_dialog import MergeDBDialog
from src.logger_config import logger


class PublisherMergeDialog(MergeDBDialog):
    """Specialized dialog for merging publishers."""

    def __init__(self, controller, parent=None):
        # Initialize with "Publisher" as the model name
        super().__init__(controller, "Publisher", parent)
        # Publisher-specific attribute names are automatically set by base class
        # publisher_id, publisher_name

    def _get_related_count(self, publisher_id):
        """Get the number of albums for a publisher."""
        try:
            albums = self.controller.get.get_entity_links(
                "AlbumPublisher", publisher_id=publisher_id
            )
            return len(albums)
        except Exception as e:
            logger.error(
                f"Error getting album count for publisher {publisher_id}: {str(e)}"
            )
            return 0

    def _build_entity_info(self, entity, side):
        """Enhanced info display for publishers."""
        if not entity:
            return "No publisher selected"

        name = getattr(entity, self.name_attr, "Unknown")
        publisher_id = getattr(entity, self.id_attr)

        info = f"<b>{name}</b><br>"

        # Add album count
        album_count = self._get_related_count(publisher_id)
        info += f"Albums: {album_count}<br>"

        # Add status
        if hasattr(entity, "is_active"):
            status = "Active" if getattr(entity, "is_active") == 1 else "Inactive"
            info += f"Status: {status}"

        return info

    def _on_merge(self):
        """Override merge to add publisher-specific confirmation."""
        # Show publisher-specific confirmation message
        source_name = getattr(self.source_entity, self.name_attr)
        target_name = getattr(self.target_entity, self.name_attr)
        source_albums = self._get_related_count(
            getattr(self.source_entity, self.id_attr)
        )

        if source_albums > 0:
            reply = QMessageBox.question(
                self,
                "Confirm Publisher Merge",
                f"Merge '{source_name}' into '{target_name}'?\n\n"
                f"This will transfer {source_albums} album(s) from '{source_name}' to "
                f"'{target_name}' and delete '{source_name}'.\n\n"
                f"This action cannot be undone.",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
        else:
            reply = QMessageBox.question(
                self,
                "Confirm Publisher Merge",
                f"Merge '{source_name}' into '{target_name}'?\n\n"
                f"'{source_name}' has no albums.\n\n"
                f"This action cannot be undone.",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )

        if reply == QMessageBox.Yes:
            # Call parent merge logic
            super()._on_merge()
