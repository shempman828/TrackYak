"""Album editor Tracks tab: disc actions, track statistics, and the disc/track tree."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QDialog, QFrame, QHBoxLayout, QLabel, QMessageBox, QPushButton, QScrollArea, QVBoxLayout, QWidget
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import selectinload

from src.album.disc.disc_edit import DiscEditDialog
from src.album.disc.disc_sorting import TrackSortingDisplay
from src.core.status_widget import show_status_message
from src.db.db_tables.album import AlbumVirtualTrack
from src.foundation.logger_config import logger


class DiscTabView(QWidget):
    """Manage an album's discs and show its disc/track tree."""

    # Tells the embedding editor that its own track-derived tabs (Genres, Track Credits, ...) are stale.
    tracks_changed = Signal()

    def __init__(self, album, controller, parent=None):
        super().__init__(parent)
        self.album = album
        self.controller = controller
        self.physical_tracks = []
        self.virtual_links = []
        self.virtual_tracks = []
        self.discs = []

        self.init_ui()
        self.load_data()

    def init_ui(self):
        """Build the header, stats bar, action buttons, track tree, and status line."""
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(10)

        header = QLabel(f"Disc Management: {self.album.album_name}")
        header.setProperty("title", True)
        main_layout.addWidget(header)

        self.stats_bar = self.create_stats_bar()
        main_layout.addWidget(self.stats_bar)

        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        main_layout.addWidget(line)

        action_layout = QHBoxLayout()

        self.add_disc_btn = QPushButton("➕ Add Disc")  # noqa: RUF001
        self.add_disc_btn.setToolTip("Add a new disc to this album.")
        self.add_disc_btn.clicked.connect(lambda: self.add_disc())
        action_layout.addWidget(self.add_disc_btn)

        self.edit_disc_btn = QPushButton("✏️ Edit Disc")
        self.edit_disc_btn.setToolTip("Change the number or title of the selected disc. Select a disc or one of its tracks first.")
        self.edit_disc_btn.clicked.connect(lambda: self.edit_disc())
        self.edit_disc_btn.setEnabled(False)
        action_layout.addWidget(self.edit_disc_btn)

        self.remove_disc_btn = QPushButton("🗑️ Remove Disc")
        self.remove_disc_btn.setToolTip("Remove the selected disc. Its tracks become unassigned.")
        self.remove_disc_btn.clicked.connect(lambda: self.remove_disc())
        self.remove_disc_btn.setEnabled(False)
        action_layout.addWidget(self.remove_disc_btn)

        action_layout.addStretch()

        self.renumber_btn = QPushButton("🔢 Auto-Number Tracks")
        self.renumber_btn.setToolTip(
            "Assign track numbers (restarting at 1 on each disc/side) and "
            "absolute track numbers (continuous) based on the order shown below. "
            "For a detected single with two bare tracks, assigns A1/B1 instead."
        )
        self.renumber_btn.clicked.connect(self.renumber_tracks)
        action_layout.addWidget(self.renumber_btn)

        self.refresh_btn = QPushButton("🔄 Refresh")
        self.refresh_btn.setToolTip("Reload the discs and tracks from the library.")
        self.refresh_btn.clicked.connect(lambda: self.refresh_view())
        action_layout.addWidget(self.refresh_btn)

        main_layout.addLayout(action_layout)

        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QScrollArea.NoFrame)

        self.track_container = QWidget()
        self.track_layout = QVBoxLayout(self.track_container)
        self.track_layout.setSpacing(5)

        # Built once and refilled by load_data, so open track edit dialogs (its children) survive a refresh.
        self.track_display = TrackSortingDisplay([], controller=self.controller, parent=self)
        self.track_display.track_edited.connect(lambda: self.refresh_view())
        self.track_display.track_deleted.connect(lambda: self.refresh_view())
        self.track_display.layout_changed.connect(self._on_layout_changed)
        self.track_display.itemSelectionChanged.connect(self.update_action_buttons)
        self.track_display.edit_disc_requested.connect(self.edit_disc)
        self.track_display.remove_disc_requested.connect(self.remove_disc)
        self.track_layout.addWidget(self.track_display)

        self._empty_placeholder = QLabel("No tracks found for this album.")
        self._empty_placeholder.setAlignment(Qt.AlignCenter)
        self._empty_placeholder.setProperty("textRole", "placeholder")
        self._empty_placeholder.hide()
        self.track_layout.addWidget(self._empty_placeholder)

        scroll_area.setWidget(self.track_container)
        main_layout.addWidget(scroll_area, 1)

        self.status_label = QLabel("Ready")
        self.status_label.setProperty("textRole", "muted")
        main_layout.addWidget(self.status_label)

    def create_stats_bar(self):
        """Create the track/disc/unassigned count bar."""
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

    def load_data(self) -> bool:
        """Load the album's tracks, virtual links and discs, then refill the tree. Return False on a DB error."""
        album_id = self.album.album_id
        try:
            physical_tracks = self.controller.get.get_all_entities("Track", album_id=album_id) or []
            virtual_links = self.controller.get.get_all_entities("AlbumVirtualTrack", load_options=[selectinload(AlbumVirtualTrack.track)], album_id=album_id) or []
            discs = self.controller.get.get_all_entities("Disc", album_id=album_id) or []
        except SQLAlchemyError as e:
            logger.error(f"Error loading disc data: {e}")
            self.status_label.setText("Could not load the discs and tracks. See the log for details.")
            return False

        self.physical_tracks = physical_tracks
        self.virtual_links = virtual_links
        self.virtual_tracks = [link.track for link in virtual_links if link.track]
        self.discs = sorted(discs, key=lambda d: (d.disc_number or 0, d.disc_id))

        self.update_stats()
        self.create_track_display()
        self.update_action_buttons()
        return True

    def update_action_buttons(self):
        """Enable Edit/Remove Disc when a disc is selected or the album has exactly one disc."""
        has_target = len(self.discs) == 1 or (bool(self.discs) and self.track_display.selected_disc() is not None)
        self.edit_disc_btn.setEnabled(has_target)
        self.remove_disc_btn.setEnabled(has_target)

    def update_stats(self):
        """Update the track/disc/unassigned counts."""
        # Virtual tracks have no disc_id; they are placed by their link's virtual_disc_number.
        unassigned_physical = sum(1 for t in self.physical_tracks if t.disc_id is None)
        self.track_count_label.setText(f"Tracks: {len(self.physical_tracks)} physical, {len(self.virtual_tracks)} virtual")
        self.disc_count_label.setText(f"Discs: {len(self.discs)}")
        self.unassigned_label.setText(f"Unassigned: {unassigned_physical}")

    def create_track_display(self):
        """Refill the track tree with the loaded data and show the empty-state label when there are no tracks."""
        self.track_display.set_data(self.physical_tracks, discs=self.discs, virtual_links=self.virtual_links, release_type=self.album.release_type)
        self._empty_placeholder.setVisible(not self.physical_tracks and not self.virtual_tracks)

    def _on_layout_changed(self):
        """After a saved drag-and-drop, update the counts and tell the editor."""
        self.update_stats()
        self.status_label.setText("Saved the new track order")
        self.tracks_changed.emit()

    def _next_disc_number(self):
        """Return one more than the highest disc number on the album."""
        return max((d.disc_number or 0 for d in self.discs), default=0) + 1

    def add_disc(self):
        """Open the dialog and add a new disc."""
        next_number = self._next_disc_number()
        dialog = DiscEditDialog(self.album, parent=self, next_number=next_number, taken_numbers=[d.disc_number for d in self.discs])
        if dialog.exec() != QDialog.Accepted:
            return

        disc_data = dialog.get_disc_data()
        number = disc_data.get("disc_number") or next_number
        try:
            success = self.controller.add.add_entity("Disc", album_id=self.album.album_id, disc_number=number, disc_title=disc_data.get("disc_title"))
        except SQLAlchemyError as e:
            logger.error(f"Error adding disc: {e}")
            QMessageBox.warning(self, "Error", f"Could not add disc: {e!s}")
            return

        if success:
            self.refresh_view(f"Added disc {number}", tracks_changed=False)
        else:
            QMessageBox.warning(self, "Error", "Failed to create disc")

    @staticmethod
    def _disc_label(disc):
        """Human-readable "Disc N[: title]" label for a disc row."""
        label = f"Disc {disc.disc_number}"
        if disc.disc_title:
            label += f": {disc.disc_title}"
        return label

    def _target_disc(self, action):
        """Return the selected disc (or the sole disc); else ask the user to select one and return None."""
        disc = self.track_display.selected_disc()
        if disc is not None:
            return disc

        if len(self.discs) == 1:
            return self.discs[0]

        show_status_message(self, f"Select a disc in the list to {action}.")
        return None

    def edit_disc(self, disc=None):
        """Edit the number and title of disc, or of the disc selected in the track list."""
        if not self.discs:
            show_status_message(self, "No discs to edit.")
            return

        disc = disc or self._target_disc("edit")
        if disc is None:
            return

        dialog = DiscEditDialog(self.album, parent=self, disc=disc, taken_numbers=[d.disc_number for d in self.discs])
        if dialog.exec() != QDialog.Accepted:
            return

        disc_data = dialog.get_disc_data()
        changes = {"disc_title": disc_data.get("disc_title")}
        new_number = disc_data.get("disc_number")
        if new_number is not None and new_number != disc.disc_number:
            changes["disc_number"] = new_number

        try:
            success = self.controller.update.update_entity("Disc", disc.disc_id, **changes)
        except SQLAlchemyError as e:
            logger.error(f"Error updating disc: {e}")
            QMessageBox.warning(self, "Error", f"Could not update disc: {e!s}")
            return

        if success:
            # A new number moves the disc's tracks; a title change does not touch tracks.
            self.refresh_view(f"Updated {self._disc_label(disc)}", tracks_changed="disc_number" in changes)
        else:
            QMessageBox.warning(self, "Error", "Failed to update disc.")

    def _confirm_remove(self, label, track_count):
        """Ask the user to confirm removal of a disc; True when confirmed."""
        text = f"{label} has {track_count} track(s) assigned to it. Removing it will unassign those tracks. Continue?" if track_count else f"Remove {label}?"
        return QMessageBox.question(self, "Remove Disc", text, QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel) == QMessageBox.Yes

    def remove_disc(self, disc=None):
        """Remove disc, or the disc selected in the track list, and close the gap in the disc numbers."""
        if not self.discs:
            show_status_message(self, "No discs to remove.")
            return

        disc = disc or self._target_disc("remove")
        if disc is None:
            return
        label = self._disc_label(disc)
        removed_number = disc.disc_number or 0

        assigned_count = sum(1 for t in self.physical_tracks if t.disc_id == disc.disc_id)
        if not self._confirm_remove(label, assigned_count):
            return

        # Later discs move down by one so the numbers stay continuous; virtual links keep their numbers.
        # No shift when a duplicate disc still holds the removed number -- it would collide.
        others = [d for d in self.discs if d.disc_id != disc.disc_id]
        number_still_used = any((d.disc_number or 0) == removed_number for d in others)
        shifts = [] if number_still_used else [{"disc_id": d.disc_id, "disc_number": d.disc_number - 1} for d in others if (d.disc_number or 0) > removed_number]

        try:
            success = self.controller.delete.delete_entity("Disc", disc.disc_id)
            if not success:
                QMessageBox.warning(self, "Error", f"Failed to remove {label}.")
                return
            # The DB's ON DELETE SET NULL runs outside SQLAlchemy, so loaded Tracks keep the old disc_id until expired.
            self.controller.get.session.expire_all()
            if shifts:
                _, failed = self.controller.update.update_entities_bulk_with_fallback("Disc", shifts)
                if failed:
                    logger.error(f"Could not renumber {len(failed)} disc(s) after removing {label}")
                    QMessageBox.warning(self, "Some Discs Not Renumbered", f"{label} was removed, but {len(failed)} later disc(s) kept their old numbers.")
        except SQLAlchemyError as e:
            logger.error(f"Error removing disc: {e}")
            QMessageBox.warning(self, "Error", f"Could not remove disc: {e!s}")
            return

        self.refresh_view(f"Removed {label}")

    def renumber_tracks(self):
        """Number the tracks in the order shown in the tree."""
        self.track_display.assign_absolute_track_numbers()

    def refresh_view(self, message=None, tracks_changed=True):
        """Reload all data, show message (or "Ready"), and emit tracks_changed when requested."""
        if not self.load_data():
            return
        self.status_label.setText(message or "Ready")
        if tracks_changed:
            self.tracks_changed.emit()
