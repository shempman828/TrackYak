# base_track_view.py

import csv
import random

from PySide6.QtCore import QMimeData, Qt, Signal
from PySide6.QtGui import QAction, QDrag, QKeySequence, QShortcut, QStandardItemModel
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMenu, QMessageBox, QPushButton, QTableView, QToolButton, QVBoxLayout
from sqlalchemy.exc import SQLAlchemyError

from src.common.dialogs.delete_confirmation import confirm_delete_with_file_option
from src.common.widgets.entity_submenu import populate_entity_submenu, selection_membership
from src.db.db_mapping_tracks import TRACK_FIELDS
from src.foundation.censor import censor_text
from src.foundation.logger_config import logger
from src.foundation.status_utility import show_status_message
from src.track.track_edit import MultiTrackEditDialog, TrackEditDialog
from src.track.view.track_view_columns import TrackViewColumnsMixin
from src.track.view.track_view_data import TrackViewDataMixin
from src.track.view.track_view_filter import SEARCH_ALL
from src.track.view.track_view_search import TrackViewSearchMixin


class BaseTrackView(QDialog, TrackViewColumnsMixin, TrackViewDataMixin, TrackViewSearchMixin):
    """Base reusable view for listing tracks.

    Takes in any number of track objects for table list display.

    Column setup/customization/persistence (TrackViewColumnsMixin) and
    background search/sort/lookup-caching (TrackViewDataMixin,
    TrackViewSearchMixin) are shared with the main library TrackView, so
    both stay in feature parity instead of drifting apart. Everything
    dialog-specific -- the fixed-list constructor, CSV export, the
    shuffle-and-play button, the persistent context menu, and optional
    drag/drop -- stays local to this class.
    """

    track_deleted = Signal(int)

    def __init__(self, controller, tracks, title="Tracks", enable_drag=False, enable_drop=False):
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

        # Lazy loading / search / sort state (shared mixins read these)
        self._all_tracks = []
        self._loaded_count = 0
        self._filter_active = False
        self._filtered_tracks = []
        self._tracks_loaded = False
        self._filter_worker = None
        self._sort_worker = None
        self._lookup_thread = None
        self._lookup_worker = None
        self._artist_name_cache = {}
        self._artist_sort_cache = {}
        self._album_cache = {}
        self._disc_number_cache = {}
        self._search_field_name = SEARCH_ALL
        # BaseTrackView's track list is normally small (a mood's tracks, a
        # duplicate group, ...); scope the bulk lookup-cache queries to it
        # instead of joining the whole library on every popup open.
        self._scope_lookup_caches_to_tracks = True

        self.setWindowTitle(title)
        self.setMinimumSize(800, 600)

        # Initialize the model FIRST
        self.model = QStandardItemModel()
        self.columns = {field_name: field_config.friendly for field_name, field_config in self.track_fields.items() if field_config.friendly}

        self.layout = QVBoxLayout(self)

        # Status label. Kept as both names: the shared TrackViewDataMixin
        # writes to `status_label` (same name TrackView uses); some callers
        # (mood_dialog.py, playlist_tracks_window.py) reach in via the
        # original `info_label` name to reparent or update it directly.
        self.status_label = QLabel(f"Showing {len(tracks)} tracks")
        self.info_label = self.status_label
        self.layout.addWidget(self.status_label)

        # Search bar, column customization, shuffle, and export buttons
        search_layout = QHBoxLayout()

        self.search_bar = QLineEdit(self)
        self.search_bar.setPlaceholderText("Search tracks...")
        self.search_bar.textChanged.connect(lambda _text=None: self._apply_search_filter())
        search_layout.addWidget(self.search_bar)

        # Columns button: show/hide, reorder, resize -- shared with TrackView
        self.columns_button = QToolButton(self)
        self.columns_button.setText("⚙ Columns")
        self.columns_button.setToolTip("Column visibility and order")
        self.columns_button.setPopupMode(QToolButton.InstantPopup)
        columns_menu = QMenu(self.columns_button)
        columns_menu.addAction("Toggle Columns", self.show_column_menu)
        columns_menu.addAction("Column Order && Visibility", self.show_column_customization)
        self.columns_button.setMenu(columns_menu)
        search_layout.addWidget(self.columns_button)

        # Shuffle All button
        self.shuffle_button = QPushButton("🔀 Shuffle All")
        self.shuffle_button.setToolTip("Shuffle all tracks and add to queue")
        self.shuffle_button.clicked.connect(self.shuffle_all_tracks)
        self.shuffle_button.setMaximumWidth(120)
        search_layout.addWidget(self.shuffle_button)

        # Export CSV button
        self.export_csv_button = QPushButton("📄 Export CSV")
        self.export_csv_button.setToolTip("Export the track list to a CSV file")
        self.export_csv_button.clicked.connect(self.export_tracks_to_csv)
        self.export_csv_button.setMaximumWidth(120)
        search_layout.addWidget(self.export_csv_button)

        self.layout.addLayout(search_layout)

        # Table setup
        self.table = QTableView(self)
        self.table.setModel(self.model)
        self._setup_table()  # This creates and configures the table
        self.table.verticalScrollBar().valueChanged.connect(self._on_scroll)

        # Layout
        self.layout.addWidget(self.table)

        # Set up context menu
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.show_context_menu)
        self.setup_context_menu()

        # Double-click a row to preview-play that track
        self.table.doubleClicked.connect(self._on_row_double_clicked)

        # Keyboard shortcuts
        copy_shortcut = QShortcut(QKeySequence.Copy, self.table)
        copy_shortcut.activated.connect(self._copy_selected_rows)
        delete_shortcut = QShortcut(QKeySequence.Delete, self.table)
        delete_shortcut.activated.connect(self._delete_selected_tracks)

        # Set up drag and drop if enabled - MOVED TO AFTER TABLE CREATION
        if self.enable_drag:
            self.setup_drag_support()
        if self.enable_drop:
            self.setup_drop_support()

        # Load tracks
        self.load_data(tracks)

    def _setup_table(self):
        """Set up the table with the full shared column set."""
        self.model.setColumnCount(len(self.columns))
        self.model.setHorizontalHeaderLabels(list(self.columns.values()))

        self.table.setSortingEnabled(False)  # Background SortWorker handles sorting

        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setStretchLastSection(False)
        header.setDefaultSectionSize(120)
        header.setSortIndicatorShown(True)

        self._sort_column_index = -1
        self._sort_ascending = True
        header.sectionClicked.connect(self._on_header_clicked)

        # Set selection behavior - ENABLE MULTIPLE SELECTION
        self.table.setSelectionBehavior(QTableView.SelectRows)
        self.table.setSelectionMode(QTableView.ExtendedSelection)  # Changed from SingleSelection to ExtendedSelection
        self.table.setAlternatingRowColors(True)

        # Hide vertical headers
        self.table.verticalHeader().setVisible(False)

        # Make table read-only
        self.table.setEditTriggers(QTableView.NoEditTriggers)

        self._set_initial_column_visibility()
        self.load_column_state()

    def setup_context_menu(self):
        """Set up context menu for track selection."""
        self.context_menu = QMenu(self)

        # Initialize submenus FIRST
        self.add_to_playlist_menu = QMenu("➕  Add to Playlist", self)  # noqa: RUF001
        self.add_to_mood_menu = QMenu("🎭  Add to Mood", self)  # Initialize here

        # Add to Queue actions
        self.add_to_queue_action = QAction("Add to Queue", self)
        self.add_to_queue_action.triggered.connect(self.add_selected_to_queue)

        self.add_to_queue_next_action = QAction("Add to Queue (Next)", self)
        self.add_to_queue_next_action.triggered.connect(lambda: self.add_selected_to_queue(insert_next=True))

        # Edit action - label/enabled state set per-selection in show_context_menu
        self.edit_action = QAction("✏️ Edit Track", self)
        self.edit_action.triggered.connect(self.edit_selected_tracks)

        # Add separator and menu items
        self.context_menu.addAction(self.add_to_queue_action)
        self.context_menu.addAction(self.add_to_queue_next_action)
        self.context_menu.addSeparator()
        self.context_menu.addAction(self.edit_action)
        self.context_menu.addSeparator()
        self.context_menu.addMenu(self.add_to_playlist_menu)
        self.context_menu.addMenu(self.add_to_mood_menu)
        self.context_menu.addSeparator()

        # Delete action
        self.delete_tracks_action = QAction("🗑 Delete Tracks...", self)
        self.delete_tracks_action.triggered.connect(self._delete_selected_tracks)
        self.context_menu.addAction(self.delete_tracks_action)

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

        count = len(selected_tracks)
        self.edit_action.setText(f"✏️ Edit {count} Tracks" if count > 1 else "✏️ Edit Track")
        self.edit_action.setEnabled(has_selection)

        # Clear previous menu items
        self.add_to_playlist_menu.clear()
        self.add_to_mood_menu.clear()

        # Load playlists into submenu
        self._populate_playlist_menu(selected_tracks, track_ids)

        # Load moods into submenu
        self._populate_mood_menu(selected_tracks, track_ids)

        self.context_menu.exec_(self.table.mapToGlobal(position))

    def get_selected_tracks(self):
        """Get list of selected track objects."""
        selected_indexes = self.table.selectionModel().selectedRows()
        selected_tracks = []
        track_list = self._filtered_tracks if self._filter_active else self._all_tracks

        for index in selected_indexes:
            row = index.row()
            if 0 <= row < len(track_list):
                selected_tracks.append(track_list[row])

        return selected_tracks

    def _on_row_double_clicked(self, index):
        """Play a short preview of the double-clicked track."""
        row = index.row()
        track_list = self._filtered_tracks if self._filter_active else self._all_tracks
        if not (0 <= row < len(track_list)):
            return

        track = track_list[row]
        file_path = getattr(track, "track_file_path", None)
        if not file_path or not hasattr(self.controller, "mediaplayer"):
            return

        try:
            from pathlib import Path

            if self.controller.mediaplayer.load_track(Path(file_path)):
                self.controller.mediaplayer.play()
            else:
                logger.warning(f"Failed to load track for preview: {file_path}")
        except (OSError, RuntimeError) as e:
            logger.error(f"Error previewing track: {e}")

    def _copy_selected_rows(self):
        """Copy the selected rows (visible columns, in visual order) to the clipboard."""
        selected = self.table.selectionModel().selectedRows()
        if not selected:
            return

        header = self.table.horizontalHeader()
        visual_order = [header.logicalIndex(v) for v in range(header.count()) if not self.table.isColumnHidden(header.logicalIndex(v))]

        column_labels = list(self.columns.values())
        header_labels = [column_labels[i] for i in visual_order]
        lines = ["\t".join(header_labels)]

        for index in sorted(selected, key=lambda i: i.row()):
            row_data = []
            for col_i in visual_order:
                item = self.model.item(index.row(), col_i)
                row_data.append(item.text() if item else "")
            lines.append("\t".join(row_data))

        QApplication.clipboard().setText("\n".join(lines))
        logger.debug(f"Copied {len(selected)} row(s) to clipboard")

    def add_selected_to_queue(self, insert_next=False):
        """Add selected tracks to the playback queue."""
        selected_tracks = self.get_selected_tracks()
        if not selected_tracks:
            return

        queue_manager = getattr(self.controller, "queue_manager", None)
        if not queue_manager:
            # Try to find queue manager in controller
            if hasattr(self.controller, "mediaplayer") and hasattr(self.controller.mediaplayer, "queue_manager"):
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

    def edit_selected_tracks(self):
        """Open the track edit dialog for the current selection (single or multi)."""
        tracks = self.get_selected_tracks()
        if not tracks:
            return

        try:
            dialog = TrackEditDialog(tracks[0], self.controller, self) if len(tracks) == 1 else MultiTrackEditDialog(tracks, self.controller, self)
            dialog.accepted.connect(lambda: self.load_data(self._all_tracks))
            self._track_edit_dialog = dialog
            dialog.show()
        except RuntimeError as e:
            logger.error(f"Error opening track edit dialog: {e}")
            QMessageBox.warning(self, "Error", f"Failed to open track editor: {e!s}")

    def _get_queue_manager(self):
        """Helper to get the queue manager from controller."""
        queue_manager = getattr(self.controller, "queue_manager", None)
        if not queue_manager and hasattr(self.controller, "mediaplayer"):
            queue_manager = getattr(self.controller.mediaplayer, "queue_manager", None)
        return queue_manager

    def _get_artist_name(self, track):
        """Extract artist name from the bulk-fetched cache (see _build_lookup_caches).

        Must never fall back to a lazy-loaded `track.artist_roles` relationship
        access: this is also used as FilterWorker's artist-lookup callback,
        which runs on a background thread where the ORM session is off-limits.
        """
        cache = getattr(self, "_artist_name_cache", None) or {}
        return cache.get(track.track_id, "Unknown Artist")

    def _format_value(self, value, field_name, field_config):
        """Format field value for table/CSV display."""
        if value is None:
            return ""
        if field_name == "duration" and isinstance(value, (int, float)):
            total_s = int(value)
            m, s = divmod(total_s, 60)
            return f"{m}:{s:02d}"
        if field_name == "file_size" and isinstance(value, (int, float)):
            return f"{value / (1024 * 1024):.1f} MB"
        if field_name in ("track_name", "album_name", "lyrics"):
            return censor_text(str(value))
        return str(value)

    def shuffle_all_tracks(self):
        """Shuffle ALL tracks and add them to the queue, then start playing."""
        tracks_to_shuffle = self._filtered_tracks.copy() if self._filter_active else self._all_tracks.copy()

        if not tracks_to_shuffle:
            show_status_message(self, "No tracks available to shuffle.")
            return

        random.shuffle(tracks_to_shuffle)
        queue_manager = self._get_queue_manager()

        if not queue_manager:
            show_status_message(self, "Could not access the playback queue.")
            return

        if hasattr(queue_manager, "clear_queue"):
            queue_manager.clear_queue()
        queue_manager.add_tracks_to_queue(tracks_to_shuffle)

        # Start playing first track
        if tracks_to_shuffle and hasattr(self.controller, "mediaplayer"):
            try:
                from pathlib import Path

                first_track = tracks_to_shuffle[0]
                track_path = Path(first_track.track_file_path)
                if self.controller.mediaplayer.load_track(track_path):
                    self.controller.mediaplayer.play()
                    logger.info(f"Started shuffled playback: {len(tracks_to_shuffle)} tracks")
            except (OSError, RuntimeError, TypeError) as e:
                logger.error(f"Error starting playback: {e}")

    def export_tracks_to_csv(self):
        """Export the currently displayed track list (respecting any active filter) to CSV."""
        tracks_to_export = self._filtered_tracks if self._filter_active else self._all_tracks

        if not tracks_to_export:
            show_status_message(self, "No tracks available to export.")
            return

        file_path, _ = QFileDialog.getSaveFileName(self, "Export Track List to CSV", "tracks.csv", "CSV Files (*.csv)")
        if not file_path:
            return

        try:
            from pathlib import Path

            with Path(file_path).open("w", newline="", encoding="utf-8") as csv_file:
                writer = csv.writer(csv_file)
                writer.writerow(self.columns.values())
                for track in tracks_to_export:
                    writer.writerow(self._format_value(self._field_value(track, db_field), db_field, self.track_fields.get(db_field)) for db_field in self.columns)

            show_status_message(self, f"Exported {len(tracks_to_export)} track(s) to {file_path}")
            logger.info(f"Exported {len(tracks_to_export)} track(s) to CSV: {file_path}")
        except OSError as e:
            logger.error(f"Error exporting tracks to CSV: {e}")
            QMessageBox.critical(self, "Error", f"Failed to export track list:\n{e!s}")

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
        """Handle drop event - to be overridden by subclass."""
        if not self.enable_drop or not event.mimeData().hasFormat("application/x-track-id"):
            event.ignore()
            return

        event.acceptProposedAction()
        logger.warning("dropEvent should be overridden by subclass")

    def _populate_playlist_menu(self, selected_tracks, track_ids):
        """Populate the playlist submenu (shared hierarchical builder)."""
        full, partial = selection_membership(selected_tracks, "playlists", "playlist_id")
        populate_entity_submenu(
            self.add_to_playlist_menu,
            controller=self.controller,
            entity_type="Playlist",
            on_trigger=self._add_to_playlist_from_menu,
            member_ids=full,
            partial_ids=partial,
            make_action_data=lambda entity_id: (entity_id, track_ids),
        )

    def _populate_mood_menu(self, selected_tracks, track_ids):
        """Populate the mood submenu (shared hierarchical builder)."""
        full, partial = selection_membership(selected_tracks, "moods", "mood_id")
        populate_entity_submenu(
            self.add_to_mood_menu,
            controller=self.controller,
            entity_type="Mood",
            on_trigger=self._add_to_mood_from_menu,
            member_ids=full,
            partial_ids=partial,
            make_action_data=lambda entity_id: (entity_id, track_ids),
        )

    def _add_to_playlist_from_menu(self):
        """Handle adding multiple tracks to a playlist from the context menu."""
        action = self.sender()
        if not action:
            return

        playlist_id, track_ids = action.data()
        success_count = 0

        try:
            # Get the current maximum position in the playlist
            existing_tracks = self.controller.get.get_entity_links("PlaylistTracks", playlist_id=playlist_id)
            next_position = max([t.position for t in existing_tracks], default=0) + 1

            for track_id_str in track_ids:
                track_id = int(track_id_str)

                # Check if track already exists in playlist
                existing = self.controller.get.get_entity_links("PlaylistTracks", playlist_id=playlist_id, track_id=track_id)

                if not existing:
                    # Add the track to playlist
                    if self.controller.add.add_entity_link("PlaylistTracks", playlist_id=playlist_id, track_id=track_id, position=next_position):
                        success_count += 1
                        next_position += 1  # Increment for next track
                    else:
                        logger.warning(f"Failed to add track {track_id} to playlist {playlist_id}")
                else:
                    logger.debug(f"Track {track_id} already in playlist {playlist_id}")

            # Show results based on success
            if success_count == len(track_ids):
                show_status_message(self, f"All {success_count} track(s) added to playlist successfully!")
            elif success_count > 0:
                QMessageBox.warning(self, "Partial Success", f"{success_count} of {len(track_ids)} track(s) added (some might already be in the playlist).")
            else:
                show_status_message(self, "No tracks were added (they might already be in the playlist).")
        except (SQLAlchemyError, ValueError) as e:
            logger.error(f"Error adding tracks to playlist: {e!s}")
            QMessageBox.critical(self, "Error", f"Failed to add tracks to playlist:\n{e!s}")

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
                existing_associations = self.controller.get.get_entity_links("MoodTrackAssociation", mood_id=mood_id, track_id=track_id)

                if not existing_associations:
                    # Add the track to mood
                    if self.controller.add.add_entity_link("MoodTrackAssociation", mood_id=mood_id, track_id=track_id):
                        success_count += 1
                    else:
                        error_messages.append(f"Failed to add track {track_id} to mood")
                else:
                    # Track already in mood
                    error_messages.append(f"Track {track_id} is already in this mood")

            # Show results based on success
            if success_count == len(track_ids):
                show_status_message(self, f"All {success_count} track(s) added to mood successfully!")
            elif success_count > 0:
                QMessageBox.warning(
                    self,
                    "Partial Success",
                    f"{success_count} of {len(track_ids)} track(s) added.\nSome tracks might already be in this mood:\n\n".join(error_messages[-3:]),  # Show last 3 errors
                )
            else:
                show_status_message(self, "No tracks were added. All selected tracks are already in this mood.")

        except (SQLAlchemyError, ValueError) as e:
            logger.error(f"Error adding tracks to mood: {e!s}")
            QMessageBox.critical(self, "Error", f"Failed to add tracks to mood:\n{e!s}")

    def _delete_selected_tracks(self):
        """Delete selected tracks: DB-only, or DB + audio file(s) from disk."""
        tracks = self.get_selected_tracks()
        if not tracks:
            return

        count = len(tracks)
        names = ", ".join(getattr(t, "track_name", f"ID {t.track_id}") for t in tracks[:3])
        if count > 3:
            names += f" … and {count - 3} more"

        choice = confirm_delete_with_file_option(self, "Delete Tracks", f"Delete {count} track(s)?\n\n{names}")
        if choice is None:
            return
        delete_files = choice == "db_and_file"

        # Collect file paths BEFORE the DB delete — ORM objects may become stale after.
        file_paths = []
        if delete_files:
            for track in tracks:
                fp = getattr(track, "track_file_path", None)
                if fp:
                    file_paths.append(fp)

        # Batch delete from DB (single query via entity_ids)
        entity_ids = [track.track_id for track in tracks]
        ok = self.controller.delete.delete_entity("Track", entity_ids=entity_ids)
        deleted_ids = entity_ids if ok else []
        if ok:
            logger.info(f"Batch-deleted {count} track(s) from DB")
        else:
            logger.error("Batch delete returned False — some tracks may not have been removed")

        if delete_files and file_paths:
            for fp in file_paths:
                try:
                    self.controller.delete.delete_file(file_path=fp)
                except (OSError, SQLAlchemyError) as e:
                    logger.error(f"Error deleting file {fp}: {e}")

        # Remove deleted tracks from our internal list and refresh display
        self._all_tracks = [t for t in self._all_tracks if t.track_id not in deleted_ids]
        self.load_data(self._all_tracks)

        for tid in deleted_ids:
            self.track_deleted.emit(tid)

        what = "file(s) and DB record(s)" if delete_files else "track(s) from DB"
        logger.info(f"Deleted {len(deleted_ids)}/{count} {what}")
