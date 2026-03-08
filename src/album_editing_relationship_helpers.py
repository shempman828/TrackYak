"""
album_editing_relationship_helpers.py
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCompleter,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLineEdit,
    QMessageBox,
    QSpinBox,
    QTextEdit,
)

from src.logger_config import logger


class RelationshipHelpers:
    """Manages album relationship operations (artists, publishers, places, awards)"""

    def __init__(self, controller, album, refresh_callback):
        self.controller = controller
        self.album = album
        self.show_updated_view = refresh_callback

    # =========================================================================
    # Publisher management
    # =========================================================================

    def add_publisher(self):
        """Add a new publisher to the album."""
        existing_publishers = self._get_existing_entities("Publisher", "publisher_name")

        result = AutocompleteDialog.get_inputs(
            [
                {
                    "name": "publisher_name",
                    "label": "Publisher Name:",
                    "type": "text",
                    "completer_data": existing_publishers,
                    "placeholder": "Start typing to search publishers...",
                }
            ],
            title="Add Publisher",
            parent=None,
        )

        if not result or not result["publisher_name"]:
            return

        publisher_name = result["publisher_name"]
        try:
            publisher = self.controller.get.get_entity_object(
                "Publisher", publisher_name=publisher_name
            )
            if not publisher:
                publisher = self.controller.add.add_entity(
                    "Publisher", publisher_name=publisher_name
                )

            self.controller.add.add_entity(
                "AlbumPublisher",
                publisher_id=publisher.publisher_id,
                album_id=self.album.album_id,
            )

            self.show_updated_view()
            QMessageBox.information(None, "Success", "Publisher added successfully!")

        except Exception as e:
            logger.exception("Failed to add publisher")
            QMessageBox.critical(None, "Error", f"Failed to add publisher: {str(e)}")

    def remove_publisher(self, album_publisher):
        """Remove a publisher from the album using the association object.

        FIX: AlbumPublisher uses a composite primary key (album_id + publisher_id)
        with no separate album_publisher_id column.  We must delete by filters
        rather than by a single integer ID.
        """
        try:
            self.controller.delete.delete_entity(
                "AlbumPublisher",
                album_id=album_publisher.album_id,
                publisher_id=album_publisher.publisher_id,
            )
            self.show_updated_view()
        except Exception as e:
            QMessageBox.critical(None, "Error", f"Failed to remove publisher: {str(e)}")

    # =========================================================================
    # Artist credit management
    # =========================================================================

    def add_artist_credit(self):
        """Add a new artist credit with role to the album."""
        existing_artists = self._get_existing_entities("Artist", "artist_name")
        existing_roles = self._get_existing_entities("Role", "role_name")

        result = AutocompleteDialog.get_inputs(
            [
                {
                    "name": "artist_name",
                    "label": "Artist:",
                    "type": "text",
                    "completer_data": existing_artists,
                    "placeholder": "Start typing to search artists...",
                },
                {
                    "name": "role_name",
                    "label": "Role:",
                    "type": "text",
                    "completer_data": existing_roles,
                    "placeholder": "Enter role (composer, performer, etc.)",
                },
            ],
            title="Add Artist Credit",
            parent=None,
        )

        if not result or not result["artist_name"] or not result["role_name"]:
            QMessageBox.warning(None, "Warning", "Both artist and role are required.")
            return

        try:
            artist = self.controller.get.get_entity_object(
                "Artist", artist_name=result["artist_name"]
            )
            if not artist:
                artist = self.controller.add.add_entity(
                    "Artist", artist_name=result["artist_name"]
                )

            role = self.controller.get.get_entity_object(
                "Role", role_name=result["role_name"]
            )
            if not role:
                role = self.controller.add.add_entity(
                    "Role", role_name=result["role_name"]
                )

            self.controller.add.add_entity(
                "AlbumRoleAssociation",
                album_id=self.album.album_id,
                artist_id=artist.artist_id,
                role_id=role.role_id,
            )

            self.show_updated_view()

        except Exception as e:
            logger.exception("Failed to add artist credit")
            QMessageBox.critical(
                None, "Error", f"Failed to add artist credit: {str(e)}"
            )

    def remove_artist_credit(self, role_assoc):
        """Remove an artist credit from the album."""
        try:
            self.controller.delete.delete_entity(
                "AlbumRoleAssociation", role_assoc.album_role_id
            )
            self.show_updated_view()
        except Exception as e:
            QMessageBox.critical(
                None, "Error", f"Failed to remove artist credit: {str(e)}"
            )

    # =========================================================================
    # Place management
    # =========================================================================

    def add_place(self):
        """Add a place association to the album."""
        existing_places = self._get_existing_entities("Place", "place_name")

        result = AutocompleteDialog.get_inputs(
            [
                {
                    "name": "place_name",
                    "label": "Place Name:",
                    "type": "text",
                    "completer_data": existing_places,
                    "placeholder": "Start typing to search places...",
                },
                {
                    "name": "association_type",
                    "label": "Association Type:",
                    "type": "text",
                    "placeholder": "e.g. Recording Location, Release Country...",
                },
            ],
            title="Add Place Association",
            parent=None,
        )

        if not result or not result["place_name"]:
            QMessageBox.warning(None, "Warning", "Place name is required.")

    def remove_place(self, association):
        """Remove a place association from the album."""
        try:
            self.controller.delete.delete_entity(
                "PlaceAssociation", association.association_id
            )
            self.show_updated_view()
        except Exception as e:
            QMessageBox.critical(
                None, "Error", f"Failed to remove place association: {str(e)}"
            )

    # =========================================================================
    # Award management
    # =========================================================================

    def add_album_award(self):
        """Add an award association to the album."""
        existing_awards = self._get_existing_entities("Award", "award_name")

        result = AutocompleteDialog.get_inputs(
            [
                {
                    "name": "award_name",
                    "label": "Award Name:",
                    "type": "text",
                    "completer_data": existing_awards,
                    "placeholder": "Start typing to search awards...",
                }
            ],
            title="Add Award",
            parent=None,
        )

        if not result or not result["award_name"]:
            return

        try:
            award = self.controller.get.get_entity_object(
                "Award", award_name=result["award_name"]
            )
            if not award:
                award = self.controller.add.add_entity(
                    "Award", award_name=result["award_name"]
                )

            self.controller.add.add_entity(
                "AwardAssociation",
                award_id=award.award_id,
                entity_id=self.album.album_id,
                entity_type="Album",
            )

            self.show_updated_view()

        except Exception as e:
            logger.exception("Failed to add award")
            QMessageBox.critical(None, "Error", f"Failed to add award: {str(e)}")

    def remove_album_award_association(self, award):
        """Remove an award from the album."""
        try:
            self.controller.delete.delete_entity(
                "AwardAssociation",
                entity_id=self.album.album_id,
                entity_type="Album",
                award_id=award.award_id,
            )
            self.show_updated_view()
        except Exception as e:
            QMessageBox.critical(None, "Error", f"Failed to remove award: {str(e)}")

    # =========================================================================
    # Helpers
    # =========================================================================

    def _get_existing_entities(self, entity_type, name_field):
        """Get list of existing entity names for autocomplete."""
        try:
            entities = self.controller.get.get_all_entities(entity_type)
            return [
                getattr(e, name_field) for e in entities if getattr(e, name_field, None)
            ]
        except Exception:
            return []


# =============================================================================
# AutocompleteDialog
# =============================================================================


class AutocompleteDialog:
    """Reusable dialog with autocomplete fields.

    Call signature for get_inputs:
        get_inputs(field_configs, title="", parent=None)

    Previous code was passing title and parent as positional args 2 and 3,
    but the old method signature only accepted (field_configs, existing_data=None).
    This has been corrected — title and parent are now proper named parameters.
    """

    @staticmethod
    def get_inputs(field_configs, title="", parent=None):
        """
        Show a dialog with the given fields and return the entered values.

        field_configs: list of dicts, each with:
          - 'name'           : key for the result dict
          - 'label'          : label shown next to the widget
          - 'type'           : 'text', 'spin', 'textarea'
          - 'completer_data' : list of strings for autocomplete (text only)
          - 'placeholder'    : placeholder text
          - 'default'        : default value
          - 'min' / 'max'    : range for spin widgets

        Returns a dict of {name: value} or None if cancelled.
        """
        dialog = QDialog(parent)
        dialog.setWindowTitle(title or "Enter Details")
        dialog.setMinimumWidth(400)

        layout = QFormLayout(dialog)
        widgets = {}

        for config in field_configs:
            widget_type = config.get("type", "text")

            if widget_type == "text":
                widget = QLineEdit()
                if config.get("placeholder"):
                    widget.setPlaceholderText(config["placeholder"])
                if config.get("completer_data"):
                    completer = QCompleter(config["completer_data"])
                    completer.setCaseSensitivity(Qt.CaseInsensitive)
                    completer.setFilterMode(Qt.MatchContains)
                    widget.setCompleter(completer)

            elif widget_type == "spin":
                widget = QSpinBox()
                widget.setRange(config.get("min", 1900), config.get("max", 2100))

            elif widget_type == "textarea":
                widget = QTextEdit()
                widget.setMaximumHeight(100)
                if config.get("placeholder"):
                    widget.setPlaceholderText(config["placeholder"])

            else:
                widget = QLineEdit()

            # Apply default value
            default = config.get("default")
            if default is not None:
                if isinstance(widget, QLineEdit):
                    widget.setText(str(default))
                elif isinstance(widget, QTextEdit):
                    widget.setPlainText(str(default))
                elif isinstance(widget, QSpinBox):
                    widget.setValue(int(default))

            layout.addRow(config["label"], widget)
            widgets[config["name"]] = widget

        button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        button_box.accepted.connect(dialog.accept)
        button_box.rejected.connect(dialog.reject)
        layout.addRow(button_box)

        if dialog.exec() == QDialog.Accepted:
            return {
                name: AutocompleteDialog._get_widget_value(widget)
                for name, widget in widgets.items()
            }
        return None

    @staticmethod
    def _get_widget_value(widget):
        if isinstance(widget, QLineEdit):
            return widget.text().strip()
        elif isinstance(widget, QTextEdit):
            return widget.toPlainText().strip()
        elif isinstance(widget, QSpinBox):
            return widget.value()
        return None
