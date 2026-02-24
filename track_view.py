from pathlib import Path

from PySide6.QtCore import (
    QByteArray,
    QMimeData,
    QRegularExpression,
    QSortFilterProxyModel,
    Qt,
)
from PySide6.QtGui import (
    QAction,
    QDrag,
    QPainter,
    QPixmap,
    QStandardItem,
    QStandardItemModel,
)
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
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

    # ──────────────────────────────────────────────────────────────────────
    #  Columns
    # ──────────────────────────────────────────────────────────────────────

    def _initialize_columns(self):
        """Initialize columns dictionary using TrackField configuration."""
        self.columns = {}
        for field_name, field_config in self.track_fields.items():
            if field_config.friendly:
                self.columns[field_name] = field_config.friendly

    def _setup_table(self):
        """Set up the table with columns for all metadata fields."""
        self.columns = {}
        for field_name, field_config in self.track_fields.items():
            if field_config.friendly:
                self.columns[field_name] = field_config.friendly

        self.model.setColumnCount(len(self.columns))
        self.model.setHorizontalHeaderLabels(self.columns.values())

        self.table.setSortingEnabled(True)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.setSelectionBehavior(QTableView.SelectRows)
        self.table.setSelectionMode(QTableView.ExtendedSelection)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableView.NoEditTriggers)

        # Drag support
        self.table.setDragEnabled(True)
        self.table.setDragDropMode(QTableView.DragOnly)
        self.table.setDefaultDropAction(Qt.CopyAction)
        self.table.startDrag = self.startDrag

        self._set_initial_column_visibility()

    def _set_initial_column_visibility(self):
        """Hide technical / low-interest columns by default."""
        hidden_by_default = {
            "file_size",
            "bit_rate",
            "sample_rate",
            "track_id",
            "track_file_path",
        }
        for i, (field_name, _) in enumerate(self.columns.items()):
            field_config = self.track_fields.get(field_name)
            if field_config and field_config.category == "Technical":
                self.table.setColumnHidden(i, True)
            elif field_name in hidden_by_default:
                self.table.setColumnHidden(i, True)

    # ──────────────────────────────────────────────────────────────────────
    #  Data loading
    # ──────────────────────────────────────────────────────────────────────

    def load_tracks_on_startup(self):
        """Load tracks from database."""
        try:
            tracks = self.controller.get.get_all_entities("Track")
            self.load_data(tracks or [])
            if tracks:
                logger.info("Successfully loaded tracks on startup.")
            else:
                logger.warning("No tracks found in the database — empty list shown.")
        except Exception as e:
            logger.error(f"Error loading tracks on startup: {e}")
            self.load_data([])

    def load_data(self, tracks):
        """Populate the table with track metadata."""
        self.model.setRowCount(0)
        column_keys = list(self.columns.keys())

        for track in tracks:
            row_items = []
            for field_name in column_keys:
                field_config = self.track_fields.get(field_name)
                value = getattr(track, field_name, None)

                # Special computed fields
                if field_name == "artist_name":
                    value = self._get_artist_name(track)

                display_value = self._format_value(value, field_name, field_config)
                item = QStandardItem(display_value)
                item.setEditable(False)

                # Store raw value for sorting
                if isinstance(value, (int, float)):
                    item.setData(value, Qt.UserRole)
                else:
                    item.setData(display_value, Qt.UserRole)

                row_items.append(item)

            self.model.appendRow(row_items)

    def _get_artist_name(self, track) -> str:
        """Resolve the primary artist name for a track."""
        if hasattr(track, "primary_artist_names"):
            return track.primary_artist_names
        if track.artist_roles:
            artist = track.artist_roles[0].artist
            return artist.artist_name if artist else "Unknown Artist"
        return "Unknown Artist"

    def _format_value(self, value, db_field: str, field_config) -> str:
        """Format a field value for display."""
        if value is None:
            return ""
        if db_field == "duration" and isinstance(value, int):
            try:
                return f"{value // 60}:{value % 60:02d}"
            except (TypeError, ValueError):
                return "0:00"
        if db_field == "sample_rate" and isinstance(value, int):
            return f"{value / 1000:,.1f} kHz"
        if db_field == "file_size" and isinstance(value, int):
            return f"{value / (1024 * 1024):.2f} MB"
        return str(value)

    # ──────────────────────────────────────────────────────────────────────
    #  Search / filter
    # ──────────────────────────────────────────────────────────────────────

    def filter_tracks(self, text: str):
        """Filter the track table by search text."""
        self.proxy_model.setFilterRegularExpression(
            QRegularExpression(text, QRegularExpression.CaseInsensitiveOption)
        )

    # ──────────────────────────────────────────────────────────────────────
    #  Context menu
    # ──────────────────────────────────────────────────────────────────────

    def show_context_menu(self, pos):
        """Show context menu for track operations."""
        selected = self.table.selectionModel().selectedRows()
        if not selected:
            return

        # Resolve track IDs from model
        track_ids = []
        for index in selected:
            source_index = self.proxy_model.mapToSource(index)
            col = list(self.columns.keys()).index("track_id")
            item = self.model.item(source_index.row(), col)
            if item:
                track_ids.append(item.text())

        menu = QMenu(self)
        count = len(selected)

        # ── Edit ──────────────────────────────────────────────────────────
        edit_label = "Edit Track" if count == 1 else f"Edit {count} Tracks"
        menu.addAction(edit_label, self.edit_selected_track)
        menu.addSeparator()

        # ── Queue ─────────────────────────────────────────────────────────
        add_queue_action = QAction("Add to Queue", menu)
        add_queue_action.triggered.connect(self.add_selected_to_queue)
        menu.addAction(add_queue_action)

        add_queue_next_action = QAction("Add to Queue (Next)", menu)
        add_queue_next_action.triggered.connect(
            lambda: self.add_selected_to_queue(insert_next=True)
        )
        menu.addAction(add_queue_next_action)
        menu.addSeparator()

        # ── Playlist submenu ──────────────────────────────────────────────
        add_to_playlist_menu = menu.addMenu("Add to Playlist")
        self._populate_playlist_submenu(add_to_playlist_menu, track_ids)

        # ── Mood submenu ──────────────────────────────────────────────────
        add_to_mood_menu = menu.addMenu("Add to Mood")
        self._populate_mood_submenu(add_to_mood_menu, track_ids)

        menu.exec_(self.table.viewport().mapToGlobal(pos))

    def _populate_playlist_submenu(self, submenu: QMenu, track_ids: list):
        """Fill the Add to Playlist submenu."""
        try:
            playlists = self.controller.get.get_all_entities("Playlist")
            if not playlists:
                submenu.addAction("No playlists available").setEnabled(False)
                return
            for playlist in playlists:
                action = submenu.addAction(playlist.playlist_name)
                action.setData((playlist.playlist_id, track_ids))
                action.triggered.connect(self.add_to_playlist)
        except Exception as e:
            logger.error(f"Error loading playlists for context menu: {e}")
            submenu.addAction("Error loading playlists").setEnabled(False)

    def _populate_mood_submenu(self, submenu: QMenu, track_ids: list):
        """Fill the Add to Mood submenu."""
        try:
            moods = self.controller.get.get_all_entities("Mood")
            if not moods:
                submenu.addAction("No moods available").setEnabled(False)
                return
            for mood in moods:
                action = submenu.addAction(mood.mood_name)
                action.setData((mood.mood_id, track_ids))
                action.triggered.connect(self.add_to_mood)
        except Exception as e:
            logger.error(f"Error loading moods for context menu: {e}")
            submenu.addAction("Error loading moods").setEnabled(False)

    # ──────────────────────────────────────────────────────────────────────
    #  Queue
    # ──────────────────────────────────────────────────────────────────────

    def _get_queue_manager(self):
        """Return the queue manager from the controller, or None."""
        if hasattr(self.controller, "queue_manager"):
            return self.controller.queue_manager
        if hasattr(self.controller, "mediaplayer") and hasattr(
            self.controller.mediaplayer, "queue_manager"
        ):
            return self.controller.mediaplayer.queue_manager
        logger.warning("Queue manager not found in controller")
        return None

    def _get_selected_track_objects(self):
        """Return a list of track objects for the currently selected rows."""
        selected = self.table.selectionModel().selectedRows()
        tracks = []
        col_keys = list(self.columns.keys())
        track_id_col = col_keys.index("track_id")

        for index in selected:
            source_index = self.proxy_model.mapToSource(index)
            item = self.model.item(source_index.row(), track_id_col)
            if not item:
                continue
            try:
                track = self.controller.get.get_entity_object(
                    "Track", track_id=int(item.text())
                )
                if track:
                    tracks.append(track)
            except Exception as e:
                logger.error(f"Error fetching track object for queue: {e}")

        return tracks

    def add_selected_to_queue(self, insert_next: bool = False):
        """Add currently selected tracks to the playback queue."""
        queue_manager = self._get_queue_manager()
        if not queue_manager:
            return

        tracks = self._get_selected_track_objects()
        if not tracks:
            return

        if insert_next and hasattr(queue_manager, "insert_tracks_next"):
            queue_manager.insert_tracks_next(tracks)
        else:
            queue_manager.add_tracks_to_queue(tracks)

        logger.info(
            f"Added {len(tracks)} track(s) to queue (insert_next={insert_next})"
        )

    # ──────────────────────────────────────────────────────────────────────
    #  Playlist / mood actions
    # ──────────────────────────────────────────────────────────────────────

    def add_to_playlist(self):
        """Handle adding selected tracks to a playlist from the context menu."""
        action = self.sender()
        if not action:
            return

        playlist_id, track_ids = action.data()
        success_count = 0

        try:
            existing_tracks = self.controller.get.get_entity_links(
                "PlaylistTracks", playlist_id=playlist_id
            )
            next_position = max((t.position for t in existing_tracks), default=0) + 1

            for track_id in track_ids:
                if self.controller.add.add_entity_link(
                    "PlaylistTracks",
                    playlist_id=playlist_id,
                    track_id=track_id,
                    position=next_position,
                ):
                    success_count += 1
                    next_position += 1

            self._show_batch_result(
                success_count,
                len(track_ids),
                "playlist",
                already_present_possible=False,
            )
        except Exception as e:
            logger.error(f"Error adding tracks to playlist: {e}")
            QMessageBox.critical(
                self, "Error", f"Failed to add tracks to playlist:\n{e}"
            )

    def add_to_mood(self):
        """Handle adding selected tracks to a mood from the context menu."""
        action = self.sender()
        if not action:
            return

        mood_id, track_ids = action.data()
        success_count = 0
        skipped = 0

        try:
            for track_id in track_ids:
                existing = self.controller.get.get_entity_links(
                    "MoodTrackAssociation", mood_id=mood_id, track_id=track_id
                )
                if not existing:
                    if self.controller.add.add_entity_link(
                        "MoodTrackAssociation",
                        mood_id=mood_id,
                        track_id=track_id,
                    ):
                        success_count += 1
                else:
                    skipped += 1

            self._show_batch_result(
                success_count,
                len(track_ids),
                "mood",
                already_present_possible=skipped > 0,
            )
        except Exception as e:
            logger.error(f"Error adding tracks to mood: {e}")
            QMessageBox.critical(self, "Error", f"Failed to add tracks to mood:\n{e}")

    def _show_batch_result(
        self,
        success: int,
        total: int,
        destination: str,
        already_present_possible: bool = False,
    ):
        """Display a consistent result dialog for batch add operations."""
        if success == total:
            QMessageBox.information(
                self,
                "Success",
                f"All {success} track(s) added to {destination} successfully!",
            )
        elif success > 0:
            note = " (some may already be present)" if already_present_possible else ""
            QMessageBox.warning(
                self,
                "Partial Success",
                f"{success} of {total} track(s) added to {destination}{note}.",
            )
        else:
            msg = (
                "No tracks were added — all selected tracks may already be in this "
                f"{destination}."
                if already_present_possible
                else f"No tracks were added to {destination}."
            )
            QMessageBox.warning(self, "Nothing Added", msg)

    # ──────────────────────────────────────────────────────────────────────
    #  Playback
    # ──────────────────────────────────────────────────────────────────────

    def on_double_clicked(self, index):
        """Play the double-clicked track."""
        source_index = self.proxy_model.mapToSource(index)
        row = source_index.row()
        logger.debug(f"Double-clicked: row={row}, isValid={source_index.isValid()}")

        try:
            file_path_col = list(self.columns.keys()).index("track_file_path")
            file_path = self.model.item(row, file_path_col).text()
            logger.info(f"Attempting to play track: {file_path}")

            track_path = Path(file_path)
            if file_path and self.controller.mediaplayer.load_track(track_path):
                self.player.play()
            else:
                logger.warning(f"Failed to load track: {file_path}")
        except Exception as e:
            logger.error(f"Error playing track: {e}")

    # ──────────────────────────────────────────────────────────────────────
    #  Track editing
    # ──────────────────────────────────────────────────────────────────────

    def edit_selected_track(self):
        """Open edit dialog for selected track(s)."""
        selected = self.table.selectionModel().selectedRows()
        if not selected:
            return

        track_id_col = list(self.columns.keys()).index("track_id")
        track_ids = []
        for index in selected:
            source_index = self.proxy_model.mapToSource(index)
            item = self.model.item(source_index.row(), track_id_col)
            if item:
                try:
                    track_ids.append(int(item.text()))
                except ValueError:
                    pass

        if not track_ids:
            return

        if len(track_ids) == 1:
            try:
                track = self.controller.get.get_entity_object(
                    "Track", track_id=track_ids[0]
                )
                if track:
                    dialog = TrackEditDialog(self.controller, track, self)
                    if dialog.exec_() == QDialog.Accepted:
                        self.load_tracks_on_startup()
            except Exception as e:
                logger.error(f"Error opening track edit dialog: {e}")
        else:
            try:
                tracks = [
                    self.controller.get.get_entity_object("Track", track_id=tid)
                    for tid in track_ids
                ]
                tracks = [t for t in tracks if t]
                if tracks:
                    dialog = MultiTrackEditDialog(self.controller, tracks, self)
                    if dialog.exec_() == QDialog.Accepted:
                        self.load_tracks_on_startup()
            except Exception as e:
                logger.error(f"Error opening multi-track edit dialog: {e}")

    # ──────────────────────────────────────────────────────────────────────
    #  Column management
    # ──────────────────────────────────────────────────────────────────────

    def show_column_menu(self):
        """Show a checkable popup menu to toggle column visibility."""
        menu = QMenu(self)
        categories: dict[str, list] = {}

        for i, (field_name, display_name) in enumerate(self.columns.items()):
            field_config = self.track_fields.get(field_name)
            category = field_config.category if field_config else "General"
            categories.setdefault(category, []).append(
                (i, field_name, display_name, field_config)
            )

        for category_name, cols in categories.items():
            parent_menu = menu.addMenu(category_name) if len(categories) > 1 else menu
            for i, field_name, display_name, field_config in cols:
                action = parent_menu.addAction(display_name)
                action.setCheckable(True)
                action.setChecked(not self.table.isColumnHidden(i))
                if field_config and field_config.tooltip:
                    action.setToolTip(field_config.tooltip)
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

    def _toggle_column_visibility(self, column_index: int, hide: bool):
        """Toggle a column's visibility and persist state."""
        self.table.setColumnHidden(column_index, hide)
        self.save_column_state()

    def show_column_customization(self):
        """Show the column order/visibility customization dialog."""
        dialog = ColumnCustomizationDialog(self, self)
        dialog.exec_()

    def save_column_state(self):
        """Persist column visibility, order, and widths to config."""
        try:
            header = self.table.horizontalHeader()
            column_keys = list(self.columns.keys())

            # Build a list sorted by current visual position
            columns_by_visual_order = sorted(
                range(self.model.columnCount()),
                key=lambda logical: header.visualIndex(logical),
            )

            visible_columns = []
            column_order = []
            column_widths_map = {}

            for logical_index in columns_by_visual_order:
                field_name = column_keys[logical_index]
                column_order.append(field_name)
                column_widths_map[field_name] = self.table.columnWidth(logical_index)
                if not self.table.isColumnHidden(logical_index):
                    visible_columns.append(field_name)

            column_widths = [column_widths_map[f] for f in column_order]

            app_config.set_track_view_visible_columns(visible_columns)
            app_config.set_track_view_column_order(column_order)
            app_config.set_track_view_column_widths(column_widths)
            app_config.save()
            logger.info("Track view column state saved")
        except Exception as e:
            logger.error(f"Error saving column state: {e}")

    def load_column_state(self):
        """Restore column visibility, order, and widths from config."""
        try:
            visible_columns = app_config.get_track_view_visible_columns()
            column_order = app_config.get_track_view_column_order()  # ADD
            column_widths = app_config.get_track_view_column_widths()
            column_keys = list(self.columns.keys())

            if column_order:  # ADD
                self._reorder_columns(column_order)  # ADD

            if visible_columns:
                for i, field_name in enumerate(column_keys):
                    self.table.setColumnHidden(i, field_name not in visible_columns)

            if column_widths and len(column_widths) == len(column_keys):
                for i, width in enumerate(column_widths):
                    if width > 0:
                        self.table.setColumnWidth(i, width)
        except Exception as e:
            logger.error(f"Error loading column state: {e}")

    def get_column_state(self) -> dict:
        """Return a snapshot of current column visibility, order, and widths."""
        state = {"visible": [], "order": [], "widths": [], "positions": {}}

        for i in range(self.model.columnCount()):
            field_name = list(self.columns.keys())[i]
            visual_index = self.table.horizontalHeader().visualIndex(i)
            state["order"].append(field_name)
            state["widths"].append(self.table.columnWidth(i))
            state["positions"][field_name] = visual_index
            if not self.table.isColumnHidden(i):
                state["visible"].append(field_name)

        state["order"] = sorted(state["order"], key=lambda x: state["positions"][x])
        return state

    def _reorder_columns(self, ordered_field_names: list):
        """Move columns into the visual order specified by ordered_field_names."""
        header = self.table.horizontalHeader()
        column_keys = list(self.columns.keys())

        for desired_visual_index, field_name in enumerate(ordered_field_names):
            if field_name not in column_keys:
                continue
            logical_index = column_keys.index(field_name)
            current_visual_index = header.visualIndex(logical_index)
            if current_visual_index != desired_visual_index:
                header.moveSection(current_visual_index, desired_visual_index)

    # ──────────────────────────────────────────────────────────────────────
    #  Drag support
    # ──────────────────────────────────────────────────────────────────────

    def startDrag(self, supported_actions):
        """Build a drag payload containing selected track file paths."""
        selected = self.table.selectionModel().selectedRows()
        if not selected:
            return

        try:
            file_path_col = list(self.columns.keys()).index("track_file_path")
        except ValueError:
            return

        paths = []
        for index in selected:
            source_index = self.proxy_model.mapToSource(index)
            item = self.model.item(source_index.row(), file_path_col)
            if item and item.text():
                paths.append(item.text())

        if not paths:
            return

        mime = QMimeData()
        mime.setData("application/x-track-paths", QByteArray("\n".join(paths).encode()))

        drag = QDrag(self.table)
        drag.setMimeData(mime)

        # Build a simple drag pixmap showing the count
        pixmap = QPixmap(200, 30)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.drawText(pixmap.rect(), Qt.AlignCenter, f"{len(paths)} track(s)")
        painter.end()
        drag.setPixmap(pixmap)

        drag.exec_(supported_actions)
