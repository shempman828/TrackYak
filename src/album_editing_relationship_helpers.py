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

from logger_config import logger


class RelationshipHelpers:
    """Helper functions to manage relationships"""

    def __init__(self, controller, album, refresh_callback=None):
        self.controller = controller
        self.album = album
        self.refresh_callback = (
            refresh_callback  # Use callback instead of creating view
        )

    def show_updated_view(self):
        """Delegate to the refresh callback if available"""
        if self.refresh_callback:
            self.refresh_callback()

    def remove_place_association(self, association):
        """Remove a place association from the album."""
        try:
            self.controller.delete.delete_entity(
                "PlaceAssociation", association.place_association_id
            )
            self.show_updated_view()
        except Exception as e:
            QMessageBox.critical(
                None, "Error", f"Failed to remove place association: {str(e)}"
            )

    def add_place_association(self):
        """Add a new place association for the album with autocomplete."""
        existing_places = self._get_existing_entities("Place", "place_name")
        existing_types = [
            "Recording Location",
            "Production Location",
            "Origin",
            "Other",
        ]

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
                    "completer_data": existing_types,
                    "placeholder": "Enter association type",
                },
            ],
            "Add Place Association",
            None,
        )

        if not result or not result["place_name"] or not result["association_type"]:
            QMessageBox.warning(
                None, "Warning", "Both place name and association type are required."
            )
            return

        try:
            # Find or create the place
            place = self.controller.get.get_entity_object(
                "Place", place_name=result["place_name"]
            )
            if not place:
                place = self.controller.add.add_entity(
                    "Place", place_name=result["place_name"]
                )

            # Create the association
            params = dict(
                place_id=place.place_id,
                entity_id=self.album.album_id,
                association_type=result["association_type"],
                entity_type="Album",
            )

            self.controller.add.add_entity("PlaceAssociation", **params)
            self.show_updated_view()
            QMessageBox.information(
                None, "Success", "Place association added successfully!"
            )

        except Exception as e:
            logger.exception("Failed to add place association")
            QMessageBox.critical(
                None, "Error", f"Failed to add place association: {str(e)}"
            )

    def add_album_award(self):
        """Add a new award to the album."""
        result = AutocompleteDialog.get_inputs(
            [
                {
                    "name": "award_name",
                    "label": "Award Name:",
                    "type": "text",
                    "placeholder": "Enter award name...",
                },
                {
                    "name": "award_year",
                    "label": "Award Year:",
                    "type": "spin",
                    "min": 1900,
                    "max": 2100,
                    "default": 2024,
                },
                {
                    "name": "award_category",
                    "label": "Award Category:",
                    "type": "text",
                    "placeholder": "Enter category...",
                },
                {
                    "name": "award_description",
                    "label": "Description:",
                    "type": "textarea",
                    "placeholder": "Enter award description...",
                },
            ],
            "Add Album Award",
            None,
        )

        if not result or not result["award_name"]:
            QMessageBox.warning(None, "Warning", "Award name is required.")
            return

        try:
            # Create the award entity
            award = self.controller.add.add_entity(
                "Award",
                award_name=result["award_name"],
                award_year=result["award_year"],
                award_category=result["award_category"] or None,
                award_description=result["award_description"] or None,
            )

            # Create the association
            self.controller.add.add_entity(
                "AwardAssociation",
                award_id=award.award_id,
                entity_id=self.album.album_id,
                entity_type="Album",
                association_type="recipient",
            )

            self.show_updated_view()
            QMessageBox.information(None, "Success", "Award added successfully!")

        except Exception as e:
            logger.exception("Failed to add album award")
            QMessageBox.critical(None, "Error", f"Failed to add award: {str(e)}")

    def remove_album_award_association(self, award):
        """Remove the association between an album and an award."""
        try:
            # Find the association record
            association = self.controller.get.get_entity(
                "AwardAssociation",
                award_id=award.award_id,
                entity_id=self.album.album_id,
                entity_type="Album",
            )

            if association:
                self.controller.delete.delete_entity(association)
                # Refresh the awards tab
                self.refresh_awards_tab()
            else:
                logger.warning(
                    f"No association found for award {award.award_id} and album {self.album.album_id}"
                )

        except Exception as e:
            logger.error(f"Error removing album award association: {e}")

    def add_publisher(self):
        """Add a new publisher to the album with autocomplete."""
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
            "Add Publisher",
            None,
        )

        if not result or not result["publisher_name"]:
            return

        publisher_name = result["publisher_name"]
        try:
            # Find or create publisher
            publisher = self.controller.get.get_entity_object(
                "Publisher", publisher_name=publisher_name
            )
            if not publisher:
                publisher = self.controller.add.add_entity(
                    "Publisher", publisher_name=publisher_name
                )

            # Create the album-publisher association
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
        """Remove a publisher from the album using the association object."""
        try:
            self.controller.delete.delete_entity(
                "AlbumPublisher", album_publisher.album_publisher_id
            )
            self.show_updated_view()
        except Exception as e:
            QMessageBox.critical(None, "Error", f"Failed to remove publisher: {str(e)}")

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
            "Add Artist Credit",
            None,
        )

        if not result or not result["artist_name"] or not result["role_name"]:
            QMessageBox.warning(None, "Warning", "Both artist and role are required.")
            return

        try:
            # Find or create artist
            artist = self.controller.get.get_entity_object(
                "Artist", artist_name=result["artist_name"]
            )
            if not artist:
                artist = self.controller.add.add_entity(
                    "Artist", artist_name=result["artist_name"]
                )

            # Find or create role
            role = self.controller.get.get_entity_object(
                "Role", role_name=result["role_name"]
            )
            if not role:
                role = self.controller.add.add_entity(
                    "Role", role_name=result["role_name"]
                )

            # Create the album role association
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

    def _get_existing_entities(self, entity_type, name_field="name"):
        """Get existing entities for autocomplete"""
        try:
            entities = self.controller.get.get_all_entities(entity_type)
            return [
                getattr(entity, name_field)
                for entity in entities
                if hasattr(entity, name_field) and getattr(entity, name_field)
            ]
        except Exception as e:
            logger.warning(f"Could not load existing {entity_type}: {e}")
            return []


