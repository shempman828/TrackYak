from PySide6.QtCore import Qt, Signal
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
from sqlalchemy.exc import SQLAlchemyError

from src.album.disc_tab.disc_edit import DiscEditDialog
from src.album.disc_tab.disc_sorting import TrackSortingDisplay
from src.common.style_utils import set_style_property
from src.foundation.logger_config import logger
from src.foundation.status_utility import show_status_message


class DiscTabView(QWidget):
    """
    Main widget for managing disc structure of an album.
    Displays tracks in their natural hierarchy and allows disc creation/editing.
    """

    # Emitted after any change that may add, remove, or reassign a track --
    # lets an embedding parent (e.g. the album editor) know its own,
    # separately-snapshotted track lists (Genres, Track Credits, ...) are
    # now stale and need rebuilding, since this view only refreshes itself.
    tracks_changed = Signal()

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
        header.setProperty("title", True)
        main_layout.addWidget(header)

        # Statistics bar
        self.stats_bar = self.create_stats_bar()
        main_layout.addWidget(self.stats_bar)

        # Divider
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        main_layout.addWidget(line)

        # Action buttons
        action_layout = QHBoxLayout()

        self.add_disc_btn = QPushButton("➕ Add Disc")  # noqa: RUF001
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

        self.renumber_btn = QPushButton("🔢 Auto-Number Tracks")
        self.renumber_btn.setToolTip(
            "Assign track numbers (restarting at 1 on each disc/side) and "
            "absolute track numbers (continuous) based on the order shown below"
        )
        self.renumber_btn.clicked.connect(self.renumber_tracks)
        action_layout.addWidget(self.renumber_btn)

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
        self.status_label.setProperty("textRole", "muted")
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

        for label in [self.track_count_label, self.disc_count_label, self.unassigned_label]:
            label.setProperty("textRole", "muted")
            stats_layout.addWidget(label)

        stats_layout.addStretch()
        return stats_widget

    def load_data(self):
        """Load tracks and discs from the database"""
        try:
            # Load physical tracks for this album
            self.physical_tracks = (
                self.controller.get.get_all_entities("Track", album_id=self.album.album_id) or []
            )

            # Load virtual track links for this album
            self.virtual_links = (
                self.controller.get.get_all_entities(
                    "AlbumVirtualTrack", album_id=self.album.album_id
                )
                or []
            )

            # Extract actual tracks from virtual links
            self.virtual_tracks = [link.track for link in self.virtual_links if link.track]

            # Combine all tracks for display
            self.all_tracks = self.physical_tracks + self.virtual_tracks

            # Load discs for this album
            self.discs = (
                self.controller.get.get_all_entities("Disc", album_id=self.album.album_id) or []
            )

            # Sort discs by disc_number
            self.discs.sort(key=lambda d: d.disc_number or 0)

            # Update statistics
            self.update_stats()

            # Create track display
            self.create_track_display()

            # Keep the Edit/Remove disc buttons in sync with what's loaded
            self.update_action_buttons()

        except (SQLAlchemyError, AttributeError) as e:
            logger.error(f"Error loading disc data: {e}")

    def update_action_buttons(self):
        """Enable the Edit/Remove disc buttons only when the album has discs."""
        has_discs = len(self.discs) > 0
        self.edit_disc_btn.setEnabled(has_discs)
        self.remove_disc_btn.setEnabled(has_discs)

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
        self.track_display.track_deleted.connect(self.refresh_view)

        self.track_layout.addWidget(self.track_display)

        # Add placeholder if no tracks
        if not self.physical_tracks and not self.virtual_tracks:
            placeholder = QLabel("No tracks found for this album.")
            placeholder.setAlignment(Qt.AlignCenter)
            placeholder.setProperty("textRole", "placeholder")
            self.track_layout.addWidget(placeholder)

    def add_disc(self):
        """Open dialog to add a new disc"""
        dialog = DiscEditDialog(self.album, self.controller, parent=self)
        if dialog.exec_() == QDialog.Accepted:
            try:
                # Create new disc
                disc_data = dialog.get_disc_data()

                # Determine next disc number
                next_number = max(d.disc_number for d in self.discs) + 1 if self.discs else 1

                # Create disc using controller
                success = self.controller.add.add_entity(
                    "Disc",
                    album_id=self.album.album_id,
                    disc_number=next_number,
                    disc_title=disc_data.get("disc_title"),
                )

                if success:
                    self.status_label.setText(f"Added disc {next_number}")
                    self.refresh_view()
                else:
                    QMessageBox.warning(self, "Error", "Failed to create disc")

            except SQLAlchemyError as e:
                logger.error(f"Error adding disc: {e}")
                QMessageBox.warning(self, "Error", f"Could not add disc: {e!s}")

    @staticmethod
    def _disc_label(disc):
        """Human-readable "Disc N[: title]" label for a disc row."""
        label = f"Disc {disc.disc_number}"
        if disc.disc_title:
            label += f": {disc.disc_title}"
        return label

    def _target_disc(self, action):
        """The disc to act on: the one selected in the track list, or the sole
        disc if the album only has one. Returns None (after nudging the user)
        when the album has several discs and none is selected."""
        if self.track_display is not None:
            disc = self.track_display.selected_disc()
            if disc is not None:
                return disc

        if len(self.discs) == 1:
            return self.discs[0]

        show_status_message(self, f"Select a disc in the list to {action}.")
        return None

    def edit_disc(self):
        """Edit the disc currently selected in the track list."""
        if not self.discs:
            show_status_message(self, "No discs to edit.")
            return

        disc = self._target_disc("edit")
        if disc is None:
            return

        dialog = DiscEditDialog(self.album, self.controller, parent=self, disc=disc)
        if dialog.exec_() != QDialog.Accepted:
            return

        disc_data = dialog.get_disc_data()
        try:
            success = self.controller.update.update_entity(
                "Disc", disc.disc_id, disc_title=disc_data.get("disc_title")
            )
            if success:
                self.status_label.setText(f"Updated {self._disc_label(disc)}")
                self.refresh_view()
            else:
                QMessageBox.warning(self, "Error", "Failed to update disc.")
        except SQLAlchemyError as e:
            logger.error(f"Error updating disc: {e}")
            QMessageBox.warning(self, "Error", f"Could not update disc: {e!s}")

    def remove_disc(self):
        """Remove the disc currently selected in the track list."""
        if not self.discs:
            show_status_message(self, "No discs to remove.")
            return

        disc = self._target_disc("remove")
        if disc is None:
            return
        choice = self._disc_label(disc)

        # Warn if the disc has tracks assigned to it
        assigned_tracks = [t for t in self.physical_tracks if t.disc_id == disc.disc_id]
        if assigned_tracks:
            confirm = QMessageBox.warning(
                self,
                "Disc Has Tracks",
                f"{choice} has {len(assigned_tracks)} track(s) assigned to it. "
                "Removing it will unassign those tracks. Continue?",
                QMessageBox.Yes | QMessageBox.Cancel,
            )
            if confirm != QMessageBox.Yes:
                return

        try:
            success = self.controller.delete.delete_entity("Disc", disc.disc_id)
            if success:
                self.status_label.setText(f"Removed {choice}")
                self.refresh_view()
            else:
                QMessageBox.warning(self, "Error", f"Failed to remove {choice}.")
        except SQLAlchemyError as e:
            logger.error(f"Error removing disc: {e}")
            QMessageBox.warning(self, "Error", f"Could not remove disc: {e!s}")

    def renumber_tracks(self):
        """Assign absolute track numbers based on the currently visible order."""
        if not self.track_display:
            return
        self.track_display.assign_absolute_track_numbers()

    def refresh_view(self):
        """Refresh all data and UI"""
        self.status_label.setText("Refreshing...")

        # Reload data
        self.load_data()

        # Re-enable/disable buttons based on data (also done inside load_data)
        self.update_action_buttons()

        self.status_label.setText("Ready")
        self.tracks_changed.emit()

    def show_message(self, message, is_error=False):
        """Show status message"""
        set_style_property(self.status_label, "statusState", "error" if is_error else "ok")
        self.status_label.setText(message)
