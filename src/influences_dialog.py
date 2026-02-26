from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)

from logger_config import logger


class AddInfluenceDialog(QDialog):
    def __init__(self, controller, all_artists, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.all_artists = all_artists  # List of (artist_id, artist_name)
        self.filtered_artists = all_artists.copy()
        self.created_artists = []
        self.added_influence = None
        self.setWindowTitle("Add Influence Relationship")
        self.setModal(True)
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout()

        # Influencer (parent) selection with search
        layout.addWidget(QLabel("Influencer (who influenced):"))
        self.influencer_search = QLineEdit()
        self.influencer_search.setPlaceholderText("Search or type new artist name...")
        self.influencer_search.textChanged.connect(self.filter_influencer_artists)
        layout.addWidget(self.influencer_search)

        # Status label for influencer
        self.influencer_status = QLabel("")
        layout.addWidget(self.influencer_status)

        # Influenced (child) selection with search
        layout.addWidget(QLabel("Influenced (who was influenced):"))
        self.influenced_search = QLineEdit()
        self.influenced_search.setPlaceholderText("Search or type new artist name...")
        self.influenced_search.textChanged.connect(self.filter_influenced_artists)
        layout.addWidget(self.influenced_search)

        # Status label for influenced
        self.influenced_status = QLabel("")
        layout.addWidget(self.influenced_status)

        # Description
        layout.addWidget(QLabel("Description (optional):"))
        self.description_edit = QTextEdit()
        self.description_edit.setMaximumHeight(80)
        layout.addWidget(self.description_edit)

        # Buttons
        button_layout = QHBoxLayout()
        self.add_button = QPushButton("Add Influence")
        self.add_button.clicked.connect(self.add_influence)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self.reject)

        button_layout.addWidget(self.add_button)
        button_layout.addWidget(self.cancel_button)
        layout.addLayout(button_layout)

        self.setLayout(layout)
        self.resize(500, 350)

    def filter_influencer_artists(self, text):
        """Filter artists for influencer search"""
        self.filter_artists(text, "influencer")

    def filter_influenced_artists(self, text):
        """Filter artists for influenced search"""
        self.filter_artists(text, "influenced")

    def filter_artists(self, text, role):
        """Filter artists based on search text and update visual indicators"""
        search_field = (
            self.influencer_search if role == "influencer" else self.influenced_search
        )
        status_label = (
            self.influencer_status if role == "influencer" else self.influenced_status
        )

        if not text.strip():
            status_label.setText("")
            return

        # Check if any existing artist matches
        matches = [
            (id, name) for id, name in self.all_artists if text.lower() in name.lower()
        ]
        exact_matches = [
            (id, name) for id, name in self.all_artists if name.lower() == text.lower()
        ]

        if exact_matches:
            # Exact match found
            artist_id, artist_name = exact_matches[0]
            status_label.setText(f"✓ Using existing artist: '{artist_name}'")
            search_field.setToolTip(f"Exact match: {artist_name}")

        elif matches:
            # Partial matches found
            match_names = [name for _, name in matches[:5]]  # Show first 5 matches
            status_text = f"Partial matches: {', '.join(match_names)}"
            if len(matches) > 5:
                status_text += f" (+{len(matches) - 5} more)"

            status_label.setText(status_text)

            tooltip_text = "Matches: " + ", ".join(match_names)
            if len(matches) > 5:
                tooltip_text += f"... and {len(matches) - 5} more"
            search_field.setToolTip(tooltip_text)

        else:
            # No matches - will create new artist
            status_label.setText(f"✎ Will create new artist: '{text}'")
            search_field.setToolTip(f"Will create new artist: '{text}'")

    def get_or_create_artist_id(self, artist_name):
        """Get existing artist ID or create new artist"""
        # First try to find exact match
        for artist_id, name in self.all_artists:
            if name.lower() == artist_name.lower():
                return artist_id, False  # False = not newly created

        # Create new artist
        try:
            new_artist = self.controller.add.add_entity(
                "Artist", artist_name=artist_name
            )
            # Extract the artist_id from the returned Artist object
            new_artist_id = new_artist.artist_id
            # Refresh the artists list
            artists = self.controller.get.get_all_entities("Artist")
            self.all_artists = [
                (artist.artist_id, artist.artist_name) for artist in artists
            ]
            # Store the newly created artist
            self.created_artists.append((new_artist_id, artist_name))
            return new_artist_id, True  # True = newly created
        except Exception as e:
            raise Exception(f"Failed to create new artist '{artist_name}': {str(e)}")

    def add_influence(self):
        influencer_name = self.influencer_search.text().strip()
        influenced_name = self.influenced_search.text().strip()
        description = self.description_edit.toPlainText().strip()

        # Validate
        if not influencer_name or not influenced_name:
            QMessageBox.warning(
                self,
                "Error",
                "Please enter both influencer and influenced artist names!",
            )
            return

        if influencer_name.lower() == influenced_name.lower():
            QMessageBox.warning(self, "Error", "An artist cannot influence themselves!")
            return

        try:
            # Clear previous created artists list
            self.created_artists = []

            # Get or create artist IDs (now returns tuple with creation flag)
            influencer_id, influencer_created = self.get_or_create_artist_id(
                influencer_name
            )
            influenced_id, influenced_created = self.get_or_create_artist_id(
                influenced_name
            )

            # Create the influence relationship
            influence_data = {
                "influencer_id": influencer_id,
                "influenced_id": influenced_id,
                "description": description if description else None,
            }

            # Use controller to add entity
            self.controller.add.add_entity("ArtistInfluence", **influence_data)

            self.accept()

        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to add influence: {str(e)}")

    def get_created_artists(self):
        """Return list of newly created artists (artist_id, artist_name)"""
        return self.created_artists


