"""Dialog for creating a new smart playlist."""

from PySide6.QtWidgets import QMessageBox

from src.playlist.playlist_smart_base_dialog import BaseSmartPlaylistDialog


class SmartPlaylistCreateDialog(BaseSmartPlaylistDialog):
    """Dialog for creating a new smart playlist."""

    def __init__(self, parent=None):
        super().__init__("Create Smart Playlist", "Create", parent)
        self.add_criteria_widget()  # start with one blank row

    def name_placeholder(self) -> str:
        return "My Smart Playlist"

    def _on_ok_clicked(self):
        """Validate before accepting so a blank name or missing criteria
        value never silently discards the form the user just filled in."""
        if not self.name_edit.text().strip():
            QMessageBox.warning(self, "Input Error", "Playlist name cannot be empty.")
            return
        if not self._validate_criteria():
            return
        self.accept()

    def get_data(self):
        """Return (name, description, logic, criteria_list, auto_refresh) for the caller to save."""
        return self._collect_form_data()
