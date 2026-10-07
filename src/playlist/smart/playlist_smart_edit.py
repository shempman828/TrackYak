"""Dialog for editing an existing smart playlist."""

import datetime

from PySide6.QtWidgets import QMessageBox
from sqlalchemy.exc import SQLAlchemyError

from src.db.db_tables import SmartPlaylistCriteria
from src.foundation.logger_config import logger
from src.playlist.smart.playlist_smart_base_dialog import BaseSmartPlaylistDialog
from src.playlist.smart.playlist_smart_builder import condition_to_row_fields, row_to_condition


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
            playlist = self.controller.get.get_entity_object("Playlist", playlist_id=self.playlist_id)
            if playlist:
                self.name_edit.setText(playlist.playlist_name or "")
                self.desc_edit.setPlainText(getattr(playlist, "playlist_description", "") or "")

            # Load the SmartPlaylist row (AND / OR logic)
            smart_playlist = self.controller.get.get_entity_object("SmartPlaylist", playlist_id=self.playlist_id)
            if smart_playlist:
                logic = (getattr(smart_playlist, "logic", "AND") or "AND").upper()
                index = self.logic_combo.findData(logic)
                if index >= 0:
                    self.logic_combo.setCurrentIndex(index)

                self.auto_refresh_check.setChecked(bool(getattr(smart_playlist, "auto_refresh", 0)))

                # Load criteria rows
                criteria_rows = self.controller.get.get_all_entities("SmartPlaylistCriteria", smart_playlist_id=smart_playlist.playlist_id)
                if criteria_rows:
                    for row in criteria_rows:
                        self.add_criteria_widget(row_to_condition(row))
                else:
                    # No criteria saved yet — show one blank row
                    self.add_criteria_widget()
            else:
                # SmartPlaylist record missing — show one blank row
                self.add_criteria_widget()

        except SQLAlchemyError as e:
            logger.error(f"Failed to load smart playlist data: {e}")
            QMessageBox.warning(self, "Load Error", f"Could not load playlist details:\n{e}")
            # Fall back to a blank row so the dialog is still usable
            if not self.criteria_widgets:
                self.add_criteria_widget()

    # ------------------------------------------------------------------
    # Save
    # ------------------------------------------------------------------

    def _on_ok_clicked(self):
        """Validate input, then update the database records and close."""
        name, description, logic, criteria_list, auto_refresh = self._collect_form_data()
        if not name:
            QMessageBox.warning(self, "Input Error", "Playlist name cannot be empty.")
            return
        if not self._validate_criteria():
            return

        now = datetime.datetime.now()
        # update_entity logs and returns False on failure instead of raising.
        saved = self.controller.update.update_entity("Playlist", self.playlist_id, playlist_name=name, playlist_description=description, last_modified=now) and self.controller.update.update_entity(
            "SmartPlaylist", self.playlist_id, logic=logic, auto_refresh=int(auto_refresh), last_refreshed=now
        )
        if not saved or not self._replace_criteria(criteria_list):
            QMessageBox.critical(self, "Save Error", "Could not save changes. Check the log for details.")
            return

        logger.info(f"Saved edits to smart playlist {self.playlist_id}: {name!r}")
        self.accept()

    def _replace_criteria(self, criteria_list: list[dict]) -> bool:
        """Replace this playlist's criteria rows in one transaction; True on success."""
        session = self.controller.get.session
        try:
            session.query(SmartPlaylistCriteria).filter(SmartPlaylistCriteria.smart_playlist_id == self.playlist_id).delete(synchronize_session=False)
            session.add_all(SmartPlaylistCriteria(smart_playlist_id=self.playlist_id, **condition_to_row_fields(c)) for c in criteria_list)
            session.commit()
            return True
        except SQLAlchemyError as e:
            # Roll back so a failed insert can't leave the old criteria deleted.
            logger.error(f"Failed to save smart playlist {self.playlist_id} criteria: {e}")
            session.rollback()
            return False
