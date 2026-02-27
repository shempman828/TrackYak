"""
track_view.py — TrackView
"""

import random
from pathlib import Path

from PySide6.QtCore import QByteArray, QMimeData, Qt, QTimer
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
    QPushButton,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from src.config_setup import app_config
from src.db_mapping_tracks import TRACK_FIELDS
from src.logger_config import logger
from src.track_columns import ColumnCustomizationDialog
from src.track_edit import MultiTrackEditDialog, TrackEditDialog

# How many rows to load into the Qt model in each batch.
LAZY_BATCH_SIZE = 200

# Search debounce delay in ms — prevents filtering on every keystroke.
SEARCH_DEBOUNCE_MS = 300


class TrackView(QWidget):
    """
    Main library track view with lazy loading.

    self._all_tracks    — full track list from DB, loaded ONCE and cached.
    self._loaded_count  — rows currently pushed into the Qt model.
    self._filtered_tracks — active subset when a search filter is live.
    self._filter_active — True while a search filter is applied.
    """

    def __init__(self, controller, music_player):
        super().__init__()
        self.controller = controller
        self.player = music_player
        self.track_fields = TRACK_FIELDS
        self._filtered_tracks: list = []
        self._all_tracks: list = []
        self._loaded_count: int = 0
        self._filter_active: bool = False
        self._tracks_loaded: bool = False  # Guard against redundant DB calls

        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(4, 4, 4, 4)
        self.layout.setSpacing(4)

        # ── Toolbar row 1: search + column buttons ────────────────────────
        self.search_bar = QLineEdit(self)
        self.search_bar.setPlaceholderText("Search tracks…")

        # Debounce — only fire filter logic after user pauses typing
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(SEARCH_DEBOUNCE_MS)
        self._search_timer.timeout.connect(self._apply_search_filter)
        self.search_bar.textChanged.connect(self._search_timer.start)

        self.column_toggle_button = QPushButton("Toggle Columns", self)
        self.column_toggle_button.clicked.connect(self.show_column_menu)

        self.customize_columns_button = QPushButton("Column Order", self)
        self.customize_columns_button.clicked.connect(self.show_column_customization)

        self.refresh_button = QPushButton("Refresh", self)
        self.refresh_button.clicked.connect(self._force_reload)

        search_layout = QHBoxLayout()
        search_layout.addWidget(self.search_bar, stretch=1)
        search_layout.addWidget(self.column_toggle_button)
        search_layout.addWidget(self.customize_columns_button)
        search_layout.addWidget(self.refresh_button)
        self.layout.addLayout(search_layout)

        # ── Toolbar row 2: queue action buttons ───────────────────────────
        self.btn_add_filtered = QPushButton("➕ Add Filtered to Queue")
        self.btn_add_filtered.setToolTip(
            "Add all tracks matching the current search to the queue"
        )
        self.btn_add_filtered.clicked.connect(self._add_filtered_to_queue)

        self.btn_shuffle_filtered = QPushButton("🔀 Shuffle Filtered to Queue")
        self.btn_shuffle_filtered.setToolTip("Add all matching tracks in random order")
        self.btn_shuffle_filtered.clicked.connect(
            lambda: self._add_filtered_to_queue(shuffle=True)
        )

        self.btn_add_all = QPushButton("➕ Add Library to Queue")
        self.btn_add_all.setToolTip("Add every track in your library to the queue")
        self.btn_add_all.clicked.connect(self._add_all_to_queue)

        self.btn_shuffle_all = QPushButton("🔀 Shuffle Library")
        self.btn_shuffle_all.setToolTip("Add every track in random order")
        self.btn_shuffle_all.clicked.connect(
            lambda: self._add_all_to_queue(shuffle=True)
        )

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

        self.model = QStandardItemModel()
        self.table = QTableView(self)
        self.table.setModel(self.model)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.show_context_menu)

        self._setup_table()
        self.load_column_state()

        self.layout.addWidget(self.table)

        self.table.doubleClicked.connect(self.on_double_clicked)
        self.table.verticalScrollBar().valueChanged.connect(self._on_scroll)

        # Initial load
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
    #  Column state persistence
    # =========================================================================

    def load_column_state(self):
        try:
            visible = app_config.get_track_view_visible_columns()
            order = app_config.get_track_view_column_order()
            widths = app_config.get_track_view_column_widths()
            col_keys = list(self.columns.keys())

            if visible:
                for i, key in enumerate(col_keys):
                    self.table.setColumnHidden(i, key not in visible)

            if order:
                header = self.table.horizontalHeader()
                for target_visual, key in enumerate(order):
                    if key in col_keys:
                        current_visual = header.visualIndex(col_keys.index(key))
                        if current_visual != target_visual:
                            header.moveSection(current_visual, target_visual)

            if widths:
                for i, w in enumerate(widths):
                    if i < len(col_keys) and w > 0:
                        self.table.setColumnWidth(i, w)
        except Exception as e:
            logger.error(f"Error loading column state: {e}")

    def save_column_state(self):
        try:
            col_keys = list(self.columns.keys())
            header = self.table.horizontalHeader()
            visible = [
                k for i, k in enumerate(col_keys) if not self.table.isColumnHidden(i)
            ]
            order = [col_keys[header.logicalIndex(v)] for v in range(header.count())]
            widths = [self.table.columnWidth(i) for i in range(len(col_keys))]

            app_config.set_track_view_visible_columns(visible)
            app_config.set_track_view_column_order(order)
            app_config.set_track_view_column_widths(widths)
        except Exception as e:
            logger.error(f"Error saving column state: {e}")

    def show_column_menu(self):
        menu = QMenu(self)
        list(self.columns.keys())
        for i, (key, label) in enumerate(self.columns.items()):
            action = QAction(label, self)
            action.setCheckable(True)
            action.setChecked(not self.table.isColumnHidden(i))
            action.setData(i)
            action.triggered.connect(self._toggle_column)
            menu.addAction(action)
        menu.exec_(
            self.column_toggle_button.mapToGlobal(
                self.column_toggle_button.rect().bottomLeft()
            )
        )

    def _toggle_column(self):
        action = self.sender()
        if action:
            i = action.data()
            self.table.setColumnHidden(i, not action.isChecked())
            self.save_column_state()

    def show_column_customization(self):
        dialog = ColumnCustomizationDialog(self, self)
        dialog.exec_()

    # =========================================================================
    #  Data loading — lazy, single DB call
    # =========================================================================

    def load_tracks_on_startup(self):
        """
        Load all tracks from DB into self._all_tracks (once).
        Only pushes the first LAZY_BATCH_SIZE rows into the Qt model.
        Subsequent calls from search-clear reuse the cached list.
        """
        if not self._tracks_loaded:
            try:
                tracks = self.controller.get.get_all_entities("Track")
                self._all_tracks = tracks or []
                self._tracks_loaded = True
                logger.info(
                    f"Fetched {len(self._all_tracks):,} tracks from DB (one-time)."
                )
            except Exception as e:
                logger.error(f"Error fetching tracks: {e}")
                self._all_tracks = []

        self._filter_active = False
        self._filtered_tracks = []
        self._loaded_count = 0
        self.model.setRowCount(0)
        self._append_next_batch(self._all_tracks)
        self._update_status()

    def _force_reload(self):
        """Explicitly re-query the DB (Refresh button)."""
        self._tracks_loaded = False
        self.load_tracks_on_startup()

    def load_data(self, tracks: list):
        """
        External callers (e.g. main_window refresh) can push a new track list.
        This replaces the cache and resets the view.
        """
        self._all_tracks = tracks or []
        self._tracks_loaded = True
        self._filter_active = False
        self._filtered_tracks = []
        self._loaded_count = 0
        self.model.setRowCount(0)
        self._append_next_batch(self._all_tracks)
        self._update_status()

    def _append_next_batch(self, source_list: list):
        """Push the next LAZY_BATCH_SIZE rows from source_list into the Qt model."""
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
                item.setData(
                    value if isinstance(value, (int, float)) else display_value,
                    Qt.UserRole,
                )
                row_items.append(item)

            self.model.appendRow(row_items)

        self._loaded_count = end
        self._update_status()

    def _on_scroll(self, value: int):
        scrollbar = self.table.verticalScrollBar()
        if scrollbar.maximum() > 0 and value >= scrollbar.maximum() * 0.90:
            source = self._filtered_tracks if self._filter_active else self._all_tracks
            if self._loaded_count < len(source):
                self._append_next_batch(source)

    def _update_status(self):
        total = len(self._all_tracks)
        if self._filter_active:
            visible = len(self._filtered_tracks)
            self.status_label.setText(
                f"Showing {self._loaded_count:,} / {visible:,} matches  ({total:,} total)"
            )
        else:
            self.status_label.setText(
                f"Showing {self._loaded_count:,} / {total:,} tracks"
            )

    # =========================================================================
    #  Search / filter
    # =========================================================================

    def _apply_search_filter(self):
        """
        Called after debounce timer fires.
        Filters self._all_tracks in Python (fast) then reloads the model
        with just the matching subset.
        """
        search_text = self.search_bar.text().strip().lower()

        if not search_text:
            # Clear filter — reuse cached track list, no DB call
            self._filter_active = False
            self._filtered_tracks = []
            self._loaded_count = 0
            self.model.setRowCount(0)
            self._append_next_batch(self._all_tracks)
            self._update_status()
            return

        self._filter_active = True

        # Filter against: track_name, primary_artist_names, album name
        self._filtered_tracks = []
        for t in self._all_tracks:
            title = (getattr(t, "track_name", "") or "").lower()
            artist = (self._get_artist_name(t) or "").lower()
            album_obj = getattr(t, "album", None)
            album = (
                (getattr(album_obj, "album_name", "") or "").lower()
                if album_obj
                else ""
            )

            if search_text in title or search_text in artist or search_text in album:
                self._filtered_tracks.append(t)

        self._loaded_count = 0
        self.model.setRowCount(0)
        self._append_next_batch(self._filtered_tracks)
        self._update_status()
        logger.debug(f"Search '{search_text}' → {len(self._filtered_tracks):,} matches")

    def filter_tracks(self, text: str):
        """Public alias kept for compatibility with external callers."""
        self.search_bar.setText(text)

    # =========================================================================
    #  Artist name helper — uses primary_artist_names like player_dock
    # =========================================================================

    def _get_artist_name(self, track) -> str:
        """
        Return primary artist name(s) for the track.
        Uses track.primary_artist_names which walks TrackArtistRole with
        Role='Primary Artist' — the same data player_dock uses.
        Falls back to track.artists[0] for backward compatibility.
        """
        # Preferred: proper role-filtered property
        try:
            name = getattr(track, "primary_artist_names", None)
            if name:
                return name
        except Exception:
            pass

        # Fallback: first artist in the unfiltered artists proxy
        try:
            artists = getattr(track, "artists", None) or []
            if artists:
                return getattr(artists[0], "artist_name", "") or ""
        except Exception:
            pass

        return ""

    def _format_value(self, value, field_name: str, field_config) -> str:
        if value is None:
            return ""
        if field_name == "duration" and isinstance(value, (int, float)):
            total_s = int(value)
            m, s = divmod(total_s, 60)
            return f"{m}:{s:02d}"
        if field_name in ("file_size",) and isinstance(value, (int, float)):
            return f"{value / (1024 * 1024):.1f} MB"
        return str(value)

    # =========================================================================
    #  Drag support
    # =========================================================================

    def startDrag(self, supported_actions):
        selected = self.table.selectionModel().selectedRows()
        if not selected:
            return

        col_keys = list(self.columns.keys())
        try:
            track_id_col = col_keys.index("track_id")
        except ValueError:
            return

        track_ids = []
        for index in selected:
            item = self.model.item(index.row(), track_id_col)
            if item:
                try:
                    track_ids.append(int(item.text()))
                except ValueError:
                    pass

        if not track_ids:
            return

        mime = QMimeData()
        mime.setData(
            "application/x-track-id",
            QByteArray(",".join(str(i) for i in track_ids).encode()),
        )

        drag = QDrag(self)
        drag.setMimeData(mime)

        pixmap = QPixmap(200, 30)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setPen(Qt.white)
        painter.drawText(pixmap.rect(), Qt.AlignCenter, f"{len(track_ids)} track(s)")
        painter.end()
        drag.setPixmap(pixmap)
        drag.exec_(Qt.CopyAction)

    # =========================================================================
    #  Queue helpers
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
        qm = self._get_queue_manager()
        if not qm:
            return
        tracks = list(self._all_tracks)
        logger.info(
            f"{'Shuffling' if shuffle else 'Adding'} {len(tracks):,} tracks to queue"
        )
        if len(tracks) > 500:
            qm.add_tracks_async(tracks, shuffle=shuffle)
        else:
            if shuffle:
                random.shuffle(tracks)
            qm.add_tracks_to_queue(tracks)

    def _add_filtered_to_queue(self, shuffle: bool = False):
        qm = self._get_queue_manager()
        if not qm:
            return
        source = (
            self._filtered_tracks if self._filter_active else list(self._all_tracks)
        )
        tracks = list(source)
        if shuffle:
            random.shuffle(tracks)
        if len(tracks) > 500:
            qm.add_tracks_async(tracks, shuffle=False)  # already shuffled if needed
        else:
            qm.add_tracks_to_queue(tracks)
        logger.info(
            f"{'Shuffled' if shuffle else 'Added'} {len(tracks):,} filtered tracks to queue"
        )

    def _get_selected_track_objects(self) -> list:
        """Return Track ORM objects for all selected rows."""
        selected = self.table.selectionModel().selectedRows()
        col_keys = list(self.columns.keys())
        try:
            track_id_col = col_keys.index("track_id")
        except ValueError:
            return []

        tracks = []
        for index in selected:
            item = self.model.item(index.row(), track_id_col)
            if not item:
                continue
            try:
                track = self.controller.get.get_entity_object(
                    "Track", track_id=int(item.text())
                )
                if track:
                    tracks.append(track)
            except Exception as e:
                logger.error(f"Error fetching selected track: {e}")
        return tracks

    def add_selected_to_queue(self, insert_next: bool = False):
        qm = self._get_queue_manager()
        if not qm:
            return
        tracks = self._get_selected_track_objects()
        if not tracks:
            return
        if insert_next:
            qm.insert_tracks_next(tracks)
        else:
            qm.add_tracks_to_queue(tracks)
        logger.info(f"Added {len(tracks)} track(s) to queue (next={insert_next})")

    # =========================================================================
    #  Playback
    # =========================================================================

    def on_double_clicked(self, index):
        row = index.row()
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
        tracks = self._get_selected_track_objects()
        if not tracks:
            return
        if len(tracks) == 1:
            try:
                dialog = TrackEditDialog(tracks[0], self.controller, self)
                if dialog.exec_() == QDialog.Accepted:
                    self._force_reload()
            except Exception as e:
                logger.error(f"Error opening track edit dialog: {e}")
        else:
            try:
                dialog = MultiTrackEditDialog(tracks, self.controller, self)
                if dialog.exec_() == QDialog.Accepted:
                    self._force_reload()
            except Exception as e:
                logger.error(f"Error opening multi-track edit dialog: {e}")

    # =========================================================================
    #  Context menu — player_dock style with hierarchical submenus
    # =========================================================================

    def show_context_menu(self, pos):
        selected = self.table.selectionModel().selectedRows()
        if not selected:
            return

        tracks = self._get_selected_track_objects()
        if not tracks:
            return

        track_ids = [str(t.track_id) for t in tracks]
        count = len(tracks)

        menu = QMenu(self)

        # ── Playback ──────────────────────────────────────────────────────
        play_next_action = QAction("▶  Play Next", self)
        play_next_action.triggered.connect(
            lambda: self.add_selected_to_queue(insert_next=True)
        )
        menu.addAction(play_next_action)

        add_queue_action = QAction("➕  Add to Queue", self)
        add_queue_action.triggered.connect(
            lambda: self.add_selected_to_queue(insert_next=False)
        )
        menu.addAction(add_queue_action)

        menu.addSeparator()

        # ── Edit ──────────────────────────────────────────────────────────
        if count == 1:
            edit_action = QAction("✏️  Edit Track", self)
            edit_action.triggered.connect(self.edit_selected_track)
            menu.addAction(edit_action)
        else:
            edit_action = QAction(f"✏️  Edit {count} Tracks", self)
            edit_action.triggered.connect(self.edit_selected_track)
            menu.addAction(edit_action)

        menu.addSeparator()

        # ── Add to Playlist (hierarchical, alphabetical) ──────────────────
        playlist_menu = QMenu("📋  Add to Playlist", self)
        self._populate_playlist_submenu(playlist_menu, tracks, track_ids)
        menu.addMenu(playlist_menu)

        # ── Add to Mood (hierarchical, alphabetical) ──────────────────────
        mood_menu = QMenu("🎭  Add to Mood", self)
        self._populate_mood_submenu(mood_menu, tracks, track_ids)
        menu.addMenu(mood_menu)

        menu.exec_(self.table.mapToGlobal(pos))

    # ── Playlist submenu ──────────────────────────────────────────────────

    def _populate_playlist_submenu(self, submenu: QMenu, tracks: list, track_ids: list):
        try:
            playlists = self.controller.get.get_all_entities("Playlist") or []
            if not playlists:
                submenu.addAction("No playlists available").setEnabled(False)
                return

            # Which playlists contain ALL selected tracks?
            # (for checkmarks — only show checked if every track is a member)
            track_playlist_sets = []
            for track in tracks:
                pts = getattr(track, "playlists", []) or []
                track_playlist_sets.append({pt.playlist_id for pt in pts})
            all_playlist_ids = (
                track_playlist_sets[0].intersection(*track_playlist_sets[1:])
                if track_playlist_sets
                else set()
            )

            # Build hierarchy
            children_map: dict = {}
            for pl in playlists:
                pid = getattr(pl, "parent_id", None)
                children_map.setdefault(pid, []).append(pl)
            for pid in children_map:
                children_map[pid].sort(key=lambda x: x.playlist_name.lower())

            self._build_playlist_hierarchy(
                submenu, None, children_map, all_playlist_ids, track_ids
            )

        except Exception as e:
            logger.error(f"Error populating playlist submenu: {e}")
            submenu.addAction("Error loading playlists").setEnabled(False)

    def _build_playlist_hierarchy(
        self, parent_menu, parent_id, children_map, member_ids, track_ids, depth=0
    ):
        if depth > 8:
            return
        for pl in children_map.get(parent_id, []):
            has_children = bool(children_map.get(pl.playlist_id))
            if has_children:
                sub = QMenu(pl.playlist_name, parent_menu)
                self._build_playlist_hierarchy(
                    sub, pl.playlist_id, children_map, member_ids, track_ids, depth + 1
                )
                sub.addSeparator()
                act = QAction(f"Add to '{pl.playlist_name}'", sub)
                act.setData((pl.playlist_id, track_ids))
                if pl.playlist_id in member_ids:
                    act.setCheckable(True)
                    act.setChecked(True)
                act.triggered.connect(self.add_to_playlist)
                sub.addAction(act)
                parent_menu.addMenu(sub)
            else:
                act = QAction(pl.playlist_name, parent_menu)
                act.setData((pl.playlist_id, track_ids))
                if pl.playlist_id in member_ids:
                    act.setCheckable(True)
                    act.setChecked(True)
                act.triggered.connect(self.add_to_playlist)
                parent_menu.addAction(act)

    def add_to_playlist(self):
        action = self.sender()
        if not action:
            return
        playlist_id, track_ids = action.data()
        try:
            existing = self.controller.get.get_entity_links(
                "PlaylistTracks", playlist_id=playlist_id
            )
            next_position = max((t.position for t in existing), default=0) + 1
            added = 0
            for track_id in track_ids:
                already = self.controller.get.get_entity_links(
                    "PlaylistTracks", playlist_id=playlist_id, track_id=int(track_id)
                )
                if already:
                    continue
                ok = self.controller.add.add_entity_link(
                    "PlaylistTracks",
                    playlist_id=playlist_id,
                    track_id=int(track_id),
                    position=next_position,
                )
                if ok:
                    next_position += 1
                    added += 1
            logger.info(f"Added {added} track(s) to playlist {playlist_id}")
        except Exception as e:
            logger.error(f"Error adding tracks to playlist: {e}")

    # ── Mood submenu ──────────────────────────────────────────────────────

    def _populate_mood_submenu(self, submenu: QMenu, tracks: list, track_ids: list):
        try:
            moods = self.controller.get.get_all_entities("Mood") or []
            if not moods:
                submenu.addAction("No moods available").setEnabled(False)
                return

            # Checkmark if ALL selected tracks share the mood
            track_mood_sets = []
            for track in tracks:
                tm = getattr(track, "moods", []) or []
                track_mood_sets.append({m.mood_id for m in tm})
            all_mood_ids = (
                track_mood_sets[0].intersection(*track_mood_sets[1:])
                if track_mood_sets
                else set()
            )

            children_map: dict = {}
            for m in moods:
                pid = getattr(m, "parent_id", None)
                children_map.setdefault(pid, []).append(m)
            for pid in children_map:
                children_map[pid].sort(key=lambda x: x.mood_name.lower())

            self._build_mood_hierarchy(
                submenu, None, children_map, all_mood_ids, track_ids
            )

        except Exception as e:
            logger.error(f"Error populating mood submenu: {e}")
            submenu.addAction("Error loading moods").setEnabled(False)

    def _build_mood_hierarchy(
        self, parent_menu, parent_id, children_map, member_ids, track_ids, depth=0
    ):
        if depth > 8:
            return
        for mood in children_map.get(parent_id, []):
            has_children = bool(children_map.get(mood.mood_id))
            if has_children:
                sub = QMenu(mood.mood_name, parent_menu)
                self._build_mood_hierarchy(
                    sub, mood.mood_id, children_map, member_ids, track_ids, depth + 1
                )
                sub.addSeparator()
                act = QAction(f"Add to '{mood.mood_name}'", sub)
                act.setData((mood.mood_id, track_ids))
                if mood.mood_id in member_ids:
                    act.setCheckable(True)
                    act.setChecked(True)
                act.triggered.connect(self.add_to_mood)
                sub.addAction(act)
                parent_menu.addMenu(sub)
            else:
                act = QAction(mood.mood_name, parent_menu)
                act.setData((mood.mood_id, track_ids))
                if mood.mood_id in member_ids:
                    act.setCheckable(True)
                    act.setChecked(True)
                act.triggered.connect(self.add_to_mood)
                parent_menu.addAction(act)

    def add_to_mood(self):
        action = self.sender()
        if not action:
            return
        mood_id, track_ids = action.data()
        try:
            added = 0
            for track_id in track_ids:
                already = self.controller.get.get_entity_links(
                    "MoodTrackAssociation", mood_id=mood_id, track_id=int(track_id)
                )
                if already:
                    continue
                ok = self.controller.add.add_entity_link(
                    "MoodTrackAssociation",
                    mood_id=mood_id,
                    track_id=int(track_id),
                )
                if ok:
                    added += 1
            logger.info(f"Added {added} track(s) to mood {mood_id}")
        except Exception as e:
            logger.error(f"Error adding tracks to mood: {e}")

    # =========================================================================
    #  Fill model fully (used by _add_filtered_to_queue edge case)
    # =========================================================================

    def _fill_model_completely(self):
        source = self._filtered_tracks if self._filter_active else self._all_tracks
        while self._loaded_count < len(source):
            self._append_next_batch(source)
