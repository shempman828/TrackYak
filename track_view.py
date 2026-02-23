from pathlib import Path

from PySide6.QtCore import (
    QByteArray,
    QMimeData,
    QRegularExpression,
    QSortFilterProxyModel,
    Qt,
)
from PySide6.QtGui import QDrag, QPainter, QPixmap, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from config_setup import app_config
from db_mapping_tracks import TRACK_FIELDS
from logger_config import logger
from track_columns import ColumnCustomizationDialog
from track_edit import TrackEditDialog
from track_editing_multiple import MultiTrackEditDialog


class TrackView(QWidget):
    """Track view with table, search functionality, and column customization."""

    def __init__(self, controller, music_player):
        super().__init__()
        self.controller = controller
        self.player = music_player
        self.track_fields = TRACK_FIELDS
        self.layout = QVBoxLayout(self)

        # Search bar
        self.search_bar = QLineEdit(self)
        self.search_bar.setPlaceholderText("Search tracks...")
        self.search_bar.textChanged.connect(self.filter_tracks)

        # Column toggle button
        self.column_toggle_button = QPushButton("Toggle Columns", self)
        self.column_toggle_button.clicked.connect(self.show_column_menu)

        # Column order button
        self.customize_columns_button = QPushButton("Column Order", self)
        self.customize_columns_button.clicked.connect(self.show_column_customization)

        # Refresh Table Button
        self.refresh_button = QPushButton("Refresh", self)
        self.refresh_button.clicked.connect(self.load_tracks_on_startup)

        self.table = QTableView(self)
        self._initialize_columns()

        # Enable context menu
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.show_context_menu)

        self.model = QStandardItemModel()
        self.proxy_model = QSortFilterProxyModel(self)
        self.proxy_model.setSourceModel(self.model)
        self.proxy_model.setFilterCaseSensitivity(Qt.CaseInsensitive)
        self.proxy_model.setFilterRegularExpression(QRegularExpression())
        self.proxy_model.setFilterKeyColumn(-1)  # Filter across all columns
        self.proxy_model.setSortRole(Qt.UserRole)
        self.table.setModel(self.proxy_model)

        self._setup_table()
        self.load_column_state()

        # Layout adjustments
        search_layout = QHBoxLayout()
        search_layout.addWidget(self.search_bar)
        search_layout.addWidget(self.column_toggle_button)
        search_layout.addWidget(self.customize_columns_button)
        search_layout.addWidget(self.refresh_button)

        self.layout.addLayout(search_layout)
        self.layout.addWidget(self.table)

        # Connect double-click event
        self.table.doubleClicked.connect(self.on_double_clicked)

        # Load tracks on startup
        self.load_tracks_on_startup()

    def _initialize_columns(self):
        """Initialize columns dictionary using TrackField configuration."""
        self.columns = {}
        for field_name, field_config in self.track_fields.items():
            if field_config.friendly:
                self.columns[field_name] = field_config.friendly

    def load_tracks_on_startup(self):
        """Load tracks from database when the view is initialized."""
        try:
            tracks = self.controller.get.get_all_entities("Track")
            self.load_data(tracks or [])  # always refresh the view, even if empty
            if tracks:
                logger.info("Successfully loaded tracks on startup.")
            else:
                logger.warning("No tracks found in the database — empty list shown.")
        except Exception as e:
            logger.error(f"Error loading tracks on startup: {e}")
            self.load_data([])  # clear stale data on error

    def _setup_table(self):
        """Set up the table with columns for all metadata fields using TrackField configuration."""
        # Initialize columns dictionary from TrackField configuration
        self.columns = {}
        for field_name, field_config in self.track_fields.items():
            if field_config.friendly:
                self.columns[field_name] = field_config.friendly

        # Set up the model with columns
        self.model.setColumnCount(len(self.columns))
        self.model.setHorizontalHeaderLabels(self.columns.values())

        # Enable sorting
        self.table.setSortingEnabled(True)

        # Configure header behavior
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)

        # Set selection behavior
        self.table.setSelectionBehavior(QTableView.SelectRows)
        self.table.setSelectionMode(QTableView.SingleSelection)
        self.table.setAlternatingRowColors(True)

        # Hide vertical headers
        self.table.verticalHeader().setVisible(False)

        # Make table read-only
        self.table.setEditTriggers(QTableView.NoEditTriggers)

        # Enable drag operations
        self.table.setDragEnabled(True)
        self.table.setDragDropMode(QTableView.DragOnly)
        self.table.setDefaultDropAction(Qt.CopyAction)
        self.table.setSelectionMode(QTableView.ExtendedSelection)

        # Set custom drag handler
        self.table.startDrag = self.startDrag

        # Set initial column visibility based on TrackField configuration
        self._set_initial_column_visibility()

    def _set_initial_column_visibility(self):
        """Set initial column visibility based on TrackField configuration."""
        for i, (field_name, _) in enumerate(self.columns.items()):
            field_config = self.track_fields.get(field_name)
            if field_config:
                # You can add logic here to hide certain columns by default
                # For example, hide technical fields by default:
                if field_config.category == "Technical":
                    self.table.setColumnHidden(i, True)
                # Or hide fields that are not commonly needed:
                elif field_name in [
                    "file_size",
                    "bit_rate",
                    "sample_rate",
                    "track_id",
                    "track_file_path",
                ]:
                    self.table.setColumnHidden(i, True)

    def show_context_menu(self, pos):
        """Show context menu for track operations."""
        selected = self.table.selectionModel().selectedRows()
        if not selected:
            return

        # Create the context menu
        menu = QMenu(self)

        # Add Edit Track option (single or multiple)
        if len(selected) == 1:
            edit_action = menu.addAction("Edit Track")
            edit_action.triggered.connect(self.edit_selected_track)
        elif len(selected) > 1:
            edit_action = menu.addAction(f"Edit {len(selected)} Tracks")
            edit_action.triggered.connect(self.edit_selected_track)

        menu.addSeparator()

        # Add to playlist submenu
        add_to_playlist_menu = menu.addMenu("Add to Playlist")

        # Add to mood submenu (NEW)
        add_to_mood_menu = menu.addMenu("Add to Mood")

        # Get all selected track IDs once for reuse
        track_ids = []
        for index in selected:
            source_index = self.proxy_model.mapToSource(index)
            track_id_item = self.model.item(
                source_index.row(),
                list(self.columns.keys()).index("track_id"),
            )
            if track_id_item:
                track_ids.append(track_id_item.text())

        # Load playlists for playlist submenu
        try:
            playlists = self.controller.get.get_all_entities("Playlist")
            if not playlists:
                add_to_playlist_menu.addAction("No playlists available").setEnabled(
                    False
                )
            else:
                # Add each playlist as an action
                for playlist in playlists:
                    action = add_to_playlist_menu.addAction(playlist.playlist_name)
                    # Store playlist ID and all selected track IDs
                    action.setData((playlist.playlist_id, track_ids))
                    action.triggered.connect(self.add_to_playlist)
        except Exception as e:
            logger.error(f"Error loading playlists for context menu: {str(e)}")
            add_to_playlist_menu.addAction("Error loading playlists").setEnabled(False)

        # Load moods for mood submenu (NEW)
        try:
            moods = self.controller.get.get_all_entities("Mood")
            if not moods:
                add_to_mood_menu.addAction("No moods available").setEnabled(False)
            else:
                # Add each mood as an action
                for mood in moods:
                    action = add_to_mood_menu.addAction(mood.mood_name)
                    # Store mood ID and all selected track IDs
                    action.setData((mood.mood_id, track_ids))
                    action.triggered.connect(self.add_to_mood)
        except Exception as e:
            logger.error(f"Error loading moods for context menu: {str(e)}")
            add_to_mood_menu.addAction("Error loading moods").setEnabled(False)

        # Show the menu at the cursor position
        menu.exec_(self.table.viewport().mapToGlobal(pos))

    def add_to_mood(self):
        """Handle adding multiple tracks to a mood from the context menu."""
        action = self.sender()
        if not action:
            return

        mood_id, track_ids = action.data()
        success_count = 0
        error_messages = []

        try:
            for track_id in track_ids:
                # Check if the track is already associated with this mood
                existing_associations = self.controller.get.get_entity_links(
                    "MoodTrackAssociation", mood_id=mood_id, track_id=track_id
                )

                if not existing_associations:
                    # Add the track to mood
                    if self.controller.add.add_entity_link(
                        "MoodTrackAssociation",
                        mood_id=mood_id,
                        track_id=track_id,
                    ):
                        success_count += 1
                    else:
                        error_messages.append(f"Failed to add track {track_id} to mood")
                else:
                    # Track already in mood
                    error_messages.append(f"Track {track_id} is already in this mood")

            # Show results based on success
            if success_count == len(track_ids):
                QMessageBox.information(
                    self,
                    "Success",
                    f"All {success_count} tracks added to mood successfully!",
                )
            elif success_count > 0:
                QMessageBox.warning(
                    self,
                    "Partial Success",
                    f"{success_count} of {len(track_ids)} tracks added.\n"
                    f"Some tracks might already be in this mood:\n"
                    f"\n".join(error_messages[-3:]),  # Show last 3 errors
                )
            else:
                QMessageBox.warning(
                    self,
                    "No tracks added",
                    "No tracks were added. All selected tracks are already in this mood.",
                )

        except Exception as e:
            logger.error(f"Error adding tracks to mood: {str(e)}")
            QMessageBox.critical(
                self, "Error", f"Failed to add tracks to mood:\n{str(e)}"
            )

    def add_to_playlist(self):
        """Handle adding multiple tracks to a playlist from the context menu."""
        action = self.sender()
        if not action:
            return

        playlist_id, track_ids = action.data()
        success_count = 0

        try:
            # Get the current maximum position in the playlist
            existing_tracks = self.controller.get.get_entity_links(
                "PlaylistTracks", playlist_id=playlist_id
            )
            next_position = max([t.position for t in existing_tracks], default=0) + 1

            for track_id in track_ids:
                if self.controller.add.add_entity_link(
                    "PlaylistTracks",
                    playlist_id=playlist_id,
                    track_id=track_id,
                    position=next_position,  # Add the position parameter
                ):
                    success_count += 1
                    next_position += 1  # Increment for next track

            if success_count == len(track_ids):
                QMessageBox.information(
                    self,
                    "Success",
                    f"All {success_count} tracks added to playlist successfully!",
                )
            elif success_count > 0:
                QMessageBox.warning(
                    self,
                    "Partial Success",
                    f"{success_count} of {len(track_ids)} tracks added (some might already be in the playlist).",
                )
            else:
                QMessageBox.warning(
                    self,
                    "Warning",
                    "No tracks were added (they might already be in the playlist).",
                )
        except Exception as e:
            logger.error(f"Error adding tracks to playlist: {str(e)}")
            QMessageBox.critical(
                self, "Error", f"Failed to add tracks to playlist:\n{str(e)}"
            )

    def load_data(self, tracks):
        """Populate the table with track metadata using TrackField configuration."""
        self.model.setRowCount(0)  # Clear existing rows

        for track in tracks:
            row = []
            for db_field, display_name in self.columns.items():
                field_config = self.track_fields.get(db_field)
                value = self._get_formatted_field_value(track, db_field, field_config)
                item = QStandardItem(str(value))

                # Set sorting data based on field type
                if field_config and field_config.type in (int, float):
                    # Store raw numeric value for proper sorting
                    raw_value = getattr(track, db_field, None)
                    if raw_value is not None:
                        item.setData(raw_value, Qt.UserRole)

                row.append(item)
            self.model.appendRow(row)

        logger.info(f"Loaded {len(tracks)} tracks into the table.")

    def _get_formatted_field_value(self, track, db_field, field_config):
        """Get formatted value for a track field using TrackField configuration."""
        # Handle special relationship fields first
        if db_field == "album_name":
            return track.album.album_name if track.album else "Unknown Album"
        elif db_field == "primary_artist_name":
            return self._get_artist_name(track)

        # Get the raw value from the track object
        raw_value = getattr(track, db_field, "")

        # Apply formatting based on field type and configuration
        return self._format_value(raw_value, db_field, field_config)

    def filter_tracks(self, text):
        """Filter tracks based on search text."""
        regex = QRegularExpression(text, QRegularExpression.CaseInsensitiveOption)
        self.proxy_model.setFilterRegularExpression(regex)
        logger.debug(f"Filter applied: {text}")

    def _get_artist_name(self, track):
        """Extract artist name from track."""
        if hasattr(track, "primary_artist_names"):
            return track.primary_artist_names
        elif track.artist_roles:
            return (
                track.artist_roles[0].artist.artist_name
                if track.artist_roles[0].artist
                else "Unknown Artist"
            )
        else:
            return "Unknown Artist"

    def _format_value(self, value, db_field, field_config):
        """Format field value based on TrackField configuration and field name."""
        if value is None:
            return ""

        # Field-specific formatting
        if db_field == "duration" and isinstance(value, int):
            try:
                minutes = value // 60
                seconds = value % 60
                return f"{minutes}:{seconds:02d}"
            except (TypeError, ValueError):
                return "0:00"
        elif db_field == "sample_rate" and isinstance(value, int):
            return f"{value / 1000:,.1f} kHz"
        elif db_field == "file_size" and isinstance(value, int):
            return f"{value / (1024 * 1024):.2f} MB"

        # Default formatting based on type
        if field_config and field_config.type in (int, float):
            return str(value)

        return str(value)

    def on_double_clicked(self, index):
        """Handle double-click events on table rows."""
        source_index = self.proxy_model.mapToSource(index)
        row = source_index.row()
        logger.debug(f"Double-clicked: row={row}, isValid={source_index.isValid()}")

        try:
            # Get the column index for file path
            file_path_index = list(self.columns.keys()).index("track_file_path")
            file_path = self.model.item(row, file_path_index).text()

            logger.info(f"Attempting to play track: {file_path}")

            # Convert to Path object before passing to load_track
            track_path = Path(file_path)

            if file_path and self.controller.mediaplayer.load_track(track_path):
                self.player.play()
            else:
                logger.warning(f"Failed to load track: {file_path}")
        except Exception as e:
            logger.error(f"Error playing track: {str(e)}")

    def edit_selected_track(self):
        """Open edit dialog for selected track(s) using TrackField configuration."""
        selected = self.table.selectionModel().selectedRows()
        if not selected:
            return

        try:
            tracks = self._get_selected_tracks(selected)
            if not tracks:
                return

            logger.debug(
                f"Editing {len(tracks)} tracks: {[t.track_id for t in tracks]}"
            )

            # Filter fields based on multi-edit capability
            if len(tracks) > 1:
                editable_fields = {
                    name: config
                    for name, config in self.track_fields.items()
                    if config.editable and config.multiple
                }
            else:
                editable_fields = {
                    name: config
                    for name, config in self.track_fields.items()
                    if config.editable
                }

            logger.debug(f"Editable fields: {list(editable_fields.keys())}")

            if len(tracks) == 1:
                # Add validation for the track object
                if not tracks[0] or not hasattr(tracks[0], "track_id"):
                    logger.error("Invalid track object selected")
                    QMessageBox.warning(self, "Error", "Invalid track selected")
                    return

                dialog = TrackEditDialog(tracks[0], self.controller, self)
            else:
                dialog = MultiTrackEditDialog(tracks, self.controller, self)

            if dialog.exec_() == QDialog.Accepted:
                self.load_tracks_on_startup()
                logger.info(f"Successfully updated {len(tracks)} tracks")

        except Exception as e:
            logger.error(
                f"Error editing track(s): {str(e)}", exc_info=True
            )  # Add exc_info for full traceback
            QMessageBox.critical(self, "Error", f"Failed to edit tracks:\n{str(e)}")

    def _update_artist_roles(self, track_id, artist_roles):
        """Update artist roles for a track."""
        # First remove existing roles
        self.controller.delete.delete_entity("TrackArtistRole", track_id=track_id)

        # Add new roles
        for role_data in artist_roles:
            self.controller.add.add_entity(
                "TrackArtistRole",
                track_id=track_id,
                artist_id=role_data["artist_id"],
                role_id=role_data["role_id"],
            )

    def _update_genres(self, track_id, genre_ids):
        """Update genres for a track."""
        # First remove existing genres
        self.controller.delete.delete_entity("TrackGenre", track_id=track_id)

        # Add new genres
        for genre_id in genre_ids:
            self.controller.add.add_entity_link(
                "TrackGenre", track_id=track_id, genre_id=genre_id
            )

    def _update_place_associations(self, track_id, place_associations):
        """Update place associations for a track."""
        # First remove existing associations
        self.controller.delete.delete_entity(
            "PlaceAssociation", entity_id=track_id, entity_type="Track"
        )

        # Add new associations
        for assoc_data in place_associations:
            self.controller.add.add_entity_link(
                "PlaceAssociation",
                entity_id=track_id,
                entity_type="Track",
                place_id=assoc_data["place_id"],
                association_type=assoc_data["association_type"],
            )

    def keyPressEvent(self, event):
        """Handle key press events for delete functionality."""
        if event.key() == Qt.Key_Delete:
            self.delete_selected_tracks()
        else:
            super().keyPressEvent(event)

    def delete_selected_tracks(self):
        """Delete selected tracks with option to delete from database or disk."""
        selected = self.table.selectionModel().selectedRows()
        if not selected:
            return

        # Get selected track IDs and file paths
        track_data = []
        for index in selected:
            source_index = self.proxy_model.mapToSource(index)
            track_id_item = self.model.item(
                source_index.row(), list(self.columns.keys()).index("track_id")
            )
            file_path_item = self.model.item(
                source_index.row(), list(self.columns.keys()).index("track_file_path")
            )

            if track_id_item and file_path_item:
                track_data.append(
                    {
                        "track_id": track_id_item.text(),
                        "file_path": file_path_item.text(),
                    }
                )

        if not track_data:
            return

        # Create delete dialog
        dialog = QDialog(self)
        dialog.setWindowTitle("Delete Tracks")
        dialog.setMinimumWidth(400)
        layout = QVBoxLayout(dialog)

        # Warning label
        warning_label = QLabel(f"You are about to delete {len(track_data)} track(s).")
        layout.addWidget(warning_label)

        # Options
        options_layout = QVBoxLayout()
        options_group = QWidget()
        options_group.setLayout(options_layout)
        layout.addWidget(options_group)

        # Radio buttons for delete options
        self.db_radio = QRadioButton("Delete from database only (keep files)")
        self.file_radio = QRadioButton("Delete from disk and database")
        self.db_radio.setChecked(True)  # Default option

        options_layout.addWidget(self.db_radio)
        options_layout.addWidget(self.file_radio)

        # Additional warning for file deletion
        file_warning = QLabel("Warning: File deletion cannot be undone!")
        file_warning.setVisible(False)
        layout.addWidget(file_warning)

        # Connect radio buttons to show/hide file warning
        def toggle_file_warning():
            file_warning.setVisible(self.file_radio.isChecked())

        self.db_radio.toggled.connect(toggle_file_warning)
        self.file_radio.toggled.connect(toggle_file_warning)

        # Dialog buttons
        button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        button_box.accepted.connect(dialog.accept)
        button_box.rejected.connect(dialog.reject)
        layout.addWidget(button_box)

        # Show dialog and process result
        if dialog.exec_() == QDialog.Accepted:
            delete_from_disk = self.file_radio.isChecked()

            if delete_from_disk:
                # Show confirmation dialog for file deletion
                confirm_msg = QMessageBox(self)
                confirm_msg.setWindowTitle("Confirm File Deletion")
                confirm_msg.setIcon(QMessageBox.Warning)
                confirm_msg.setText(
                    f"You are about to permanently delete {len(track_data)} file(s) from disk.\nThis action cannot be undone!"
                )
                confirm_msg.setInformativeText("Are you sure you want to continue?")
                confirm_msg.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
                confirm_msg.setDefaultButton(QMessageBox.No)

                if confirm_msg.exec_() != QMessageBox.Yes:
                    return  # User canceled file deletion

            # Perform deletion
            self._perform_deletion(track_data, delete_from_disk)

    def startDrag(self, supported_actions):
        """Handle drag operation start with multiple tracks."""
        selected = self.table.selectionModel().selectedRows()
        if not selected:
            return

        # Get all selected track IDs
        track_ids = []
        for index in selected:
            source_index = self.proxy_model.mapToSource(index)
            track_id_item = self.model.item(
                source_index.row(), list(self.columns.keys()).index("track_id")
            )
            if track_id_item:
                track_ids.append(track_id_item.text())

        if not track_ids:
            return

        # Create MIME data with comma-separated track IDs
        mime_data = QMimeData()
        track_ids_str = ",".join(track_ids)
        mime_data.setData(
            "application/x-track-id", QByteArray(track_ids_str.encode("utf-8"))
        )

        # Also set text/plain for compatibility
        mime_data.setText(f"Add {len(track_ids)} tracks to playlist")

        # Create drag object
        drag = QDrag(self.table)
        drag.setMimeData(mime_data)

        # Visual feedback
        pixmap = QPixmap(150, 30)
        pixmap.fill(Qt.darkGray)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.white)
        painter.drawText(10, 20, f"Add {len(track_ids)} tracks")
        painter.end()
        drag.setPixmap(pixmap)
        drag.setHotSpot(pixmap.rect().center())

        # Start drag
        result = drag.exec_(Qt.CopyAction)
        logger.debug(f"Drag operation completed with result: {result}")

    def _perform_deletion(self, track_data, delete_from_disk):
        """Perform the actual deletion of tracks using the robust deletion methods."""
        success_count = 0
        error_messages = []

        for track in track_data:
            try:
                track_id = int(track["track_id"])

                if delete_from_disk:
                    # Use the comprehensive delete method that handles both file and database
                    success = self.controller.delete.delete_file(
                        file_path=track["file_path"],
                        entity_type="Track",
                        entity_id=track_id,  # Fixed: use track_id from the current track
                    )
                else:
                    # Delete from database only
                    success = self.controller.delete.delete_entity(
                        "Track", entity_id=track_id
                    )

                if success:
                    success_count += 1
                else:
                    error_messages.append(f"Failed to delete track {track_id}")

            except Exception as e:
                error_messages.append(
                    f"Error deleting track {track['track_id']}: {str(e)}"
                )

        # Show results
        if success_count == len(track_data):
            if delete_from_disk:
                QMessageBox.information(
                    self,
                    "Success",
                    f"Successfully deleted {success_count} track(s) from disk and database.",
                )
            else:
                QMessageBox.information(
                    self,
                    "Success",
                    f"Successfully deleted {success_count} track(s) from database.",
                )

            # Refresh the table
            self.load_tracks_on_startup()

        else:
            error_text = (
                f"Deleted {success_count} of {len(track_data)} track(s).\n\nErrors:\n"
                + "\n".join(error_messages)
            )
            QMessageBox.warning(self, "Partial Success", error_text)

            # Refresh even on partial success to update the view
            if success_count > 0:
                self.load_tracks_on_startup()

    def _get_selected_tracks(self, selected_indices):
        """Get track objects for selected indices."""
        tracks = []
        for index in selected_indices:
            source_index = self.proxy_model.mapToSource(index)
            row = source_index.row()

            # Find track_id column dynamically
            track_id_index = list(self.columns.keys()).index("track_id")
            track_id_item = self.model.item(row, track_id_index)

            if not track_id_item:
                continue

            track_id = track_id_item.text()
            track = self.controller.get.get_entity_object("Track", track_id=track_id)
            if track:
                tracks.append(track)

        return tracks

    def show_column_menu(self):
        """Display a categorized menu for showing/hiding table columns using TrackField."""
        menu = QMenu(self)

        # Group columns by category
        categories = {}
        for i, (field_name, display_name) in enumerate(self.columns.items()):
            field_config = self.track_fields.get(field_name)
            category = field_config.category if field_config else "General"

            if category not in categories:
                categories[category] = []
            categories[category].append((i, field_name, display_name, field_config))

        # Create submenus for each category
        for category_name, columns in categories.items():
            if len(categories) > 1:  # Only use submenus if we have multiple categories
                category_menu = menu.addMenu(category_name)
            else:
                category_menu = menu

            for i, field_name, display_name, field_config in columns:
                action = category_menu.addAction(display_name)
                action.setCheckable(True)
                action.setChecked(not self.table.isColumnHidden(i))

                # Add tooltip if available
                if field_config and field_config.tooltip:
                    action.setToolTip(field_config.tooltip)

                # Connect to save function
                action.triggered.connect(
                    lambda checked, col=i: self._toggle_column_visibility(
                        col, not checked
                    )
                )

        menu.exec_(
            self.column_toggle_button.mapToGlobal(
                self.column_toggle_button.rect().bottomLeft()
            )
        )

    def _toggle_column_visibility(self, column_index, visible):
        """Toggle column visibility and save state."""
        self.table.setColumnHidden(column_index, not visible)
        self.save_column_state()

    def save_column_state(self):
        """Save current column visibility, order, and widths to config."""
        try:
            visible_columns = []
            column_order = []
            column_widths = []

            # Get current visible columns and their order
            for i in range(self.model.columnCount()):
                field_name = list(self.columns.keys())[i]
                if not self.table.isColumnHidden(i):
                    visible_columns.append(field_name)

                # Track order by original field names
                column_order.append(field_name)
                column_widths.append(self.table.columnWidth(i))

            # Save to config

            app_config.set_track_view_visible_columns(visible_columns)
            app_config.set_track_view_column_order(column_order)
            app_config.set_track_view_column_widths(column_widths)
            app_config.save()

            logger.info("Track view column state saved")

        except Exception as e:
            logger.error(f"Error saving column state: {e}")

    def load_column_state(self):
        """Load column visibility, order, and widths from config."""
        try:
            from config_setup import app_config

            # Get saved settings
            visible_columns = app_config.get_track_view_visible_columns()
            column_order = app_config.get_track_view_column_order()
            column_widths = app_config.get_track_view_column_widths()

            # If no saved order, use default column order
            if not column_order:
                column_order = list(self.columns.keys())

            # Reorder columns based on saved order
            self._reorder_columns(column_order)

            # Set column visibility
            if visible_columns:
                for i, field_name in enumerate(self.columns.keys()):
                    self.table.setColumnHidden(i, field_name not in visible_columns)

            # Restore column widths if available
            if column_widths and len(column_widths) == len(self.columns):
                for i, width in enumerate(column_widths):
                    if width > 0:  # Only set if width was saved
                        self.table.setColumnWidth(i, width)

        except Exception as e:
            logger.error(f"Error loading column state: {e}")

    def _reorder_columns(self, new_order):
        """Reorder table columns based on provided order."""
        try:
            # Create a mapping of current positions
            current_order = list(self.columns.keys())

            # Calculate the new column positions
            new_positions = [
                current_order.index(col) for col in new_order if col in current_order
            ]

            # Reorder the columns in the table
            for new_display_index, old_logical_index in enumerate(new_positions):
                current_visual_index = self.table.horizontalHeader().visualIndex(
                    old_logical_index
                )
                if current_visual_index != new_display_index:
                    self.table.horizontalHeader().moveSection(
                        current_visual_index, new_display_index
                    )

        except Exception as e:
            logger.error(f"Error reordering columns: {e}")

    def get_current_column_state(self):
        """Get current column state including order and visibility."""
        state = {
            "visible": [],
            "order": [],
            "widths": [],
            "positions": {},  # field_name -> visual_index
        }

        for i in range(self.model.columnCount()):
            field_name = list(self.columns.keys())[i]
            visual_index = self.table.horizontalHeader().visualIndex(i)

            state["order"].append(field_name)
            state["widths"].append(self.table.columnWidth(i))
            state["positions"][field_name] = visual_index

            if not self.table.isColumnHidden(i):
                state["visible"].append(field_name)

        # Sort order by visual position
        state["order"] = sorted(state["order"], key=lambda x: state["positions"][x])

        return state

    def show_column_customization(self):
        """Show column customization dialog."""
        dialog = ColumnCustomizationDialog(self, self)
        dialog.exec_()
