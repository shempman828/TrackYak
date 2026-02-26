# base_track_view.py

from PySide6.QtCore import QMimeData, QRegularExpression, QSortFilterProxyModel, Qt
from PySide6.QtGui import QAction, QDrag, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QTableView,
    QVBoxLayout,
)

from base_track_playlist_dialog import PlaylistSelectionDialog
from db_mapping_tracks import TRACK_FIELDS
from logger_config import logger


class BaseTrackView(QDialog):
    """Base reusable view for listing tracks. Takes in any number of track objects for table list display."""

    def __init__(
        self, controller, tracks, title="Tracks", enable_drag=False, enable_drop=False
    ):
        """
        Initialize the base track view.

        Args:
            controller: The main controller for data operations
            tracks: List of track objects to display
            title: Window title (default: "Tracks")
            enable_drag: Whether to enable dragging tracks from this view
            enable_drop: Whether to enable dropping tracks onto this view
        """
        super().__init__()
        self.controller = controller
        self.tracks = tracks
        self.track_fields = TRACK_FIELDS
        self.enable_drag = enable_drag
        self.enable_drop = enable_drop

        self.setWindowTitle(title)
        self.setMinimumSize(800, 600)

        # Initialize the models FIRST
        self.model = QStandardItemModel()
        self.proxy_model = QSortFilterProxyModel(self)

        self.layout = QVBoxLayout(self)

        # Info label
        self.info_label = QLabel(f"Showing {len(tracks)} tracks")
        self.layout.addWidget(self.info_label)

        # Search bar
        self.search_bar = QLineEdit(self)
        self.search_bar.setPlaceholderText("Search tracks...")
        self.search_bar.textChanged.connect(self.filter_tracks)

        # Table setup
        self.table = QTableView(self)
        self._setup_table()  # This creates and configures the table

        # Configure proxy model
        self.proxy_model.setSourceModel(self.model)
        self.proxy_model.setFilterCaseSensitivity(Qt.CaseInsensitive)
        self.proxy_model.setFilterRegularExpression(QRegularExpression())
        self.proxy_model.setFilterKeyColumn(-1)  # Filter across all columns
        self.proxy_model.setSortRole(Qt.UserRole)
        self.table.setModel(self.proxy_model)

        # Layout
        search_layout = QHBoxLayout()
        search_layout.addWidget(self.search_bar)
        self.layout.addLayout(search_layout)
        self.layout.addWidget(self.table)

        # Set up context menu
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.show_context_menu)
        self.setup_context_menu()

        # Set up drag and drop if enabled - MOVED TO AFTER TABLE CREATION
        if self.enable_drag:
            self.setup_drag_support()
        if self.enable_drop:
            self.setup_drop_support()

        # Load tracks
        self.load_data(tracks)

    def _setup_table(self):
        """Set up the table with essential columns only."""
        # Define essential columns to show
        self.essential_columns = {
            "track_name": "Title",
            "artist_name": "Artist",
            "album_name": "Album",
            "track_number": "#",
            "duration": "Duration",
            "year": "Year",
        }

        # Set up the model (self.model already initialized)
        self.model.setColumnCount(len(self.essential_columns))
        self.model.setHorizontalHeaderLabels(self.essential_columns.values())

        # Enable sorting
        self.table.setSortingEnabled(True)

        # Configure header behavior
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)

        # Set selection behavior - ENABLE MULTIPLE SELECTION
        self.table.setSelectionBehavior(QTableView.SelectRows)
        self.table.setSelectionMode(
            QTableView.ExtendedSelection
        )  # Changed from SingleSelection to ExtendedSelection
        self.table.setAlternatingRowColors(True)

        # Hide vertical headers
        self.table.verticalHeader().setVisible(False)

        # Make table read-only
        self.table.setEditTriggers(QTableView.NoEditTriggers)

    def setup_context_menu(self):
        """Set up context menu for track selection."""
        self.context_menu = QMenu(self)

        # Initialize submenus FIRST
        self.add_to_playlist_menu = QMenu("Add to Playlist", self)
        self.add_to_mood_menu = QMenu("Add to Mood", self)  # Initialize here

        # Add to Queue actions
        self.add_to_queue_action = QAction("Add to Queue", self)
        self.add_to_queue_action.triggered.connect(self.add_selected_to_queue)

        self.add_to_queue_next_action = QAction("Add to Queue (Next)", self)
        self.add_to_queue_next_action.triggered.connect(
            lambda: self.add_selected_to_queue(insert_next=True)
        )

        # Add separator and menu items
        self.context_menu.addAction(self.add_to_queue_action)
        self.context_menu.addAction(self.add_to_queue_next_action)
        self.context_menu.addSeparator()
        self.context_menu.addMenu(self.add_to_playlist_menu)
        self.context_menu.addMenu(self.add_to_mood_menu)

    def show_context_menu(self, position):
        """Show context menu at the given position."""
        selected_indexes = self.table.selectionModel().selectedRows()
        if not selected_indexes:
            return

        # Get selected track IDs
        selected_tracks = self.get_selected_tracks()
        track_ids = [str(track.track_id) for track in selected_tracks]

        # Enable/disable menu items based on selection
        has_selection = len(selected_indexes) > 0
        self.add_to_queue_action.setEnabled(has_selection)
        self.add_to_queue_next_action.setEnabled(has_selection)

        # Clear previous menu items
        self.add_to_playlist_menu.clear()
        self.add_to_mood_menu.clear()

        # Load playlists into submenu
        self._populate_playlist_menu(track_ids)

        # Load moods into submenu
        self._populate_mood_menu(track_ids)

        self.context_menu.exec_(self.table.mapToGlobal(position))

    def get_selected_tracks(self):
        """Get list of selected track objects."""
        selected_indexes = self.table.selectionModel().selectedRows()
        selected_tracks = []

        for index in selected_indexes:
            # Map to source model through proxy
            source_index = self.proxy_model.mapToSource(index)
            row = source_index.row()
            if 0 <= row < len(self.tracks):
                selected_tracks.append(self.tracks[row])

        return selected_tracks

    def add_selected_to_queue(self, insert_next=False):
        """Add selected tracks to the playback queue."""
        selected_tracks = self.get_selected_tracks()
        if not selected_tracks:
            return

        queue_manager = getattr(self.controller, "queue_manager", None)
        if not queue_manager:
            # Try to find queue manager in controller
            if hasattr(self.controller, "mediaplayer") and hasattr(
                self.controller.mediaplayer, "queue_manager"
            ):
                queue_manager = self.controller.mediaplayer.queue_manager
            else:
                logger.warning("Queue manager not found in controller")
                return

        if insert_next and hasattr(queue_manager, "insert_tracks_next"):
            # Insert after current playing track
            queue_manager.insert_tracks_next(selected_tracks)
        else:
            # Add to end of queue
            queue_manager.add_tracks_to_queue(selected_tracks)

        logger.info(f"Added {len(selected_tracks)} track(s) to queue")

    def show_playlist_selection_dialog(self):
        """Show dialog to select a playlist to add tracks to."""
        selected_tracks = self.get_selected_tracks()
        if not selected_tracks:
            return

        # Get all non-smart playlists
        try:
            all_playlists = self.controller.get.get_all_entities("Playlist", is_smart=0)

            # Create and show playlist selection dialog
            dialog = PlaylistSelectionDialog(all_playlists, self.controller, self)
            if dialog.exec_():
                selected_playlist = dialog.get_selected_playlist()
                if selected_playlist:
                    self.add_tracks_to_playlist(selected_playlist, selected_tracks)

        except Exception as e:
            logger.error(f"Error showing playlist selection dialog: {e}")
            from PySide6.QtWidgets import QMessageBox

            QMessageBox.warning(self, "Error", f"Failed to load playlists: {str(e)}")

    def load_data(self, tracks):
        """Populate the table with track metadata."""
        self.model.setRowCount(0)  # Clear existing rows
        self.tracks = tracks  # Update tracks reference

        for track in tracks:
            row = []
            for db_field, display_name in self.essential_columns.items():
                field_config = self.track_fields.get(db_field)
                value = self._get_formatted_field_value(track, db_field, field_config)
                item = QStandardItem(str(value))

                # Set sorting data for numeric fields
                if field_config and field_config.type in (int, float):
                    raw_value = getattr(track, db_field, None)
                    if raw_value is not None:
                        item.setData(raw_value, Qt.UserRole)

                row.append(item)
            self.model.appendRow(row)

        logger.info(f"Loaded {len(tracks)} tracks into base track view.")

    def _get_formatted_field_value(self, track, db_field, field_config):
        """Get formatted value for a track field."""
        # Handle special relationship fields
        if db_field == "album_name":
            return track.album.album_name if track.album else "Unknown Album"
        elif db_field == "artist_name":
            return self._get_artist_name(track)

        # Get the raw value from the track object
        raw_value = getattr(track, db_field, "")

        # Apply formatting
        return self._format_value(raw_value, db_field, field_config)

    def _get_artist_name(self, track):
        """Extract artist name from track."""
        if track.artist_roles:
            primary_artist = next(
                (
                    ar
                    for ar in track.artist_roles
                    if ar.role.role_name == "Primary Artist"
                ),
                None,
            )
            if primary_artist:
                return primary_artist.artist.artist_name
            else:
                return track.artist_roles[0].artist.artist_name
        else:
            return "Unknown Artist"

    def _format_value(self, value, db_field, field_config):
        """Format field value."""
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

        return str(value)

    def filter_tracks(self, text):
        """Filter tracks based on search text."""
        regex = QRegularExpression(text, QRegularExpression.CaseInsensitiveOption)
        self.proxy_model.setFilterRegularExpression(regex)

    def setup_drag_support(self):
        """Set up drag support for the table."""
        self.table.setDragEnabled(True)
        self.table.setSelectionMode(QTableView.ExtendedSelection)
        self.table.viewport().setAcceptDrops(False)  # Don't accept drops internally

    def setup_drop_support(self):
        """Set up drop support for the table."""
        self.table.setAcceptDrops(True)
        self.table.setDropIndicatorShown(True)
        self.table.viewport().setAcceptDrops(True)

    # Drag methods
    def mousePressEvent(self, event):
        """Start drag operation."""
        if self.enable_drag and event.button() == Qt.LeftButton:
            self.drag_start_position = event.pos()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        """Handle mouse movement for drag initiation."""
        if not self.enable_drag or not (event.buttons() & Qt.LeftButton):
            return

        if not hasattr(self, "drag_start_position"):
            return

        distance = (event.pos() - self.drag_start_position).manhattanLength()
        if distance < 10:  # Minimum drag distance
            return

        selected_tracks = self.get_selected_tracks()
        if not selected_tracks:
            return

        # Create drag object
        drag = QDrag(self)
        mime_data = QMimeData()

        # Create comma-separated list of track IDs
        track_ids = [str(track.track_id) for track in selected_tracks]
        mime_data.setData("application/x-track-id", ",".join(track_ids).encode())

        # Set text representation
        track_names = [track.track_name for track in selected_tracks]
        mime_data.setText(", ".join(track_names))

        drag.setMimeData(mime_data)
        drag.exec_(Qt.CopyAction)

    # Drop methods
    def dragEnterEvent(self, event):
        """Handle drag enter event."""
        if self.enable_drop and event.mimeData().hasFormat("application/x-track-id"):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        """Handle drag move event."""
        if self.enable_drop and event.mimeData().hasFormat("application/x-track-id"):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        """Handle drop event - to be overridden by subclasses."""
        if not self.enable_drop or not event.mimeData().hasFormat(
            "application/x-track-id"
        ):
            event.ignore()
            return

        event.acceptProposedAction()
        logger.warning("dropEvent should be overridden by subclass")

    def get_track_ids(self, tracks=None):
        """Get track IDs from track objects."""
        if tracks is None:
            tracks = self.get_selected_tracks()
        return [track.track_id for track in tracks] if tracks else []

    def _populate_playlist_menu(self, track_ids):
        """Populate the playlist submenu with available playlists."""
        try:
            playlists = self.controller.get.get_all_entities("Playlist")
            if not playlists:
                self.add_to_playlist_menu.addAction(
                    "No playlists available"
                ).setEnabled(False)
                return

            for playlist in playlists:
                action = self.add_to_playlist_menu.addAction(playlist.playlist_name)
                # Store playlist ID and all selected track IDs
                action.setData((playlist.playlist_id, track_ids))
                action.triggered.connect(self._add_to_playlist_from_menu)

        except Exception as e:
            logger.error(f"Error loading playlists for context menu: {str(e)}")
            self.add_to_playlist_menu.addAction("Error loading playlists").setEnabled(
                False
            )

    def _populate_mood_menu(self, track_ids):
        """Populate the mood submenu with available moods."""
        try:
            moods = self.controller.get.get_all_entities("Mood")
            if not moods:
                self.add_to_mood_menu.addAction("No moods available").setEnabled(False)
                return

            for mood in moods:
                action = self.add_to_mood_menu.addAction(mood.mood_name)
                # Store mood ID and all selected track IDs
                action.setData((mood.mood_id, track_ids))
                action.triggered.connect(self._add_to_mood_from_menu)

        except Exception as e:
            logger.error(f"Error loading moods for context menu: {str(e)}")
            self.add_to_mood_menu.addAction("Error loading moods").setEnabled(False)

    def _add_to_playlist_from_menu(self):
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

            for track_id_str in track_ids:
                track_id = int(track_id_str)

                # Check if track already exists in playlist
                existing = self.controller.get.get_entity_links(
                    "PlaylistTracks", playlist_id=playlist_id, track_id=track_id
                )

                if not existing:
                    # Add the track to playlist
                    if self.controller.add.add_entity_link(
                        "PlaylistTracks",
                        playlist_id=playlist_id,
                        track_id=track_id,
                        position=next_position,
                    ):
                        success_count += 1
                        next_position += 1  # Increment for next track
                    else:
                        logger.warning(
                            f"Failed to add track {track_id} to playlist {playlist_id}"
                        )
                else:
                    logger.debug(f"Track {track_id} already in playlist {playlist_id}")

            # Show results based on success
            if success_count == len(track_ids):
                QMessageBox.information(
                    self,
                    "Success",
                    f"All {success_count} track(s) added to playlist successfully!",
                )
            elif success_count > 0:
                QMessageBox.warning(
                    self,
                    "Partial Success",
                    f"{success_count} of {len(track_ids)} track(s) added (some might already be in the playlist).",
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

    def _add_to_mood_from_menu(self):
        """Handle adding multiple tracks to a mood from the context menu."""
        action = self.sender()
        if not action:
            return

        mood_id, track_ids = action.data()
        success_count = 0
        error_messages = []

        try:
            for track_id_str in track_ids:
                track_id = int(track_id_str)

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
                    f"All {success_count} track(s) added to mood successfully!",
                )
            elif success_count > 0:
                QMessageBox.warning(
                    self,
                    "Partial Success",
                    f"{success_count} of {len(track_ids)} track(s) added.\n"
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
