from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from logger_config import logger
from track_edit import TrackEditDialog
from track_editing_multiple import MultiTrackEditDialog


class QueueDockWidget(QWidget):
    """A dockable widget to display and manage the playback queue."""

    # Signals
    track_double_clicked = Signal(Path)
    queue_modified = Signal()

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.queue_manager = controller.mediaplayer.queue_manager

        # Create a timer for deferred updates
        self.update_timer = QTimer()
        self.update_timer.setSingleShot(True)
        self.update_timer.setInterval(50)  # 50ms delay
        self.update_timer.timeout.connect(self.update_queue_display)

        self.queue_manager.queue_changed.connect(self.update_timer.start)
        self.init_ui()
        self.setup_drag_drop()
        self.setup_context_menu()

    def init_ui(self):
        """Set up the user interface for the queue."""
        self.setWindowTitle("Playback Queue")
        self.setMinimumSize(300, 400)

        layout = QVBoxLayout(self)

        # Header with controls
        header_layout = QHBoxLayout()
        header_layout.addWidget(QLabel("Queue"))
        header_layout.addStretch()

        self.shuffle_button = QPushButton("Shuffle")
        self.shuffle_button.clicked.connect(self.queue_manager.shuffle_queue)

        self.clear_button = QPushButton("Clear")
        self.clear_button.clicked.connect(self.clear_queue)

        header_layout.addWidget(self.shuffle_button)
        header_layout.addWidget(self.clear_button)

        # Queue list
        self.queue_list = QListWidget()
        self.queue_list.itemDoubleClicked.connect(self.on_track_double_clicked)
        self.queue_list.setAlternatingRowColors(True)
        self.queue_list.setSelectionMode(QListWidget.ExtendedSelection)
        self.queue_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.queue_list.customContextMenuRequested.connect(self.show_context_menu)

        # Allow internal reordering via Drag and Drop
        self.queue_list.setDragEnabled(True)
        self.queue_list.setDragDropMode(QListWidget.InternalMove)
        self.queue_list.model().rowsMoved.connect(self.handle_internal_move)

        # Bottom controls
        bottom_layout = QHBoxLayout()
        self.remove_button = QPushButton("Remove Selected")
        self.remove_button.clicked.connect(self.remove_selected_tracks)
        bottom_layout.addWidget(self.remove_button)
        bottom_layout.addStretch()

        # Assemble layout
        layout.addLayout(header_layout)
        layout.addWidget(self.queue_list)
        layout.addLayout(bottom_layout)

        # Initial UI update
        self.update_queue_display()

    def setup_context_menu(self):
        """Set up the context menu for queue items."""
        self.context_menu = QMenu(self)

        # Single track actions
        self.edit_track_action = QAction("Edit Track", self)
        self.edit_track_action.triggered.connect(self.edit_selected_track)

        # Multi-track actions
        self.edit_multiple_tracks_action = QAction("Edit Multiple Tracks", self)
        self.edit_multiple_tracks_action.triggered.connect(self.edit_multiple_tracks)

        # Common actions
        self.remove_action = QAction("Remove from Queue", self)
        self.remove_action.triggered.connect(self.remove_selected_tracks)

    def show_context_menu(self, position):
        """Show context menu at the given position with appropriate actions."""
        selected_items = self.queue_list.selectedItems()

        self.context_menu.clear()

        if len(selected_items) == 1:
            # Single track selected
            self.context_menu.addAction(self.edit_track_action)
            self.context_menu.addAction(self.remove_action)
        elif len(selected_items) > 1:
            # Multiple tracks selected
            self.context_menu.addAction(self.edit_multiple_tracks_action)
            self.context_menu.addAction(self.remove_action)
        else:
            # No selection
            return

        self.context_menu.exec_(self.queue_list.mapToGlobal(position))

    def setup_drag_drop(self):
        """Set up drag and drop functionality."""
        self.queue_list.setDragDropMode(QListWidget.DropOnly)
        self.queue_list.setAcceptDrops(True)
        self.queue_list.setDragEnabled(False)

        # Override drag & drop events
        self.queue_list.dropEvent = self.handle_drop
        self.queue_list.dragEnterEvent = self.handle_drag_enter
        self.queue_list.dragMoveEvent = self.handle_drag_move

    def edit_selected_track(self):
        """Open edit dialog for the selected track."""
        selected_items = self.queue_list.selectedItems()
        if len(selected_items) != 1:
            return

        index = self.queue_list.row(selected_items[0])
        if 0 <= index < len(self.queue_manager.queue):
            track = self.queue_manager.queue[index]
            dialog = TrackEditDialog(track, self.controller, self)
            dialog.field_modified.connect(self.on_track_modified)
            dialog.exec_()

    def edit_multiple_tracks(self):
        """Open multi-track edit dialog for selected tracks."""
        selected_items = self.queue_list.selectedItems()
        if len(selected_items) < 2:
            return

        indices = [self.queue_list.row(item) for item in selected_items]
        tracks = [
            self.queue_manager.queue[i]
            for i in indices
            if 0 <= i < len(self.queue_manager.queue)
        ]

        if tracks:
            dialog = MultiTrackEditDialog(tracks, self.controller, self)
            dialog.field_modified.connect(self.on_track_modified)
            dialog.exec_()

    def on_track_modified(self):
        """Handle track modification signal - update display and notify."""
        self.update_queue_display()
        self.queue_modified.emit()

    def handle_drag_enter(self, event):
        """Accept drag enter events with track data."""
        if event.mimeData().hasFormat("application/x-track-id"):
            event.acceptProposedAction()
        else:
            event.ignore()

    def handle_drag_move(self, event):
        """Accept drag move events with track data."""
        if event.mimeData().hasFormat("application/x-track-id"):
            event.acceptProposedAction()
        else:
            event.ignore()

    def handle_drop(self, event):
        """Handle dropping of tracks via internal drag."""
        try:
            if not event.mimeData().hasFormat("application/x-track-id"):
                event.ignore()
                return

            track_ids_data = (
                event.mimeData().data("application/x-track-id").data().decode()
            )
            track_ids = [
                int(tid.strip()) for tid in track_ids_data.split(",") if tid.strip()
            ]

            for track_id in track_ids:
                track = self.controller.get.get_entity_object(
                    "Track", track_id=track_id
                )
                if track:
                    self.queue_manager.add_track_to_queue(track)

            self.update_queue_display()
            self.queue_modified.emit()
            event.acceptProposedAction()

        except Exception as e:
            logger.error(f"Error handling drop in queue: {e}")
            event.ignore()

    def update_queue_display(self):
        self.queue_list.blockSignals(True)
        self.queue_list.clear()

        # The 'Current' track in your window is usually at index 1
        current_track_obj = self.queue_manager.get_current_track()

        for i, track in enumerate(self.queue_manager.queue):
            is_current = (track == current_track_obj) and (
                current_track_obj is not None
            )

            # UI styling remains the same, but 'is_current' is now object-based
            prefix = "▶ " if is_current else f"{i + 1}. "
            item = QListWidgetItem(f"{prefix}{self.get_track_display_name(track)}")

            if is_current:
                # High-contrast styling for the active track
                item.setBackground(QColor(70, 130, 180))
                item.setForeground(QColor(255, 255, 255))
                font = item.font()
                font.setBold(True)
                item.setFont(font)
                self.queue_list.scrollToItem(item)

            self.queue_list.addItem(item)

        self.queue_list.blockSignals(False)

    def get_track_display_name(self, track) -> str:
        """Return a formatted track name with artist."""
        try:
            title = getattr(
                track, "track_name", getattr(track, "title", "Unknown Track")
            )
            artist_name = "Unknown Artist"

            if getattr(track, "primary_artist", None):
                artist_name = getattr(
                    track.primary_artist, "artist_name", "Unknown Artist"
                )
            elif getattr(track, "artists", None):
                first_artist = next(iter(track.artists), None)
                if first_artist:
                    artist_name = getattr(first_artist, "artist_name", "Unknown Artist")

            return f"{title} - {artist_name}"
        except Exception as e:
            logger.error(f"Error getting track display name: {e}")
            return "Unknown Track"

    def on_track_double_clicked(self, item):
        """Emit signal with file path when a track is double-clicked."""
        index = self.queue_list.row(item)
        if 0 <= index < len(self.queue_manager.queue):
            track = self.queue_manager.queue[index]
            if hasattr(track, "track_file_path"):
                self.track_double_clicked.emit(Path(track.track_file_path))

    def clear_queue(self):
        """Clear all tracks from the queue."""
        self.queue_manager.clear_queue()
        self.update_queue_display()
        self.queue_modified.emit()

    def remove_selected_tracks(self):
        """Remove all selected tracks from the queue."""
        selected_items = self.queue_list.selectedItems()
        if not selected_items:
            return

        # Remove from highest index to lowest to prevent index shift
        for index in sorted(
            [self.queue_list.row(item) for item in selected_items], reverse=True
        ):
            self.queue_manager.remove_from_queue(index)

        self.update_queue_display()
        self.queue_modified.emit()

    def dropEvent(self, event):
        """Handle external file drops."""
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                file_path = Path(url.toLocalFile())
                track = self.controller.get.get_entity_object(
                    "Track", track_file_path=str(file_path)
                )
                if track:
                    self.queue_manager.add_track_to_queue(track)

            self.update_queue_display()
            self.queue_modified.emit()
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragEnterEvent(self, event):
        """Accept drag enter events for external files."""
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        """Accept drag move events for external files."""
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def handle_internal_move(self, parent, start, end, destination, row):
        """Sync the QueueManager when the user drags items within the list."""
        # Logic to move the item in self.queue_manager.queue
        # PySide's InternalMove handles the UI; you just sync the Python list.
        track = self.queue_manager.queue.pop(start)
        # Adjust logic for insertion point
        insert_at = row if start > row else row - 1
        self.queue_manager.queue.insert(insert_at, track)
        # Notify other components if necessary
        self.queue_modified.emit()
