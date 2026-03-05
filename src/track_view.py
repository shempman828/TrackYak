"""
track_view.py — TrackView
"""

import random
from pathlib import Path

from PySide6.QtCore import QByteArray, QMimeData, Qt, QThread, QTimer, Signal
from PySide6.QtGui import (
    QAction,
    QDrag,
    QKeySequence,
    QPainter,
    QPixmap,
    QShortcut,
    QStandardItem,
    QStandardItemModel,
)
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QTableView,
    QToolButton,
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

# Sentinel value for the "All Columns" search option.
_SEARCH_ALL = "__all__"


# =============================================================================
#  Background filter worker — keeps the UI responsive while filtering
# =============================================================================


class _FilterWorker(QThread):
    """
    Runs the track-filter loop on a background thread.
    Emits `finished` with the matching subset when done.
    """

    finished = Signal(list)

    def __init__(
        self, tracks: list, search_text: str, field_name: str, get_artist_fn, format_fn
    ):
        super().__init__()
        self._tracks = tracks
        self._search_text = search_text.strip().lower()
        self._field_name = field_name  # "__all__" → search every column
        self._get_artist = get_artist_fn
        self._format = format_fn

    def run(self):
        text = self._search_text
        results = []

        for t in self._tracks:
            if self._field_name == _SEARCH_ALL:
                # Search a broad set of common fields
                values = [
                    (getattr(t, "track_name", "") or "").lower(),
                    (self._get_artist(t) or "").lower(),
                ]
                album_obj = getattr(t, "album", None)
                if album_obj:
                    values.append((getattr(album_obj, "album_name", "") or "").lower())
                # Also check all other string-like track fields
                for field_name in TRACK_FIELDS:
                    if field_name not in ("track_name", "artist_name", "album_name"):
                        val = getattr(t, field_name, None)
                        if val is not None:
                            values.append(str(val).lower())
                if any(text in v for v in values):
                    results.append(t)
            else:
                # Search a specific field
                if self._field_name == "artist_name":
                    val = (self._get_artist(t) or "").lower()
                else:
                    raw = getattr(t, self._field_name, None)
                    val = self._format(
                        raw, self._field_name, TRACK_FIELDS.get(self._field_name)
                    ).lower()
                if text in val:
                    results.append(t)

        self.finished.emit(results)


# =============================================================================
#  TrackView
# =============================================================================


