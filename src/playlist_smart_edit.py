"""
playlist_smart_edit.py

Dialog for editing an existing smart playlist.
Loads the playlist's current name, description, AND/OR logic, and criteria
from the database, lets the user change them, then saves the updates.
"""

import datetime

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from src.logger_config import logger
from src.playlist_smart_criteria_widget import CriteriaWidget


class SmartPlaylistEditDialog(QDialog):
    """Dialog for editing an existing smart playlist."""

    def __init__(self, controller, playlist_id: int, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.playlist_id = playlist_id
        self.criteria_widgets = []

        self.setWindowTitle("Edit Smart Playlist")
        self.setMinimumWidth(750)
        self.setMinimumHeight(400)

        self.init_ui()
        self._load_existing_data()

    # ------------------------------------------------------------------
    # UI setup
    # ------------------------------------------------------------------

    def init_ui(self):
        """Build the dialog layout (same structure as the Create dialog)."""
        layout = QVBoxLayout(self)

        # --- Name and description ---
        form_layout = QFormLayout()

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("Playlist name")
        form_layout.addRow("Playlist Name:", self.name_edit)

        self.desc_edit = QTextEdit()
        self.desc_edit.setMaximumHeight(60)
        self.desc_edit.setPlaceholderText("Optional description...")
        form_layout.addRow("Description:", self.desc_edit)

        layout.addLayout(form_layout)

        # --- AND / OR logic toggle ---
        logic_layout = QHBoxLayout()
        logic_label = QLabel("<b>Match</b>")
        self.logic_combo = QComboBox()
        self.logic_combo.addItem("ALL of the following conditions (AND)", "AND")
        self.logic_combo.addItem("ANY of the following conditions (OR)", "OR")
        logic_layout.addWidget(logic_label)
        logic_layout.addWidget(self.logic_combo)
        logic_layout.addStretch()
        layout.addLayout(logic_layout)

        # --- Criteria section ---
        layout.addWidget(QLabel("<b>Criteria:</b>"))

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMaximumHeight(300)

        self.criteria_container_widget = QWidget()
        self.criteria_container = QVBoxLayout(self.criteria_container_widget)
        self.criteria_container.setSpacing(4)
        self.criteria_container.addStretch()  # keeps rows pinned to the top

        scroll.setWidget(self.criteria_container_widget)
        layout.addWidget(scroll)

        # Add Criteria button
        self.add_btn = QPushButton("+ Add Another Criteria")
        self.add_btn.clicked.connect(self.add_criteria_widget)
        layout.addWidget(self.add_btn)

        # --- Dialog buttons ---
        button_layout = QHBoxLayout()
        button_layout.addStretch()

        self.ok_btn = QPushButton("Save")
        self.ok_btn.setDefault(True)
        self.ok_btn.clicked.connect(self._save)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self.reject)

        button_layout.addWidget(self.ok_btn)
        button_layout.addWidget(self.cancel_btn)
        layout.addLayout(button_layout)

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

        except Exception as e:
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
    # Criteria row management
    # ------------------------------------------------------------------

    def add_criteria_widget(self, criteria_dict: dict = None):
        """
        Add a criteria row to the dialog.

        If criteria_dict is given, the row is pre-filled with those values.
        Otherwise a blank row is added.
        """
        widget = CriteriaWidget()
        widget.delete_requested.connect(self.remove_criteria_widget)

        # Insert before the stretch (last item in the layout)
        count = self.criteria_container.count()
        self.criteria_container.insertWidget(count - 1, widget)
        self.criteria_widgets.append(widget)

        if criteria_dict:
            widget.set_criteria(criteria_dict)

    def remove_criteria_widget(self, widget):
        """Remove a criteria row, but always keep at least one."""
        if len(self.criteria_widgets) <= 1:
            return
        if widget in self.criteria_widgets:
            self.criteria_widgets.remove(widget)
            widget.setParent(None)
            widget.deleteLater()

    # ------------------------------------------------------------------
    # Save
    # ------------------------------------------------------------------

    def _save(self):
        """Validate input, then update the database records and close."""
        name = self.name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "Input Error", "Playlist name cannot be empty.")
            return

        description = self.desc_edit.toPlainText().strip()
        logic = self.logic_combo.currentData()  # "AND" or "OR"
        criteria_list = [w.get_criteria() for w in self.criteria_widgets]

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
            self.controller.delete.delete_entity_by_filter(
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

        except Exception as e:
            logger.error(f"Failed to save smart playlist edits: {e}")
            QMessageBox.critical(
                self,
                "Save Error",
                f"Could not save changes:\n{e}",
            )
