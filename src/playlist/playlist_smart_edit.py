"""
playlist_smart_edit.py

Dialog for editing an existing smart playlist.
Loads the playlist's current name, description, AND/OR logic, and criteria
from the database, lets the user change them, then saves the updates.
"""

import datetime

from PySide6.QtWidgets import QMessageBox
from sqlalchemy.exc import SQLAlchemyError

from src.core.logger_config import logger
from src.playlist.playlist_smart_base_dialog import BaseSmartPlaylistDialog


class SmartPlaylistEditDialog(BaseSmartPlaylistDialog):
    """Dialog for editing an existing smart playlist."""

    def __init__(self, controller, playlist_id: int, parent=None):
        self.controller = controller
        self.playlist_id = playlist_id
        super().__init__("Edit Smart Playlist", "Save", parent)
        self._load_existing_data()

    # ------------------------------------------------------------------
    # Load existing data from the database
    # ------------------------------------------------------------------

    def _load_existing_data(self):
        """Read the playlist's current values and pre-fill the form."""
        try:
            # Load the Playlist row (name, description)
            playlist = self.controller.get.get_entity_object(
                "Playlist", playlist_id=self.playlist_id
            )
            if playlist:
                self.name_edit.setText(playlist.playlist_name or "")
                self.desc_edit.setPlainText(
                    getattr(playlist, "playlist_description", "") or ""
                )

            # Load the SmartPlaylist row (AND / OR logic)
            smart_playlist = self.controller.get.get_entity_object(
                "SmartPlaylist", playlist_id=self.playlist_id
            )
            if smart_playlist:
                logic = (getattr(smart_playlist, "logic", "AND") or "AND").upper()
                index = self.logic_combo.findData(logic)
                if index >= 0:
                    self.logic_combo.setCurrentIndex(index)

                # Load criteria rows
                criteria_rows = self.controller.get.get_all_entities(
                    "SmartPlaylistCriteria",
                    smart_playlist_id=smart_playlist.playlist_id,
                )
                if criteria_rows:
                    for row in criteria_rows:
                        criteria_dict = {
                            "field": getattr(row, "field_name", ""),
                            "comparison": getattr(row, "comparison", "eq"),
                            "value": getattr(row, "value", None),
                            "type": getattr(row, "type", "String"),
                        }
                        self.add_criteria_widget(criteria_dict)
                else:
                    # No criteria saved yet — show one blank row
                    self.add_criteria_widget()
            else:
                # SmartPlaylist record missing — show one blank row
                self.add_criteria_widget()

        except SQLAlchemyError as e:
            logger.error(f"Failed to load smart playlist data: {e}")
            QMessageBox.warning(
                self,
                "Load Error",
                f"Could not load playlist details:\n{e}",
            )
            # Fall back to a blank row so the dialog is still usable
            if not self.criteria_widgets:
                self.add_criteria_widget()

    # ------------------------------------------------------------------
    # Save
    # ------------------------------------------------------------------

    def _on_ok_clicked(self):
        """Validate input, then update the database records and close."""
        name, description, logic, criteria_list = self._collect_form_data()
        if not name:
            QMessageBox.warning(self, "Input Error", "Playlist name cannot be empty.")
            return

        try:
            # 1. Update the Playlist row (name + description)
            self.controller.update.update_entity(
                "Playlist",
                self.playlist_id,
                playlist_name=name,
                playlist_description=description,
                last_modified=datetime.datetime.now(),
            )

            # 2. Update the SmartPlaylist row (logic + timestamp)
            self.controller.update.update_entity(
                "SmartPlaylist",
                self.playlist_id,
                logic=logic,
                last_refreshed=datetime.datetime.now(),
            )

            # 3. Replace all criteria rows:
            #    Delete the old ones, then insert the new ones.
            self.controller.delete.delete_entity(
                "SmartPlaylistCriteria",
                smart_playlist_id=self.playlist_id,
            )

            for criterion in criteria_list:
                self.controller.add.add_entity(
                    "SmartPlaylistCriteria",
                    smart_playlist_id=self.playlist_id,
                    field_name=criterion.get("field", ""),
                    comparison=criterion.get("comparison", ""),
                    value=criterion.get("value", ""),
                    type=criterion.get("type", "String"),
                )

            logger.info(f"Saved edits to smart playlist {self.playlist_id}: {name!r}")
            self.accept()

        except SQLAlchemyError as e:
            logger.error(f"Failed to save smart playlist edits: {e}")
            QMessageBox.critical(
                self,
                "Save Error",
                f"Could not save changes:\n{e}",
            )
