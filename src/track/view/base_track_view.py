# base_track_view.py
"""BaseTrackView: a reusable dialog that lists a fixed set of tracks."""

import csv
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction, QKeySequence, QShortcut, QStandardItemModel
from PySide6.QtWidgets import QDialog, QFileDialog, QHeaderView, QMenu, QMessageBox, QTableView, QToolButton, QVBoxLayout
from sqlalchemy.exc import SQLAlchemyError

from src.common.dialogs.delete_confirmation import confirm_delete_with_file_option
from src.common.widgets.entity_submenu import populate_entity_submenu, selection_membership
from src.db.db_mapping_tracks import TRACK_FIELDS
from src.foundation.logger_config import logger
from src.foundation.status_utility import show_status_message
from src.track.edit.track_edit import MultiTrackEditDialog, TrackEditDialog
from src.track.track_shuffle import play_tracks, shuffle_and_play
from src.track.view.track_table import TrackTable
from src.track.view.track_toolbar import make_primary_tool_button
from src.track.view.track_view_actions import TRACK_ID_MIME, copy_selected_rows, format_track_value, start_track_drag
from src.track.view.track_view_columns import TrackViewColumnsMixin
from src.track.view.track_view_data import TrackViewDataMixin
from src.track.view.track_view_editing import add_tracks_to_mood, add_tracks_to_playlist, delete_tracks_with_prompt, report_add_result
from src.track.view.track_view_search import TrackViewSearchMixin
from src.track.view.track_view_toolbar import TrackViewToolbarMixin


