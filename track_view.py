"""
track_view.py — TrackView
Main library track listing with lazy loading for large collections.

Key changes vs the original:
  - Lazy loading: only 200 rows are in the Qt model at any time.  As the user
    scrolls near the bottom another 200 are appended.  This makes the initial
    load instant even with 200 000 tracks.
  - The full track list (all ORM objects) is held in self._all_tracks so that
    "Add All" / "Shuffle All" operations still work without another DB round-trip.
  - Four new toolbar buttons:
      • Add All to Queue      — adds all tracks in the current filter to the queue
      • Shuffle All to Queue  — same, but shuffled
      • Add Library to Queue  — adds every track in the whole library
      • Shuffle Library       — same, but shuffled
"""

import random
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
    QLabel,
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

# How many rows to load into the Qt model in each batch.
LAZY_BATCH_SIZE = 200


class TrackView(QWidget):
    """
    Main library track view with lazy loading.

    self._all_tracks  — the complete list returned by the DB (held in Python,
                        NOT pushed into Qt — cheap to hold as ORM objects).
    self._loaded_count — how many of _all_tracks have been pushed into the model.
    self._filtered_ids — when a search filter is active this holds the track_ids
                         of the filtered subset so the "Add Filtered" buttons work.
    """

    def __init__(self, controller, music_player):
        super().__init__()
        self.controller = controller
        self.player = music_player
        self.track_fields = TRACK_FIELDS

        # Full track list from the DB (lightweight ORM objects, not audio data)
        self._all_tracks: list = []
        self._loaded_count: int = 0  # rows currently in the Qt model
        self._filter_active: bool = False

        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(4, 4, 4, 4)
        self.layout.setSpacing(4)

        # ── Toolbar row 1: search + column buttons ────────────────────────
        self.search_bar = QLineEdit(self)
        self.search_bar.setPlaceholderText("Search tracks…")
        self.search_bar.textChanged.connect(self._on_search_changed)

        self.column_toggle_button = QPushButton("Toggle Columns", self)
        self.column_toggle_button.clicked.connect(self.show_column_menu)

        self.customize_columns_button = QPushButton("Column Order", self)
        self.customize_columns_button.clicked.connect(self.show_column_customization)

        self.refresh_button = QPushButton("Refresh", self)
        self.refresh_button.clicked.connect(self.load_tracks_on_startup)

        search_layout = QHBoxLayout()
        search_layout.addWidget(self.search_bar, stretch=1)
        search_layout.addWidget(self.column_toggle_button)
        search_layout.addWidget(self.customize_columns_button)
        search_layout.addWidget(self.refresh_button)
        self.layout.addLayout(search_layout)

        # ── Toolbar row 2: queue action buttons ───────────────────────────
        # "Filtered" buttons act on whatever the search box is currently showing.
        # "Library" buttons always act on every track in the whole library.
        self.btn_add_filtered = QPushButton("➕ Add Filtered to Queue")
        self.btn_add_filtered.setToolTip(
            "Add all tracks matching the current search to the queue"
        )
        self.btn_add_filtered.clicked.connect(self._add_filtered_to_queue)

        self.btn_shuffle_filtered = QPushButton("🔀 Shuffle Filtered to Queue")
        self.btn_shuffle_filtered.setToolTip(
            "Add all matching tracks to the queue in random order"
        )
        self.btn_shuffle_filtered.clicked.connect(
            lambda: self._add_filtered_to_queue(shuffle=True)
        )

        self.btn_add_all = QPushButton("➕ Add Library to Queue")
        self.btn_add_all.setToolTip("Add every track in your library to the queue")
        self.btn_add_all.clicked.connect(self._add_all_to_queue)

        self.btn_shuffle_all = QPushButton("🔀 Shuffle Library")
        self.btn_shuffle_all.setToolTip(
            "Add every track in your library to the queue in random order"
        )
        self.btn_shuffle_all.clicked.connect(
            lambda: self._add_all_to_queue(shuffle=True)
        )

        # Status label shows e.g. "Showing 200 / 52 341 tracks"
        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: grey; font-size: 11px;")

        queue_layout = QHBoxLayout()
        queue_layout.addWidget(self.btn_add_filtered)
        queue_layout.addWidget(self.btn_shuffle_filtered)
        queue_layout.addWidget(self.btn_add_all)
        queue_layout.addWidget(self.btn_shuffle_all)
        queue_layout.addStretch()
        queue_layout.addWidget(self.status_label)
        self.layout.addLayout(queue_layout)

        # ── Table setup ───────────────────────────────────────────────────
        self._initialize_columns()

        self.table = QTableView(self)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.show_context_menu)

        self.model = QStandardItemModel()
        self.proxy_model = QSortFilterProxyModel(self)
        self.proxy_model.setSourceModel(self.model)
        self.proxy_model.setFilterCaseSensitivity(Qt.CaseInsensitive)
        self.proxy_model.setFilterRegularExpression(QRegularExpression())
        self.proxy_model.setFilterKeyColumn(-1)
        self.proxy_model.setSortRole(Qt.UserRole)
        self.table.setModel(self.proxy_model)

        self._setup_table()
        self.load_column_state()

        self.layout.addWidget(self.table)

        # Connect double-click
        self.table.doubleClicked.connect(self.on_double_clicked)

        # Detect when the user scrolls near the bottom so we can load more rows
        self.table.verticalScrollBar().valueChanged.connect(self._on_scroll)

        # Load tracks on startup
        self.load_tracks_on_startup()

    # =========================================================================
    #  Columns
    # =========================================================================

    def _initialize_columns(self):
        self.columns = {}
        for field_name, field_config in self.track_fields.items():
            if field_config.friendly:
                self.columns[field_name] = field_config.friendly

    def _setup_table(self):
        self.model.setColumnCount(len(self.columns))
        self.model.setHorizontalHeaderLabels(list(self.columns.values()))

        self.table.setSortingEnabled(True)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.setSelectionBehavior(QTableView.SelectRows)
        self.table.setSelectionMode(QTableView.ExtendedSelection)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableView.NoEditTriggers)

        self.table.setDragEnabled(True)
        self.table.setDragDropMode(QTableView.DragOnly)
        self.table.setDefaultDropAction(Qt.CopyAction)
        self.table.startDrag = self.startDrag

        self._set_initial_column_visibility()

    def _set_initial_column_visibility(self):
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

    # =========================================================================
    #  Data loading — lazy
    # =========================================================================

    def load_tracks_on_startup(self):
        """Fetch all track objects from the DB then display the first batch."""
        try:
            tracks = self.controller.get.get_all_entities("Track")
            self._all_tracks = tracks or []
            self._loaded_count = 0
            self._filter_active = False
            self.model.setRowCount(0)
            self._append_next_batch(self._all_tracks)
            self._update_status()
            logger.info(
                f"Loaded {len(self._all_tracks)} tracks (lazy, first batch shown)."
            )
        except Exception as e:
            logger.error(f"Error loading tracks on startup: {e}")

    def _append_next_batch(self, source_list: list):
        """
        Push the next LAZY_BATCH_SIZE rows from source_list into the Qt model.
        source_list should already be sliced/filtered as needed.
        """
        start = self._loaded_count
        end = min(start + LAZY_BATCH_SIZE, len(source_list))
        if start >= end:
            return

        column_keys = list(self.columns.keys())
        for track in source_list[start:end]:
            row_items = []
            for field_name in column_keys:
                field_config = self.track_fields.get(field_name)
                value = getattr(track, field_name, None)

                if field_name == "artist_name":
                    value = self._get_artist_name(track)

                display_value = self._format_value(value, field_name, field_config)
                item = QStandardItem(display_value)
                item.setEditable(False)

                if isinstance(value, (int, float)):
                    item.setData(value, Qt.UserRole)
                else:
                    item.setData(display_value, Qt.UserRole)

                row_items.append(item)

            self.model.appendRow(row_items)

        self._loaded_count = end
        self._update_status()

    def _on_scroll(self, value: int):
        """Load the next batch when the user scrolls near the bottom."""
        if self._filter_active:
            return  # The proxy model filters a full set — no lazy needed

        scrollbar = self.table.verticalScrollBar()
        # Load more when we're within 10 % of the bottom
        if scrollbar.maximum() > 0 and value >= scrollbar.maximum() * 0.90:
            if self._loaded_count < len(self._all_tracks):
                self._append_next_batch(self._all_tracks)

    def _update_status(self):
        total = len(self._all_tracks)
        visible = (
            self._loaded_count
            if not self._filter_active
            else self.proxy_model.rowCount()
        )
        self.status_label.setText(f"Showing {visible:,} / {total:,} tracks")

    # =========================================================================
    #  Search / filter
    # =========================================================================

    def _on_search_changed(self, text: str):
        """
        When a search is active we load ALL tracks into the model so the proxy
        can filter across them.  When the search is cleared we go back to lazy mode.
        """
        if text.strip():
            # Ensure all tracks are in the model for complete filtering
            if self._loaded_count < len(self._all_tracks):
                self._fill_model_completely()
            self._filter_active = True
        else:
            self._filter_active = False

        self.proxy_model.setFilterRegularExpression(
            QRegularExpression(text, QRegularExpression.CaseInsensitiveOption)
        )
        self._update_status()

    def _fill_model_completely(self):
        """Push every track into the model (needed for search to work correctly)."""
        while self._loaded_count < len(self._all_tracks):
            self._append_next_batch(self._all_tracks)

    def filter_tracks(self, text: str):
        """Public alias kept for compatibility with external callers."""
        self._on_search_changed(text)

    # =========================================================================
    #  Queue action buttons
    # =========================================================================

    def _get_queue_manager(self):
        if hasattr(self.controller, "queue_manager"):
            return self.controller.queue_manager
        if hasattr(self.controller, "mediaplayer") and hasattr(
            self.controller.mediaplayer, "queue_manager"
        ):
            return self.controller.mediaplayer.queue_manager
        logger.warning("Queue manager not found in controller")
        return None

    def _add_all_to_queue(self, shuffle: bool = False):
        """Add every track in the library to the queue (optionally shuffled)."""
        qm = self._get_queue_manager()
        if not qm:
            return

        tracks = list(self._all_tracks)  # copy so we don't mutate the master list
        if shuffle:
            random.shuffle(tracks)

        qm.add_tracks_to_queue(tracks)
        verb = "Shuffled" if shuffle else "Added"
        logger.info(f"{verb} entire library ({len(tracks)} tracks) to queue")
        QMessageBox.information(
            self,
            "Queue Updated",
            f"{verb} {len(tracks):,} tracks to the queue.",
        )

    def _add_filtered_to_queue(self, shuffle: bool = False):
        """
        Add all tracks that are currently visible (after search filter) to the queue.
        If no search is active this behaves identically to _add_all_to_queue.
        """
        qm = self._get_queue_manager()
        if not qm:
            return

        search_text = self.search_bar.text().strip()
        if not search_text:
            # No filter — same as "add all"
            self._add_all_to_queue(shuffle=shuffle)
            return

        # Make sure the model is fully populated so the proxy has everything to filter
        if self._loaded_count < len(self._all_tracks):
            self._fill_model_completely()

        # Collect tracks that survive the current proxy filter
        column_keys = list(self.columns.keys())
        try:
            track_id_col = column_keys.index("track_id")
        except ValueError:
            logger.error("track_id column not found — cannot collect filtered tracks")
            return

        filtered_track_ids = set()
        for proxy_row in range(self.proxy_model.rowCount()):
            source_index = self.proxy_model.mapToSource(
                self.proxy_model.index(proxy_row, track_id_col)
            )
            item = self.model.item(source_index.row(), track_id_col)
            if item:
                try:
                    filtered_track_ids.add(int(item.text()))
                except ValueError:
                    pass

        # Build ordered track list preserving original order
        tracks = [t for t in self._all_tracks if t.track_id in filtered_track_ids]
        if shuffle:
            random.shuffle(tracks)

        qm.add_tracks_to_queue(tracks)
        verb = "Shuffled" if shuffle else "Added"
        logger.info(f"{verb} {len(tracks)} filtered tracks to queue")
        QMessageBox.information(
            self,
            "Queue Updated",
            f"{verb} {len(tracks):,} matching tracks to the queue.",
        )

    # =========================================================================
    #  Selected-track queue (context menu)
    # =========================================================================

    def _get_selected_track_objects(self):
        """Return track objects for all selected rows."""
        selected = self.table.selectionModel().selectedRows()
        col_keys = list(self.columns.keys())
        track_id_col = col_keys.index("track_id")
        tracks = []

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
                logger.error(f"Error fetching track for queue: {e}")

        return tracks

    def add_selected_to_queue(self, insert_next: bool = False):
        """Add currently selected tracks to the playback queue."""
        qm = self._get_queue_manager()
        if not qm:
            return

        tracks = self._get_selected_track_objects()
        if not tracks:
            return

        if insert_next and hasattr(qm, "insert_tracks_next"):
            qm.insert_tracks_next(tracks)
        else:
            qm.add_tracks_to_queue(tracks)

        logger.info(
            f"Added {len(tracks)} track(s) to queue (insert_next={insert_next})"
        )

    # =========================================================================
    #  Playback
    # =========================================================================

    def on_double_clicked(self, index):
        """Play the double-clicked track."""
        source_index = self.proxy_model.mapToSource(index)
        row = source_index.row()
        try:
            file_path_col = list(self.columns.keys()).index("track_file_path")
            file_path = self.model.item(row, file_path_col).text()
            track_path = Path(file_path)
            if file_path and self.controller.mediaplayer.load_track(track_path):
                self.player.play()
            else:
                logger.warning(f"Failed to load track: {file_path}")
        except Exception as e:
            logger.error(f"Error playing track: {e}")

    # =========================================================================
    #  Track editing
    # =========================================================================

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

    # =========================================================================
    #  Context menu
    # =========================================================================

    def show_context_menu(self, pos):
        """Show context menu for track operations."""
        selected = self.table.selectionModel().selectedRows()
        if not selected:
            return

        track_id_col = list(self.columns.keys()).index("track_id")
        track_ids = []
        for index in selected:
            source_index = self.proxy_model.mapToSource(index)
            item = self.model.item(source_index.row(), track_id_col)
            if item:
                track_ids.append(item.text())

        menu = QMenu(self)
        count = len(selected)

        edit_label = "Edit Track" if count == 1 else f"Edit {count} Tracks"
        menu.addAction(edit_label, self.edit_selected_track)
        menu.addSeparator()

        add_queue_action = QAction("Add to Queue", menu)
        add_queue_action.triggered.connect(self.add_selected_to_queue)
        menu.addAction(add_queue_action)

        add_queue_next_action = QAction("Add to Queue (Next)", menu)
        add_queue_next_action.triggered.connect(
            lambda: self.add_selected_to_queue(insert_next=True)
        )
        menu.addAction(add_queue_next_action)
        menu.addSeparator()

        add_to_playlist_menu = menu.addMenu("Add to Playlist")
        self._populate_playlist_submenu(add_to_playlist_menu, track_ids)

        add_to_mood_menu = menu.addMenu("Add to Mood")
        self._populate_mood_submenu(add_to_mood_menu, track_ids)

        menu.exec_(self.table.viewport().mapToGlobal(pos))

    def _populate_playlist_submenu(self, submenu: QMenu, track_ids: list):
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

    def add_to_playlist(self):
        action = self.sender()
        if not action:
            return
        playlist_id, track_ids = action.data()
        success_count = 0
        try:
            existing = self.controller.get.get_entity_links(
                "PlaylistTracks", playlist_id=playlist_id
            )
            next_position = max((t.position for t in existing), default=0) + 1
            for track_id in track_ids:
                if self.controller.add.add_entity_link(
                    "PlaylistTracks",
                    playlist_id=playlist_id,
                    track_id=track_id,
                    position=next_position,
                ):
                    success_count += 1
                    next_position += 1
            self._show_batch_result(success_count, len(track_ids), "playlist")
        except Exception as e:
            logger.error(f"Error adding tracks to playlist: {e}")
            QMessageBox.critical(
                self, "Error", f"Failed to add tracks to playlist:\n{e}"
            )

    def add_to_mood(self):
        action = self.sender()
        if not action:
            return
        mood_id, track_ids = action.data()
        success_count = 0
        try:
            for track_id in track_ids:
                if self.controller.add.add_entity_link(
                    "MoodTracks", mood_id=mood_id, track_id=track_id
                ):
                    success_count += 1
            self._show_batch_result(success_count, len(track_ids), "mood")
        except Exception as e:
            logger.error(f"Error adding tracks to mood: {e}")
            QMessageBox.critical(self, "Error", f"Failed to add tracks to mood:\n{e}")

    def _show_batch_result(
        self,
        success_count: int,
        total: int,
        destination: str,
        already_present_possible: bool = True,
    ):
        if success_count == total:
            QMessageBox.information(
                self, "Success", f"Added {success_count} track(s) to {destination}."
            )
        elif success_count > 0:
            msg = (
                f"Added {success_count} of {total} track(s). "
                f"Some may already be in the {destination}."
                if already_present_possible
                else f"Added {success_count} of {total} track(s) to {destination}."
            )
            QMessageBox.warning(self, "Partial Success", msg)
        else:
            msg = (
                f"No tracks were added. They may already be in the {destination}."
                if already_present_possible
                else f"No tracks were added to {destination}."
            )
            QMessageBox.warning(self, "Nothing Added", msg)

    # =========================================================================
    #  Column management
    # =========================================================================

    def show_column_menu(self):
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
        self.table.setColumnHidden(column_index, hide)
        self.save_column_state()

    def show_column_customization(self):
        dialog = ColumnCustomizationDialog(self, self)
        dialog.exec_()

    def save_column_state(self):
        try:
            header = self.table.horizontalHeader()
            column_keys = list(self.columns.keys())

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
        except Exception as e:
            logger.error(f"Error saving column state: {e}")

    def load_column_state(self):
        try:
            visible_columns = app_config.get_track_view_visible_columns()
            column_order = app_config.get_track_view_column_order()
            column_widths = app_config.get_track_view_column_widths()
            column_keys = list(self.columns.keys())

            if column_order:
                self._reorder_columns(column_order)

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
        header = self.table.horizontalHeader()
        column_keys = list(self.columns.keys())
        for desired_visual_index, field_name in enumerate(ordered_field_names):
            if field_name not in column_keys:
                continue
            logical_index = column_keys.index(field_name)
            current_visual_index = header.visualIndex(logical_index)
            if current_visual_index != desired_visual_index:
                header.moveSection(current_visual_index, desired_visual_index)

    # =========================================================================
    #  Drag support
    # =========================================================================

    def startDrag(self, supported_actions):
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

        pixmap = QPixmap(200, 30)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.drawText(pixmap.rect(), Qt.AlignCenter, f"{len(paths)} track(s)")
        painter.end()
        drag.setPixmap(pixmap)

        drag.exec_(supported_actions)

    # =========================================================================
    #  Helpers
    # =========================================================================

    def _get_artist_name(self, track) -> str:
        if hasattr(track, "primary_artist_names"):
            return track.primary_artist_names
        if track.artist_roles:
            artist = track.artist_roles[0].artist
            return artist.artist_name if artist else "Unknown Artist"
        return "Unknown Artist"

    def _format_value(self, value, db_field: str, field_config) -> str:
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
