from typing import Any

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from logger_config import logger


class AddMemberDialog(QDialog):
    """Dialog for adding a member to a group"""

    def __init__(self, controller: Any, group: Any, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.group = group
        self.setWindowTitle(f"Add Member to {group.artist_name}")
        self.setMinimumWidth(400)
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)

        # Artist selection
        layout.addWidget(QLabel("Select Artist:"))
        self.artist_combo = QComboBox()
        self._load_artists()
        layout.addWidget(self.artist_combo)

        # Role
        layout.addWidget(QLabel("Role (optional):"))
        self.role_edit = QLineEdit()
        layout.addWidget(self.role_edit)

        # Active period
        period_layout = QGridLayout()
        period_layout.addWidget(QLabel("Start Year:"), 0, 0)
        self.start_year = QSpinBox()
        self.start_year.setRange(0, 2999)
        self.start_year.setSpecialValueText("")
        period_layout.addWidget(self.start_year, 0, 1)

        period_layout.addWidget(QLabel("End Year:"), 0, 2)
        self.end_year = QSpinBox()
        self.end_year.setRange(0, 2999)
        self.end_year.setSpecialValueText("")
        period_layout.addWidget(self.end_year, 0, 3)

        self.current_checkbox = QCheckBox("Currently active")
        self.current_checkbox.toggled.connect(self._toggle_current)
        period_layout.addWidget(self.current_checkbox, 1, 0, 1, 2)

        layout.addLayout(period_layout)

        # Buttons
        button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        button_box.accepted.connect(self._add_member)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

    def _load_artists(self):
        """Load all individual artists (non-groups)"""
        try:
            self.artist_combo.clear()

            # Get all artists that are not groups - handle both isgroup=0 and isgroup=None
            artists = self.controller.get.get_all_entities("Artist")
            individual_artists = [
                a
                for a in artists
                if not getattr(a, "isgroup", 0)  # Handle None, 0, False
            ]

            # Sort alphabetically
            sorted_artists = sorted(
                individual_artists, key=lambda a: a.artist_name.lower()
            )

            self.artist_combo.addItem("-- Select Artist --", None)
            for artist in sorted_artists:
                self.artist_combo.addItem(artist.artist_name, artist.artist_id)

        except Exception as e:
            logger.error(f"Error loading artists: {e}")
            self.artist_combo.addItem("Error loading artists", None)

    def _toggle_current(self, checked):
        """Toggle end year based on current status"""
        self.end_year.setEnabled(not checked)
        if checked:
            self.end_year.setValue(0)

    def _add_member(self):
        """Add the selected member to the group"""
        artist_id = self.artist_combo.currentData()
        if not artist_id:
            return

        try:
            membership_data = {
                "group_id": self.group.artist_id,
                "member_id": artist_id,
                "role": self.role_edit.text() or None,
                "active_start_year": self.start_year.value() or None,
                "active_end_year": self.end_year.value() or None,
                "is_current": self.current_checkbox.isChecked(),
            }

            self.controller.add.add_entity("GroupMembership", **membership_data)
            self.accept()
            logger.info(f"Added member {artist_id} to group {self.group.artist_name}")

        except Exception as e:
            logger.error(f"Error adding member: {e}")


class AddGroupDialog(QDialog):
    """Dialog for adding a new group or converting existing artist"""

    def __init__(self, controller: Any, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.setWindowTitle("Add New Group")
        self.setMinimumWidth(400)
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)

        # Tab widget for different creation methods
        self.tab_widget = QTabWidget()

        # Tab 1: New Group
        new_group_tab = QWidget()
        new_layout = QVBoxLayout(new_group_tab)
        new_layout.addWidget(QLabel("Group Name:"))
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("Enter new group name...")
        new_layout.addWidget(self.name_edit)
        new_layout.addStretch()
        self.tab_widget.addTab(new_group_tab, "Create New Group")

        # Tab 2: Convert Existing Artist
        convert_tab = QWidget()
        convert_layout = QVBoxLayout(convert_tab)
        convert_layout.addWidget(QLabel("Select Artist to Convert:"))
        self.artist_combo = QComboBox()
        self._load_artists()
        convert_layout.addWidget(self.artist_combo)
        convert_layout.addStretch()
        self.tab_widget.addTab(convert_tab, "Convert Existing Artist")

        layout.addWidget(self.tab_widget)

        # Buttons
        button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        button_box.accepted.connect(self._create_group)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

    def _load_artists(self):
        """Load all individual artists (non-groups)"""
        try:
            self.artist_combo.clear()

            # Get all artists that are not currently groups
            artists = self.controller.get.get_all_entities("Artist")
            individual_artists = [a for a in artists if not getattr(a, "isgroup", 0)]

            # Sort alphabetically
            sorted_artists = sorted(
                individual_artists, key=lambda a: a.artist_name.lower()
            )

            self.artist_combo.addItem("-- Select Artist --", None)
            for artist in sorted_artists:
                self.artist_combo.addItem(artist.artist_name, artist.artist_id)

        except Exception as e:
            logger.error(f"Error loading artists: {e}")
            self.artist_combo.addItem("Error loading artists", None)

    def _create_group(self):
        """Create the new group or convert existing artist"""
        try:
            current_tab = self.tab_widget.currentIndex()

            if current_tab == 0:  # New Group tab
                name = self.name_edit.text().strip()
                if not name:
                    QMessageBox.warning(
                        self, "Input Error", "Please enter a group name"
                    )
                    return

                self.controller.add.add_entity("Artist", artist_name=name, isgroup=1)
                logger.info(f"Created new group: {name}")

            else:  # Convert Artist tab
                artist_id = self.artist_combo.currentData()
                if not artist_id:
                    QMessageBox.warning(
                        self, "Input Error", "Please select an artist to convert"
                    )
                    return

                self.controller.update.update_entity("Artist", artist_id, isgroup=1)
                artist = self.controller.get.get_entity_object(
                    "Artist", artist_id=artist_id
                )
                logger.info(f"Converted artist to group: {artist.artist_name}")

            self.accept()

        except Exception as e:
            logger.error(f"Error creating group: {e}")
            QMessageBox.critical(self, "Error", f"Failed to create group: {str(e)}")