class AutocompleteDialog:
    """Reusable dialog with autocomplete fields"""

    @staticmethod
    def get_inputs(field_configs, existing_data=None):
        """
        field_configs: list of dicts with:
          - 'label': field label
          - 'type': 'text', 'spin', 'combo', etc.
          - 'completer_data': list for autocomplete
          - 'placeholder': placeholder text
          - 'default': default value
        """
        dialog = QDialog()
        dialog.setMinimumWidth(400)
        layout = QFormLayout(dialog)
        widgets = {}

        for config in field_configs:
            if config["type"] == "text":
                widget = QLineEdit()
                if config.get("placeholder"):
                    widget.setPlaceholderText(config["placeholder"])
                if config.get("completer_data"):
                    completer = QCompleter(config["completer_data"])
                    completer.setCaseSensitivity(Qt.CaseInsensitive)
                    completer.setFilterMode(Qt.MatchContains)
                    widget.setCompleter(completer)
            elif config["type"] == "spin":
                widget = QSpinBox()
                widget.setRange(config.get("min", 1900), config.get("max", 2100))
            elif config["type"] == "textarea":
                widget = QTextEdit()
                widget.setMaximumHeight(100)
                if config.get("placeholder"):
                    widget.setPlaceholderText(config["placeholder"])

            if config.get("default"):
                if hasattr(widget, "setText"):
                    widget.setText(str(config["default"]))
                elif hasattr(widget, "setValue"):
                    widget.setValue(config["default"])

            layout.addRow(config["label"], widget)
            widgets[config["name"]] = widget

        button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        button_box.accepted.connect(dialog.accept)
        button_box.rejected.connect(dialog.reject)
        layout.addRow(button_box)

        if dialog.exec_() == QDialog.Accepted:
            return {
                name: AutocompleteDialog._get_widget_value(widget)
                for name, widget in widgets.items()
            }
        return None

    @staticmethod
    def _get_widget_value(widget):
        if isinstance(widget, (QLineEdit, QTextEdit)):
            return (
                widget.text().strip()
                if isinstance(widget, QLineEdit)
                else widget.toPlainText().strip()
            )
        elif isinstance(widget, QSpinBox):
            return widget.value()
        return None
