"""
playlist_smart_new.py

Dialog for creating a new smart playlist.
Lets the user enter a name, description, AND/OR logic, and one or more criteria rows.
"""

from src.playlist.playlist_smart_base_dialog import BaseSmartPlaylistDialog


class SmartPlaylistCreateDialog(BaseSmartPlaylistDialog):
    """Dialog for creating a new smart playlist."""

    def __init__(self, parent=None):
        super().__init__("Create Smart Playlist", "Create", parent)
        self.add_criteria_widget()  # start with one blank row

    def name_placeholder(self) -> str:
        return "My Smart Playlist"

    def _on_ok_clicked(self):
        self.accept()

    def get_data(self):
        """
        Return (name, description, logic, criteria_list).

        criteria_list is a list of dicts like:
            [{"field": "user_rating", "comparison": "gt", "value": 5.5, "type": "Float"}, ...]
        logic is "AND" or "OR".
        """
        return self._collect_form_data()