class TrackView(QWidget):
    """
    Main library track view with lazy loading.

    self._all_tracks       — full track list from DB, loaded ONCE and cached.
    self._loaded_count     — rows currently pushed into the Qt model.
    self._filtered_tracks  — active subset when a search filter is live.
    self._filter_active    — True while a search filter is applied.
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
        self._tracks_loaded: bool = False
        self._filter_worker: _FilterWorker | None = None

        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(4, 4, 4, 4)
        self.layout.setSpacing(4)

        # ── Toolbar row 1: search + column filter + actions ───────────────
        self._build_toolbar()

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

        # ── Keyboard shortcuts ────────────────────────────────────────────
        copy_shortcut = QShortcut(QKeySequence.Copy, self.table)
        copy_shortcut.activated.connect(self._copy_selected_rows)

        delete_shortcut = QShortcut(QKeySequence.Delete, self.table)
        delete_shortcut.activated.connect(self.delete_selected_tracks)

        # Initial load
        self.load_tracks_on_startup()

    # =========================================================================
    #  Toolbar
    # =========================================================================

    def _build_toolbar(self):
        """Build a compact single-row toolbar replacing the old button rows."""
        toolbar_row = QHBoxLayout()
        toolbar_row.setSpacing(4)

        # Search field with built-in clear (✕) button
        self.search_bar = QLineEdit(self)
        self.search_bar.setPlaceholderText("Search tracks…")
        self.search_bar.setClearButtonEnabled(True)

        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(SEARCH_DEBOUNCE_MS)
        self._search_timer.timeout.connect(self._apply_search_filter)
        self.search_bar.textChanged.connect(self._search_timer.start)

        # Column selector for targeted search
        self.search_column_combo = QComboBox(self)
        self.search_column_combo.setToolTip("Choose which column to search")
        self.search_column_combo.addItem("All Columns", _SEARCH_ALL)
        # Populated fully after _initialize_columns() is called — see _populate_search_combo()

        toolbar_row.addWidget(self.search_bar, stretch=1)
        toolbar_row.addWidget(self.search_column_combo)

        # ── "⋮ Queue" drop-down button ────────────────────────────────────
        queue_btn = QToolButton(self)
        queue_btn.setText("＋ Queue")
        queue_btn.setToolTip("Add tracks to the playback queue")
        queue_btn.setPopupMode(QToolButton.InstantPopup)

        queue_menu = QMenu(queue_btn)
        queue_menu.addAction("Add Filtered to Queue", self._add_filtered_to_queue)
        queue_menu.addAction(
            "Shuffle Filtered to Queue",
            lambda: self._add_filtered_to_queue(shuffle=True),
        )
        queue_menu.addSeparator()
        queue_menu.addAction("Add Entire Library to Queue", self._add_all_to_queue)
        queue_menu.addAction(
            "Shuffle Entire Library", lambda: self._add_all_to_queue(shuffle=True)
        )
        queue_btn.setMenu(queue_menu)

        # ── "⋮ View" drop-down button ─────────────────────────────────────
        view_btn = QToolButton(self)
        view_btn.setText("⚙ View")
        view_btn.setToolTip("Column visibility, order, and other options")
        view_btn.setPopupMode(QToolButton.InstantPopup)

        view_menu = QMenu(view_btn)
        view_menu.addAction("Toggle Columns", self.show_column_menu)
        view_menu.addAction(
            "Column Order && Visibility", self.show_column_customization
        )
        view_menu.addSeparator()
        view_menu.addAction("Refresh Library", self._force_reload)
        view_btn.setMenu(view_menu)

        toolbar_row.addWidget(queue_btn)
        toolbar_row.addWidget(view_btn)

        # Status label sits right-aligned after the buttons
        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: grey; font-size: 11px;")
        toolbar_row.addWidget(self.status_label)

        self.layout.addLayout(toolbar_row)

    def _populate_search_combo(self):
        """Fill the column search combo with visible-column options after columns are known."""
        self.search_column_combo.blockSignals(True)
        self.search_column_combo.clear()
        self.search_column_combo.addItem("All Columns", _SEARCH_ALL)
        for field_name, friendly in self.columns.items():
            self.search_column_combo.addItem(friendly, field_name)
        self.search_column_combo.blockSignals(False)

    # =========================================================================
    #  Columns
    # =========================================================================

    def _initialize_columns(self):
        self.columns = {}
        for field_name, field_config in self.track_fields.items():
            if field_config.friendly:
                self.columns[field_name] = field_config.friendly
        # Now that columns are known, populate the search combo
        self._populate_search_combo()

    def _setup_table(self):
        self.model.setColumnCount(len(self.columns))
        self.model.setHorizontalHeaderLabels(list(self.columns.values()))

        self.table.setSortingEnabled(True)

        # Interactive resizing so users can drag column edges
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setStretchLastSection(False)
        # Give a sensible default width; saved widths will override this
        header.setDefaultSectionSize(120)

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

    def get_column_state(self) -> dict:
        """Return current visible columns and their visual order."""
        col_keys = list(self.columns.keys())
        header = self.table.horizontalHeader()
        visible = [
            k for i, k in enumerate(col_keys) if not self.table.isColumnHidden(i)
        ]
        order = [col_keys[header.logicalIndex(v)] for v in range(header.count())]
        return {"visible": visible, "order": order}

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
        """Toggle Columns menu, grouped by TrackField category into submenus."""
        menu = QMenu(self)
        col_keys = list(self.columns.keys())

        # Build a dict of  category → list of (index, field_name, label)
        category_groups: dict[str, list] = {}
        for i, (key, label) in enumerate(self.columns.items()):
            field_config = self.track_fields.get(key)
            cat = (field_config.category or "Other") if field_config else "Other"
            category_groups.setdefault(cat, []).append((i, key, label))

        for cat, fields in sorted(category_groups.items()):
            submenu = QMenu(cat, menu)
            for i, key, label in fields:
                action = QAction(label, submenu)
                action.setCheckable(True)
                action.setChecked(not self.table.isColumnHidden(i))
                action.setData(i)
                action.triggered.connect(self._toggle_column)
                submenu.addAction(action)
            menu.addMenu(submenu)

        # Find a sensible anchor: use the View button if it still exists, else cursor
        menu.exec_(self.cursor().pos())

    def _toggle_column(self):
        action = self.sender()
        if action:
            i = action.data()
            self.table.setColumnHidden(i, not action.isChecked())
            self.save_column_state()

    def show_column_customization(self):
        dialog = ColumnCustomizationDialog(self, self)
        dialog.exec_()

    def _reorder_columns(self, new_order: list):
        """Move columns to match the requested logical order."""
        col_keys = list(self.columns.keys())
        header = self.table.horizontalHeader()
        for target_visual, key in enumerate(new_order):
            if key in col_keys:
                logical = col_keys.index(key)
                current_visual = header.visualIndex(logical)
                if current_visual != target_visual:
                    header.moveSection(current_visual, target_visual)

    # =========================================================================
    #  Data loading — lazy, single DB call
    # =========================================================================

    def load_tracks_on_startup(self):
        """
        Load all tracks from DB into self._all_tracks (once).
        Only pushes the first LAZY_BATCH_SIZE rows into the Qt model.
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
        """Explicitly re-query the DB (Refresh)."""
        self._tracks_loaded = False
        self.load_tracks_on_startup()

    def load_data(self, tracks: list):
        """External callers (e.g. main_window refresh) can push a new track list."""
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
    #  Search / filter  (runs on a background thread to avoid UI lockup)
    # =========================================================================

    def _apply_search_filter(self):
        """
        Kicks off a background worker to filter tracks without blocking the UI.
        """
        search_text = self.search_bar.text().strip().lower()

        if not search_text:
            # Nothing typed — restore the full list immediately
            self._filter_active = False
            self._filtered_tracks = []
            self._loaded_count = 0
            self.model.setRowCount(0)
            self._append_next_batch(self._all_tracks)
            self._update_status()
            return

        # Stop any already-running worker before starting a new one
        if self._filter_worker and self._filter_worker.isRunning():
            self._filter_worker.quit()
            self._filter_worker.wait()

        field_name = self.search_column_combo.currentData() or _SEARCH_ALL

        self._filter_worker = _FilterWorker(
            self._all_tracks,
            search_text,
            field_name,
            self._get_artist_name,
            self._format_value,
        )
        self._filter_worker.finished.connect(self._on_filter_done)
        self._filter_worker.start()

    def _on_filter_done(self, results: list):
        """Called on the main thread when the background filter finishes."""
        self._filter_active = True
        self._filtered_tracks = results
        self._loaded_count = 0
        self.model.setRowCount(0)
        self._append_next_batch(self._filtered_tracks)
        self._update_status()
        logger.debug(f"Filter → {len(results):,} matches")

    def filter_tracks(self, text: str):
        """Public alias kept for compatibility with external callers."""
        self.search_bar.setText(text)

    # =========================================================================
    #  Clipboard — Ctrl+C copies selected rows with column headers
    # =========================================================================

    def _copy_selected_rows(self):
        selected = self.table.selectionModel().selectedRows()
        if not selected:
            return

        col_keys = list(self.columns.keys())
        header = self.table.horizontalHeader()

        # Build the visual column order (only visible columns)
        visual_order = [
            header.logicalIndex(v)
            for v in range(header.count())
            if not self.table.isColumnHidden(header.logicalIndex(v))
        ]

        # Header row
        header_labels = [list(self.columns.values())[i] for i in visual_order]
        lines = ["\t".join(header_labels)]

        # Data rows
        for index in sorted(selected, key=lambda i: i.row()):
            row_data = []
            for col_i in visual_order:
                item = self.model.item(index.row(), col_i)
                row_data.append(item.text() if item else "")
            lines.append("\t".join(row_data))

        QApplication.clipboard().setText("\n".join(lines))
        logger.debug(f"Copied {len(selected)} row(s) to clipboard")

    # =========================================================================
    #  Artist name helper
    # =========================================================================

    def _get_artist_name(self, track) -> str:
        try:
            name = getattr(track, "primary_artist_names", None)
            if name:
                return name
        except Exception:
            pass
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
            qm.add_tracks_async(tracks, shuffle=False)
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
    #  Track deletion (single or multiple)
    # =========================================================================

    def delete_selected_tracks(self):
        tracks = self._get_selected_track_objects()
        if not tracks:
            return

        count = len(tracks)
        names = ", ".join(
            getattr(t, "track_name", f"ID {t.track_id}") for t in tracks[:3]
        )
        if count > 3:
            names += f" … and {count - 3} more"

        reply = QMessageBox.question(
            self,
            "Delete Tracks",
            f"Permanently delete {count} track(s)?\n\n{names}",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        deleted = 0
        for track in tracks:
            try:
                ok = self.controller.delete.delete_entity(
                    "Track", track_id=track.track_id
                )
                if ok:
                    deleted += 1
            except Exception as e:
                logger.error(f"Error deleting track {track.track_id}: {e}")

        logger.info(f"Deleted {deleted}/{count} track(s)")
        self._force_reload()

    # =========================================================================
    #  Context menu
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
        add_queue_action.triggered.connect(lambda: self.add_selected_to_queue(False))
        menu.addAction(add_queue_action)

        menu.addSeparator()

        # ── Edit ──────────────────────────────────────────────────────────
        edit_label = f"✏️  Edit {count} Track(s)" if count > 1 else "✏️  Edit Track"
        edit_action = QAction(edit_label, self)
        edit_action.triggered.connect(self.edit_selected_track)
        menu.addAction(edit_action)

        menu.addSeparator()

        # ── Moods submenu ─────────────────────────────────────────────────
        mood_menu = QMenu("🎭  Add to Mood", menu)
        self._populate_mood_submenu(mood_menu, track_ids)
        menu.addMenu(mood_menu)

        menu.addSeparator()

        # ── Delete ────────────────────────────────────────────────────────
        delete_label = f"🗑  Delete {count} Track(s)" if count > 1 else "🗑  Delete Track"
        delete_action = QAction(delete_label, self)
        delete_action.triggered.connect(self.delete_selected_tracks)
        menu.addAction(delete_action)

        menu.exec_(self.table.viewport().mapToGlobal(pos))

    # =========================================================================
    #  Mood helpers (unchanged logic, extracted to keep context menu tidy)
    # =========================================================================

    def _populate_mood_submenu(self, parent_menu: QMenu, track_ids: list):
        try:
            moods = self.controller.get.get_all_entities("Mood")
            if not moods:
                parent_menu.addAction("No moods found").setEnabled(False)
                return

            member_ids = set()
            if len(track_ids) == 1:
                links = self.controller.get.get_entity_links(
                    "MoodTrackAssociation", track_id=int(track_ids[0])
                )
                if links:
                    member_ids = {lnk.mood_id for lnk in links}

            for mood in moods:
                children = [
                    m
                    for m in moods
                    if getattr(m, "parent_mood_id", None) == mood.mood_id
                ]
                if children:
                    sub = QMenu(mood.mood_name, parent_menu)
                    for child in children:
                        act = QAction(child.mood_name, sub)
                        act.setData((child.mood_id, track_ids))
                        if child.mood_id in member_ids:
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
        except Exception as e:
            logger.error(f"Error populating mood submenu: {e}")

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
