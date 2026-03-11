from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from src.disc_edit import DiscEditDialog
from src.disc_sorting import TrackSortingDisplay
from src.logger_config import logger


class DiscManagementView(QWidget):
    """
    Main widget for managing disc structure of an album.
    Displays tracks in their natural hierarchy and allows disc creation/editing.
    """

    def __init__(self, album, controller, parent=None):
        super().__init__(parent)
        self.album = album
        self.controller = controller
        self.tracks = []
        self.discs = []

        # Track display widget (we'll create this class next)
        self.track_display = None

        self.init_ui()
        self.load_data()

    def init_ui(self):
        """Initialize the main UI layout"""
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(10)

        # Header
        header = QLabel(f"Disc Management: {self.album.album_name}")
        header.setStyleSheet("font-size: 16px; font-weight: bold;")
        main_layout.addWidget(header)

        # Statistics bar
        self.stats_bar = self.create_stats_bar()
        main_layout.addWidget(self.stats_bar)

        # Divider
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setStyleSheet("color: #ccc;")
        main_layout.addWidget(line)

        # Action buttons
        action_layout = QHBoxLayout()

        self.add_disc_btn = QPushButton("➕ Add Disc")
        self.add_disc_btn.clicked.connect(self.add_disc)
        action_layout.addWidget(self.add_disc_btn)

        self.edit_disc_btn = QPushButton("✏️ Edit Disc")
        self.edit_disc_btn.clicked.connect(self.edit_disc)
        self.edit_disc_btn.setEnabled(False)
        action_layout.addWidget(self.edit_disc_btn)

        self.remove_disc_btn = QPushButton("🗑️ Remove Disc")
        self.remove_disc_btn.clicked.connect(self.remove_disc)
        self.remove_disc_btn.setEnabled(False)
        action_layout.addWidget(self.remove_disc_btn)

        action_layout.addStretch()

        self.refresh_btn = QPushButton("🔄 Refresh")
        self.refresh_btn.clicked.connect(self.refresh_view)
        action_layout.addWidget(self.refresh_btn)

        main_layout.addLayout(action_layout)

        # Create scroll area for track display
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QScrollArea.NoFrame)

        # Container for track display
        self.track_container = QWidget()
        self.track_layout = QVBoxLayout(self.track_container)
        self.track_layout.setSpacing(5)

        scroll_area.setWidget(self.track_container)
        main_layout.addWidget(scroll_area, 1)  # Give it stretch factor

        # Status bar
        self.status_label = QLabel("Ready")
        self.status_label.setStyleSheet("color: #666; font-size: 11px;")
        main_layout.addWidget(self.status_label)

    def create_stats_bar(self):
        """Create a statistics display bar"""
        stats_widget = QWidget()
        stats_layout = QHBoxLayout(stats_widget)
        stats_layout.setContentsMargins(10, 5, 10, 5)
        stats_layout.setSpacing(15)

        self.track_count_label = QLabel("Tracks: 0")
        self.disc_count_label = QLabel("Discs: 0")
        self.unassigned_label = QLabel("Unassigned: 0")

        for label in [
            self.track_count_label,
            self.disc_count_label,
            self.unassigned_label,
        ]:
            label.setStyleSheet("color: #444; font-size: 12px;")
            stats_layout.addWidget(label)

        stats_layout.addStretch()
        return stats_widget

    def load_data(self):
        """Load tracks and discs from the database"""
        try:
            # Load physical tracks for this album
            self.physical_tracks = (
                self.controller.get.get_all_entities(
                    "Track", album_id=self.album.album_id
                )
                or []
            )

            # Load virtual track links for this album
            self.virtual_links = (
                self.controller.get.get_all_entities(
                    "AlbumVirtualTrack", album_id=self.album.album_id
                )
                or []
            )

            # Extract actual tracks from virtual links
            self.virtual_tracks = [
                link.track for link in self.virtual_links if link.track
            ]

            # Combine all tracks for display
            self.all_tracks = self.physical_tracks + self.virtual_tracks

            # Load discs for this album
            self.discs = (
                self.controller.get.get_all_entities(
                    "Disc", album_id=self.album.album_id
                )
                or []
            )

            # Sort discs by disc_number
            self.discs.sort(key=lambda d: d.disc_number or 0)

            # Update statistics
            self.update_stats()

            # Create track display
            self.create_track_display()

        except Exception as e:
            logger.error(f"Error loading disc data: {e}")

    def update_stats(self):
        """Update statistics display"""
        # Count assigned tracks (physical tracks with disc_id)
        assigned_physical = [t for t in self.physical_tracks if t.disc_id is not None]
        unassigned_physical = len(self.physical_tracks) - len(assigned_physical)

        # Virtual tracks don't have disc assignments in the same way
        # They use virtual_disc_number from the link

        self.track_count_label.setText(
            f"Tracks: {len(self.physical_tracks)} physical, {len(self.virtual_tracks)} virtual"
        )
        self.disc_count_label.setText(f"Discs: {len(self.discs)}")
        self.unassigned_label.setText(f"Unassigned: {unassigned_physical}")

    def create_track_display(self):
        """Create and populate the track display widget"""
        # Clear existing display
        if self.track_display:
            self.track_display.setParent(None)
            self.track_display.deleteLater()

        self.track_display = TrackSortingDisplay(
            self.physical_tracks,
            discs=self.discs,
            virtual_links=self.virtual_links,
            controller=self.controller,  # Pass controller so the widget can open edit dialogs
            parent=self,
        )

        # Reload the whole view whenever a track edit dialog is saved and closed
        self.track_display.track_edited.connect(self.refresh_view)

        self.track_layout.addWidget(self.track_display)

        # Add placeholder if no tracks
        if not self.physical_tracks and not self.virtual_tracks:
            placeholder = QLabel("No tracks found for this album.")
            placeholder.setAlignment(Qt.AlignCenter)
            placeholder.setStyleSheet("color: #999; font-style: italic; padding: 20px;")
            self.track_layout.addWidget(placeholder)

    def add_disc(self):
        """Open dialog to add a new disc"""
        dialog = DiscEditDialog(self.album, self.controller, parent=self)
        if dialog.exec_() == QDialog.Accepted:
            try:
                # Create new disc
                disc_data = dialog.get_disc_data()

                # Determine next disc number
                if self.discs:
                    next_number = max(d.disc_number for d in self.discs) + 1
                else:
                    next_number = 1

                # Create disc using controller
                success = self.controller.add.add_entity(
                    "Disc",
                    album_id=self.album.album_id,
                    disc_number=next_number,
                    disc_title=disc_data.get("disc_title"),
                    media_type=disc_data.get("media_type"),
                )

                if success:
                    self.status_label.setText(f"Added disc {next_number}")
                    self.refresh_view()
                else:
                    QMessageBox.warning(self, "Error", "Failed to create disc")

            except Exception as e:
                logger.error(f"Error adding disc: {e}")
                QMessageBox.warning(self, "Error", f"Could not add disc: {str(e)}")

    def edit_disc(self):
        """Edit selected disc"""
        # TODO: Implement disc selection and editing
        # For now, show message
        QMessageBox.information(
            self, "Info", "Select a disc to edit (not yet implemented)"
        )

    def remove_disc(self):
        """Remove selected disc"""
        # TODO: Implement disc selection and removal
        # For now, show message
        QMessageBox.information(
            self, "Info", "Select a disc to remove (not yet implemented)"
        )

    def refresh_view(self):
        """Refresh all data and UI"""
        self.status_label.setText("Refreshing...")

        # Reload data
        self.load_data()

        # Re-enable/disable buttons based on data
        has_discs = len(self.discs) > 0
        self.edit_disc_btn.setEnabled(has_discs)
        self.remove_disc_btn.setEnabled(has_discs)

        self.status_label.setText("Ready")

    def show_message(self, message, is_error=False):
        """Show status message"""
        style = "color: #d00;" if is_error else "color: #090;"
        self.status_label.setStyleSheet(f"font-size: 11px; {style}")
        self.status_label.setText(message)
