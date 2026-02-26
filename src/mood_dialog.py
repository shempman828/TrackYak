from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from src.base_track_view import BaseTrackView  # Import the BaseTrackView
from src.logger_config import logger


class MoodDialog(QDialog):
    """Dialog for creating/editing moods with track associations"""

    def __init__(self, mood_data=None, controller=None, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.mood_data = mood_data
        self.is_editing = mood_data is not None
        self.mood_id = mood_data.mood_id if mood_data else None
        self.recursive_mode = False  # Default to exclusive mode

        # NEW: Track for context menu enhancement
        self.enhanced_context_menu = None

        self.setWindowTitle("Edit Mood" if self.is_editing else "Create New Mood")
        self.setModal(True)
        self.setMinimumWidth(800)
        self.setMinimumHeight(600)

        self.init_ui()
        if self.is_editing:
            self.load_mood_data()

    def init_ui(self):
        layout = QVBoxLayout(self)

        # Create tabs only if editing
        if self.is_editing:
            tabs = QTabWidget()

            # Mood details tab
            details_tab = QWidget()
            details_layout = QVBoxLayout(details_tab)

            # Mood information form
            form_group = QGroupBox("Mood Information")
            form_layout = QFormLayout()

            self.mood_name_edit = QLineEdit()
            self.mood_name_edit.setPlaceholderText("Enter mood name")
            form_layout.addRow("Name:", self.mood_name_edit)

            self.mood_description_edit = QTextEdit()
            self.mood_description_edit.setMaximumHeight(100)
            self.mood_description_edit.setPlaceholderText("Enter mood description")
            form_layout.addRow("Description:", self.mood_description_edit)

            form_group.setLayout(form_layout)
            details_layout.addWidget(form_group)

            details_layout.addStretch()
            tabs.addTab(details_tab, "Details")

            # Associated tracks tab with BaseTrackView
            tracks_tab = QWidget()
            tracks_layout = QVBoxLayout(tracks_tab)

            # Search and controls layout
            controls_layout = QHBoxLayout()

            # Add recursive toggle button
            self.btn_recursive_mode = QPushButton("Recursive: Off")
            self.btn_recursive_mode.setCheckable(True)
            self.btn_recursive_mode.setChecked(False)
            self.btn_recursive_mode.clicked.connect(self.toggle_recursive_mode)
            controls_layout.addWidget(self.btn_recursive_mode)

            controls_layout.addStretch()
            tracks_layout.addLayout(controls_layout)

            # Associated tracks view using BaseTrackView
            associated_group = QGroupBox("Associated Tracks")
            associated_layout = QVBoxLayout()

            # Create a container widget for the track view
            track_container = QWidget()
            track_container_layout = QVBoxLayout(track_container)
            track_container_layout.setContentsMargins(0, 0, 0, 0)

            # Initialize BaseTrackView
            self.track_view = BaseTrackView(
                controller=self.controller,
                tracks=[],  # Will be populated in load_associated_tracks
                title="Mood Tracks",
                enable_drag=False,
                enable_drop=False,
            )

            # Don't set window flags - keep it as a regular widget
            # Instead, extract the table and other components
            track_container_layout.addWidget(self.track_view.table)

            # Add search bar and info label if needed
            track_container_layout.addWidget(self.track_view.search_bar)
            track_container_layout.addWidget(self.track_view.info_label)

            associated_layout.addWidget(track_container)
            associated_group.setLayout(associated_layout)
            tracks_layout.addWidget(associated_group)

            tabs.addTab(tracks_tab, "Associated Tracks")

            layout.addWidget(tabs)

            # Load data for editing mode
            self.load_associated_tracks()
        else:
            # Mood information form
            form_group = QGroupBox("Mood Information")
            form_layout = QFormLayout()

            self.mood_name_edit = QLineEdit()
            self.mood_name_edit.setPlaceholderText("Enter mood name")
            form_layout.addRow("Name:", self.mood_name_edit)

            self.mood_description_edit = QTextEdit()
            self.mood_description_edit.setMaximumHeight(100)
            self.mood_description_edit.setPlaceholderText("Enter mood description")
            form_layout.addRow("Description:", self.mood_description_edit)

            form_group.setLayout(form_layout)
            layout.addWidget(form_group)

            layout.addStretch()

        # Dialog buttons
        button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

    def toggle_recursive_mode(self):
        """Toggle between recursive and exclusive track display"""
        self.recursive_mode = self.btn_recursive_mode.isChecked()
        if self.recursive_mode:
            self.btn_recursive_mode.setText("Recursive: On")
        else:
            self.btn_recursive_mode.setText("Recursive: Off")

        # Reload tracks with new mode
        if self.is_editing:
            self.load_associated_tracks()

    def load_associated_tracks(self):
        """Load tracks associated with current mood"""
        if not self.is_editing or not hasattr(self, "track_view"):
            return

        try:
            # Get track associations for this mood, with recursive option
            if self.recursive_mode:
                # Use the parent MoodView's method to get recursive tracks
                parent_view = self.parent()
                if hasattr(parent_view, "get_tracks_for_mood"):
                    tracks = parent_view.get_tracks_for_mood(
                        self.mood_id, include_children=True
                    )
                else:
                    # Fallback: get all child mood IDs and their tracks
                    all_mood_ids = [self.mood_id] + self.get_child_mood_ids(
                        self.mood_id
                    )
                    tracks = []
                    for mood_id in all_mood_ids:
                        associations = self.controller.get.get_all_entities(
                            "MoodTrackAssociation", mood_id=mood_id
                        )
                        for association in associations:
                            if hasattr(association, "track"):
                                tracks.append(association.track)
                            else:
                                track = self.controller.get.get_entity_by_id(
                                    "Track", association.track_id
                                )
                                if track and track not in tracks:
                                    tracks.append(track)
            else:
                # Just get tracks for this specific mood
                associations = self.controller.get.get_all_entities(
                    "MoodTrackAssociation", mood_id=self.mood_id
                )
                tracks = []
                for association in associations:
                    if hasattr(association, "track"):
                        tracks.append(association.track)
                    else:
                        track = self.controller.get.get_entity_by_id(
                            "Track", association.track_id
                        )
                        if track:
                            tracks.append(track)

            # Update the BaseTrackView with the tracks
            self.track_view.load_data(tracks)

            # Update the info label in track view
            self.track_view.info_label.setText(f"Showing {len(tracks)} tracks")

            # NEW: Enhance the context menu with remove option
            self.enhance_track_view_context_menu()

        except Exception as e:
            logger.error(f"Error loading associated tracks: {e}")
        self.track_view.info_label.setText("Error loading tracks")

    def get_child_mood_ids(self, parent_mood_id):
        """Get all child mood IDs recursively for a given parent mood"""
        try:
            all_moods = self.controller.get.get_all_entities("Mood")
            child_ids = []

            def collect_children(mood_id):
                children = [m.mood_id for m in all_moods if m.parent_id == mood_id]
                for child_id in children:
                    child_ids.append(child_id)
                    collect_children(child_id)

            collect_children(parent_mood_id)
            return child_ids
        except Exception as e:
            logger.error(f"Error getting child mood IDs: {e}")
            return []

    def load_mood_data(self):
        """Load existing mood data into form"""
        if not self.mood_data:
            return

        self.mood_name_edit.setText(self.mood_data.mood_name or "")
        self.mood_description_edit.setText(self.mood_data.mood_description or "")

    def get_mood_data(self):
        """Return the mood data from the form"""
        return {
            "mood_name": self.mood_name_edit.text().strip(),
            "mood_description": self.mood_description_edit.toPlainText().strip(),
        }

    def enhance_track_view_context_menu(self):
        """Add remove from mood option to the track view's context menu."""
        if not hasattr(self, "track_view") or not self.is_editing:
            return

        # Get the existing context menu from track view
        if hasattr(self.track_view, "context_menu"):
            # Add separator and remove action directly to the existing menu

            # Check if remove action already exists
            existing_actions = [
                action.text() for action in self.track_view.context_menu.actions()
            ]
            if "Remove from This Mood" not in existing_actions:
                # Add separator and remove action
                self.track_view.context_menu.addSeparator()

                self.remove_from_mood_action = self.track_view.context_menu.addAction(
                    "Remove from This Mood"
                )
                self.remove_from_mood_action.triggered.connect(
                    self.remove_selected_tracks_from_mood
                )

            # Override the track view's context menu setup to ensure our action is always available
            # Store original setup method
            original_setup_context_menu = self.track_view.setup_context_menu

            def enhanced_setup_context_menu():
                # Call original setup
                original_setup_context_menu()

                # Add our custom action to the menu
                if hasattr(self.track_view, "context_menu"):
                    # Check if remove action already exists
                    existing_texts = [
                        action.text()
                        for action in self.track_view.context_menu.actions()
                    ]
                    if "Remove from This Mood" not in existing_texts:
                        self.track_view.context_menu.addSeparator()
                        remove_action = self.track_view.context_menu.addAction(
                            "Remove from This Mood"
                        )
                        remove_action.triggered.connect(
                            self.remove_selected_tracks_from_mood
                        )

            # Replace the setup method
            self.track_view.setup_context_menu = enhanced_setup_context_menu

            # Re-run setup to ensure our action is added
            self.track_view.setup_context_menu()

            # Also enhance the show_context_menu to ensure proper parent-child relationship
            original_show = self.track_view.show_context_menu

            def enhanced_show(position):
                # Call original but ensure proper Wayland transient parent
                if original_show:
                    original_show(position)

            self.track_view.show_context_menu = enhanced_show

    def remove_selected_tracks_from_mood(self):
        """Remove selected tracks from the current mood."""
        if not self.is_editing or not self.mood_id:
            return

        selected_tracks = self.track_view.get_selected_tracks()
        if not selected_tracks:
            return

        # Confirm removal
        from PySide6.QtWidgets import QMessageBox

        reply = QMessageBox.question(
            self,
            "Remove Tracks",
            f"Remove {len(selected_tracks)} track(s) from this mood?\n\n"
            "Note: In recursive mode, tracks will only be removed from this specific mood, not child moods.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )

        if reply != QMessageBox.Yes:
            return

        success_count = 0
        failed_tracks = []

        try:
            for track in selected_tracks:
                # Get the MoodTrackAssociation for this mood and track
                associations = self.controller.get.get_entity_links(
                    "MoodTrackAssociation",
                    mood_id=self.mood_id,
                    track_id=track.track_id,
                )

                if associations:
                    # Use the first association found
                    association = associations[0]

                    # Try different possible ID attribute names
                    association_id = getattr(association, "id", None)
                    if association_id is None:
                        association_id = getattr(association, "association_id", None)

                    # If we still don't have an ID, try to get the mood_track_association directly
                    if association_id is None:
                        # The association might be a tuple or have different structure
                        # Try to delete using mood_id and track_id directly
                        if hasattr(
                            self.controller.delete, "delete_mood_track_association"
                        ):
                            if self.controller.delete.delete_mood_track_association(
                                mood_id=self.mood_id, track_id=track.track_id
                            ):
                                success_count += 1
                            else:
                                failed_tracks.append(track.track_name)
                        else:
                            # Fallback: try direct deletion using the generic method with composite key
                            result = self.controller.delete.delete_entity(
                                "MoodTrackAssociation",
                                mood_id=self.mood_id,
                                track_id=track.track_id,
                            )
                            if result:
                                success_count += 1
                            else:
                                failed_tracks.append(track.track_name)
                    else:
                        # Delete using the association ID
                        if self.controller.delete.delete_entity(
                            "MoodTrackAssociation", association_id=association_id
                        ):
                            success_count += 1
                        else:
                            failed_tracks.append(track.track_name)
                else:
                    failed_tracks.append(track.track_name)

            # Show result
            if success_count == len(selected_tracks):
                pass
            elif success_count > 0:
                QMessageBox.warning(
                    self,
                    "Partial Success",
                    f"Removed {success_count} of {len(selected_tracks)} track(s).\n"
                    f"Failed to remove: {', '.join(failed_tracks[:5])}"
                    f"{'...' if len(failed_tracks) > 5 else ''}",
                )
            else:
                QMessageBox.warning(
                    self, "Failed", "Could not remove any tracks from the mood."
                )

            # Refresh the track list
            self.load_associated_tracks()

        except Exception as e:
            logger.error(f"Error removing tracks from mood: {e}")
            QMessageBox.critical(
                self, "Error", f"An error occurred while removing tracks:\n{str(e)}"
            )