class BaseTrackView(QDialog, TrackViewToolbarMixin, TrackViewColumnsMixin, TrackViewDataMixin, TrackViewSearchMixin):
    """Track list dialog with the library view's toolbar, columns and search, over a fixed list."""

    # Dialog-specific parts: CSV export, play/shuffle buttons, a persistent context menu
    # (callers append actions to it), and optional drag/drop.
    track_deleted = Signal(int)

    def __init__(self, controller, tracks, title="Tracks", enable_drag=False, enable_drop=False):
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
        # The list is normally small: scope the lookup-cache queries to it, not the whole library.
        self._scope_lookup_caches_to_tracks = True

        self.setWindowTitle(title)
        self.setMinimumSize(800, 600)

        self.model = QStandardItemModel()
        self.columns = {field_name: field_config.friendly for field_name, field_config in self.track_fields.items() if field_config.friendly}

        self.layout = QVBoxLayout(self)
        self.layout.setSpacing(6)

        # Shared toolbar (TrackViewToolbarMixin); filters on every keystroke.
        self._build_toolbar()
        self._populate_search_combo()
        # Some callers (mood_dialog.py, playlist_tracks_window.py) use the old `info_label` name.
        self.info_label = self.status_label

        self.table = TrackTable(self)
        self.table.setModel(self.model)
        self._setup_table()
        self.table.verticalScrollBar().valueChanged.connect(self._on_scroll)
        self.layout.addWidget(self.table, 1)
        self._install_table_chrome()

        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.show_context_menu)
        self.setup_context_menu()

        # Double-click a row to preview-play that track
        self.table.doubleClicked.connect(self._on_row_double_clicked)

        copy_shortcut = QShortcut(QKeySequence.Copy, self.table)
        copy_shortcut.activated.connect(self._copy_selected_rows)
        delete_shortcut = QShortcut(QKeySequence.Delete, self.table)
        delete_shortcut.activated.connect(self._delete_selected_tracks)

        if self.enable_drag:
            self.setup_drag_support()
        if self.enable_drop:
            self.setup_drop_support()

        self.load_data(tracks)

    # ── Toolbar mixin hooks ───────────────────────────────────────────────

    def _add_toolbar_actions(self, toolbar):
        """Add Play, Shuffle, the Actions menu and the Columns button."""
        toolbar.add_action(make_primary_tool_button("▶  Play", "Play the listed tracks in order", lambda: self._play_visible(shuffle=False)))
        toolbar.add_action(make_primary_tool_button("⤮  Shuffle", "Shuffle the listed tracks and play", lambda: self._play_visible(shuffle=True)))

        self.actions_button = QToolButton(self)
        self.actions_button.setText("⋮ Actions")
        self.actions_button.setToolTip("Queue and export")
        self.actions_button.setPopupMode(QToolButton.InstantPopup)
        actions_menu = QMenu(self.actions_button)
        actions_menu.addAction("Add Selected to Queue", self.add_selected_to_queue)
        actions_menu.addAction("Play Selected Next", lambda: self.add_selected_to_queue(insert_next=True))
        actions_menu.addSeparator()
        actions_menu.addAction("📄 Export to CSV…", self.export_tracks_to_csv)
        self.actions_button.setMenu(actions_menu)
        toolbar.add_action(self.actions_button)

        self.view_button = QToolButton(self)
        self.view_button.setText("⚙ Columns")
        self.view_button.setToolTip("Column order and visibility. Right-click a column header to show or hide columns.")
        self.view_button.clicked.connect(self.show_column_customization)
        toolbar.add_action(self.view_button)

    def _selection_bar_actions(self):
        """Return the (play_next, queue, edit, delete) callables for the selection bar."""
        return (lambda: self.add_selected_to_queue(insert_next=True), self.add_selected_to_queue, self.edit_selected_tracks, self._delete_selected_tracks)

    def _now_playing_source(self):
        """Return the media player, for the now-playing highlight."""
        return getattr(self.controller, "mediaplayer", None)

    def _play_visible(self, shuffle: bool):
        """Play the listed tracks, in order or shuffled."""
        play_tracks(self, self.controller, self._visible_source(), shuffle=shuffle)

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

        self.table.setSelectionBehavior(QTableView.SelectRows)
        self.table.setSelectionMode(QTableView.ExtendedSelection)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableView.NoEditTriggers)

        self._set_initial_column_visibility()
        self.load_column_state()

    def setup_context_menu(self):
        """Build the persistent context menu (callers may append actions)."""
        self.context_menu = QMenu(self)

        self.add_to_playlist_menu = QMenu("➕  Add to Playlist", self)  # noqa: RUF001
        self.add_to_mood_menu = QMenu("🎭  Add to Mood", self)

        self.add_to_queue_action = QAction("Add to Queue", self)
        self.add_to_queue_action.triggered.connect(self.add_selected_to_queue)

        self.add_to_queue_next_action = QAction("Add to Queue (Next)", self)
        self.add_to_queue_next_action.triggered.connect(lambda: self.add_selected_to_queue(insert_next=True))

        # Label and enabled state are set per selection in show_context_menu.
        self.edit_action = QAction("✏️ Edit Track", self)
        self.edit_action.triggered.connect(self.edit_selected_tracks)

        self.context_menu.addAction(self.add_to_queue_action)
        self.context_menu.addAction(self.add_to_queue_next_action)
        self.context_menu.addSeparator()
        self.context_menu.addAction(self.edit_action)
        self.context_menu.addSeparator()
        self.context_menu.addMenu(self.add_to_playlist_menu)
        self.context_menu.addMenu(self.add_to_mood_menu)
        self.context_menu.addSeparator()

        self.delete_tracks_action = QAction("🗑 Delete Tracks...", self)
        self.delete_tracks_action.triggered.connect(self._delete_selected_tracks)
        self.context_menu.addAction(self.delete_tracks_action)

    def show_context_menu(self, position):
        """Show the context menu for the selection at `position`."""
        selected_tracks = self.get_selected_tracks()
        if not selected_tracks:
            return
        track_ids = [str(track.track_id) for track in selected_tracks]

        count = len(selected_tracks)
        self.edit_action.setText(f"✏️ Edit {count} Tracks" if count > 1 else "✏️ Edit Track")

        self.add_to_playlist_menu.clear()
        self.add_to_mood_menu.clear()
        self._populate_playlist_menu(selected_tracks, track_ids)
        self._populate_mood_menu(selected_tracks, track_ids)

        self.context_menu.exec_(self.table.mapToGlobal(position))

    def get_selected_tracks(self):
        """Return the Track objects of the selected rows, in row order."""
        source = self._visible_source()
        rows = sorted({index.row() for index in self.table.selectionModel().selectedRows()})
        return [source[row] for row in rows if 0 <= row < len(source)]

    def _on_row_double_clicked(self, index):
        """Play a short preview of the double-clicked track."""
        source = self._visible_source()
        row = index.row()
        if not 0 <= row < len(source):
            return

        file_path = getattr(source[row], "track_file_path", None)
        if not file_path or not hasattr(self.controller, "mediaplayer"):
            return

        try:
            if self.controller.mediaplayer.load_track(Path(file_path)):
                self.controller.mediaplayer.play()
            else:
                logger.warning(f"Failed to load track for preview: {file_path}")
        except (OSError, RuntimeError) as e:
            logger.error(f"Error previewing track: {e}")

    def _copy_selected_rows(self):
        """Copy the selected rows (visible columns, in visual order) to the clipboard."""
        copy_selected_rows(self.table, self.model, self.columns)

    def _get_queue_manager(self):
        """Return the playback queue manager, or None."""
        queue_manager = getattr(self.controller, "queue_manager", None)
        if not queue_manager and hasattr(self.controller, "mediaplayer"):
            queue_manager = getattr(self.controller.mediaplayer, "queue_manager", None)
        return queue_manager

    def add_selected_to_queue(self, insert_next=False):
        """Add the selected tracks to the queue (or after the current track)."""
        selected_tracks = self.get_selected_tracks()
        if not selected_tracks:
            return

        queue_manager = self._get_queue_manager()
        if not queue_manager:
            logger.warning("Queue manager not found in controller")
            return

        if insert_next and hasattr(queue_manager, "insert_tracks_next"):
            queue_manager.insert_tracks_next(selected_tracks)
        else:
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

    def _get_artist_name(self, track):
        """Return the cached primary artist names of `track` (safe on worker threads)."""
        # Never lazy-load track.artist_roles here: FilterWorker calls this off the main thread.
        return self._artist_name_cache.get(track.track_id, "Unknown Artist")

    def _format_value(self, value, field_name, field_config):
        """Format a field value for table/CSV display."""
        return format_track_value(value, field_name)

    def shuffle_all_tracks(self):
        """Shuffle the listed tracks (respecting any active filter) and start playing."""
        shuffle_and_play(self, self.controller, self._visible_source())

    def export_tracks_to_csv(self):
        """Export the listed tracks (respecting any active filter) to CSV."""
        tracks_to_export = self._visible_source()

        if not tracks_to_export:
            show_status_message(self, "No tracks available to export.")
            return

        file_path, _ = QFileDialog.getSaveFileName(self, "Export Track List to CSV", "tracks.csv", "CSV Files (*.csv)")
        if not file_path:
            return

        try:
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

    # ── Drag and drop ─────────────────────────────────────────────────────

    def setup_drag_support(self):
        """Let the user drag the selected tracks out as TRACK_ID_MIME."""
        self.table.setDragEnabled(True)
        # The table viewport receives the mouse events, so the drag must start on the table.
        self.table.startDrag = lambda _actions: start_track_drag(self.table, self.get_selected_tracks())

    def setup_drop_support(self):
        """Accept drops of TRACK_ID_MIME (callers replace dropEvent)."""
        self.table.setAcceptDrops(True)
        self.table.setDropIndicatorShown(True)
        self.table.viewport().setAcceptDrops(True)

    def dragEnterEvent(self, event):
        """Accept a drag that carries track ids."""
        if self.enable_drop and event.mimeData().hasFormat(TRACK_ID_MIME):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        """Accept a drag that carries track ids."""
        if self.enable_drop and event.mimeData().hasFormat(TRACK_ID_MIME):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        """Accept a track-id drop; callers replace this with their own handler."""
        if not self.enable_drop or not event.mimeData().hasFormat(TRACK_ID_MIME):
            event.ignore()
            return

        event.acceptProposedAction()
        logger.warning("dropEvent should be overridden by subclass")

    # ── Playlist / mood submenus ──────────────────────────────────────────

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
        """Add the menu action's tracks to its playlist."""
        action = self.sender()
        if not action:
            return
        playlist_id, track_ids = action.data()
        try:
            added, already, failed = add_tracks_to_playlist(self.controller, playlist_id, track_ids)
        except (SQLAlchemyError, ValueError) as e:
            logger.error(f"Error adding tracks to playlist: {e!s}")
            QMessageBox.critical(self, "Error", f"Failed to add tracks to playlist:\n{e!s}")
            return
        report_add_result(self, "the playlist", added, already, failed)

    def _add_to_mood_from_menu(self):
        """Tag the menu action's tracks with its mood."""
        action = self.sender()
        if not action:
            return
        mood_id, track_ids = action.data()
        try:
            added, already, failed = add_tracks_to_mood(self.controller, mood_id, track_ids)
        except (SQLAlchemyError, ValueError) as e:
            logger.error(f"Error adding tracks to mood: {e!s}")
            QMessageBox.critical(self, "Error", f"Failed to add tracks to mood:\n{e!s}")
            return
        report_add_result(self, "the mood", added, already, failed)

    # ── Delete ────────────────────────────────────────────────────────────

    def _delete_selected_tracks(self):
        """Delete the selected tracks: DB-only, or DB + audio file(s) from disk."""
        tracks = self.get_selected_tracks()
        if not tracks:
            return

        # confirm is looked up here at call time, so tests can patch this module's name.
        deleted_ids = delete_tracks_with_prompt(self, self.controller, tracks, confirm=confirm_delete_with_file_option)
        if not deleted_ids:
            return

        deleted = set(deleted_ids)
        self._all_tracks = [t for t in self._all_tracks if t.track_id not in deleted]
        self.load_data(self._all_tracks)

        for tid in deleted_ids:
            self.track_deleted.emit(tid)