class RemoveInfluenceDialog(QDialog):
    def __init__(self, controller, all_influences, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.all_influences = all_influences
        self.setWindowTitle("Remove Influence Relationship")
        self.setModal(True)
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout()

        # Search
        layout.addWidget(QLabel("Search Influence Relationships:"))
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("Search by artist name...")
        self.search_box.textChanged.connect(self.filter_influences)
        layout.addWidget(self.search_box)

        # Search status
        self.search_status = QLabel("Type to search...")
        layout.addWidget(self.search_status)

        # Results list - USE QListWidget instead of QTextEdit
        layout.addWidget(QLabel("Select Relationship to Remove:"))
        self.results_list = QListWidget()
        self.results_list.itemClicked.connect(self.on_item_selected)
        layout.addWidget(self.results_list)

        # Selected item display
        self.selected_display = QLabel("No relationship selected")
        layout.addWidget(self.selected_display)

        # Buttons
        button_layout = QHBoxLayout()
        self.remove_button = QPushButton("Remove Influence")
        self.remove_button.clicked.connect(self.remove_influence)
        self.remove_button.setEnabled(False)

        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self.reject)

        button_layout.addWidget(self.remove_button)
        button_layout.addWidget(self.cancel_button)
        layout.addLayout(button_layout)

        self.setLayout(layout)
        self.resize(500, 400)

    def filter_influences(self, text):
        """Filter and display results in the list widget"""
        self.results_list.clear()

        if not text.strip():
            # Show all when search is empty
            influences_to_show = self.all_influences
            self.search_status.setText(
                f"Showing all {len(influences_to_show)} relationships"
            )
        else:
            # Filter based on search text
            search_lower = text.lower()
            influences_to_show = []
            for inf in self.all_influences:
                influencer_name = inf["influencer_name"].lower()
                influenced_name = inf["influenced_name"].lower()

                if search_lower in influencer_name or search_lower in influenced_name:
                    influences_to_show.append(inf)

            self.search_status.setText(f"Found {len(influences_to_show)} relationships")

        # Populate the list widget
        for inf in influences_to_show:
            item_text = f"{inf['influencer_name']} → {inf['influenced_name']}"
            item = QListWidgetItem(item_text)
            item.setData(1000, inf)  # Store the influence data in the item
            self.results_list.addItem(item)

        # Clear selection when filtering
        self.remove_button.setEnabled(False)
        self.selected_display.setText("No relationship selected")

    def on_item_selected(self, item):
        """Handle when user clicks an item in the list"""
        influence_data = item.data(1000)
        self.selected_influence = influence_data

        influencer_name = influence_data["influencer_name"]
        influenced_name = influence_data["influenced_name"]

        self.selected_display.setText(
            f"Selected: {influencer_name} → {influenced_name}"
        )
        self.remove_button.setEnabled(True)

    def remove_influence(self):
        """Remove the selected influence relationship"""
        if not self.selected_influence:
            QMessageBox.warning(
                self, "Error", "Please select a relationship to remove!"
            )
            return

        try:
            influencer_id = self.selected_influence["influencer_id"]
            influenced_id = self.selected_influence["influenced_id"]
            influencer_name = self.selected_influence["influencer_name"]
            influenced_name = self.selected_influence["influenced_name"]

            # Confirm deletion
            reply = QMessageBox.question(
                self,
                "Confirm Removal",
                f"Remove this influence relationship?\n\n{influencer_name} → {influenced_name}",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )

            if reply == QMessageBox.Yes:
                success = self.controller.delete.delete_entity(
                    "ArtistInfluence",
                    influencer_id=influencer_id,
                    influenced_id=influenced_id,
                )
                if success:
                    QMessageBox.information(
                        self,
                        "Success",
                        f"Influence relationship removed:\n{influencer_name} → {influenced_name}",
                    )
                    self.accept()
                else:
                    QMessageBox.critical(self, "Error", "Failed to remove relationship")

        except Exception as e:
            logger.error(f"Error removing influence: {e}")
            QMessageBox.critical(self, "Error", f"Failed to remove: {str(e)}")
