"""
queue_dock.py — QueueDockWidget

Design
──────
The dock is split into two visual zones:

  ┌─────────────────────────────────┐
  │  ▶  Now Playing (pinned)        │  ← always visible, never scrolls
  │     Artist — Album              │
  ├─────────────────────────────────┤
  │  UPCOMING  [count]  [shuffle]   │  ← section header + controls
  ├─────────────────────────────────┤
  │  1. Track name                  │  ← lazy-loaded QListView
  │  2. Track name                  │    shows PAGE_SIZE rows at a time,
  │  ...                            │    appends more on scroll
  └─────────────────────────────────┘

The QListView is backed by _QueueModel — a QAbstractListModel that holds
the full queue slice (upcoming only) but is never rebuilt from scratch on
every change.  Updates are surgical: beginInsertRows / endInsertRows etc.

Palette (matches dark_mode.qss)
────────────────────────────────
  Base       #0b0c10
  Surface    #11121a
  Elevated   #1a1b26
  Accent     #8599ea  (periwinkle)
  Gold       #EAD685
  Pink       #EA8599
  Text       #b8c0f0
  Dim text   #555e7a
"""

from pathlib import Path

from PySide6.QtCore import QAbstractListModel, QModelIndex, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListView,
    QMenu,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from src.logger_config import logger
from src.track_edit import MultiTrackEditDialog, TrackEditDialog

# How many upcoming rows are visible / loaded at a time before the user scrolls
PAGE_SIZE = 100
# How close to the bottom (in rows) before we load the next page
SCROLL_THRESHOLD = 20


# ── Colour constants (mirrors dark_mode.qss) ──────────────────────────────────
_C_BASE = "#0b0c10"
_C_SURFACE = "#11121a"
_C_ELEVATED = "#1a1b26"
_C_ACCENT = "#8599ea"
_C_GOLD = "#EAD685"
_C_PINK = "#EA8599"
_C_TEXT = "#b8c0f0"
_C_DIM = "#555e7a"
_C_BORDER = "rgba(133, 153, 234, 0.25)"


# ── _QueueModel ───────────────────────────────────────────────────────────────


class _QueueModel(QAbstractListModel):
    """
    Lightweight model for the upcoming-track list.

    self._tracks holds a *view* into the upcoming portion of the queue
    (queue[1:]).  It is refreshed via reset_data() which does a minimal
    diff when possible, or a full reset for large structural changes.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        logger.info("Starting queue model")
        self._tracks: list = []
        self._loaded_count: int = 0  # how many rows are currently exposed to the view

    # ── QAbstractListModel interface ──────────────────────────────────────────

    def rowCount(self, parent=QModelIndex()) -> int:
        return self._loaded_count

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):
        if not index.isValid() or index.row() >= self._loaded_count:
            return None

        track = self._tracks[index.row()]

        if role == Qt.DisplayRole:
            return self._format(track, index.row())

        if role == Qt.ForegroundRole:
            return QColor(_C_TEXT)

        if role == Qt.UserRole:
            return track

        return None

    # ── Public API ────────────────────────────────────────────────────────────

    def reset_data(self, tracks: list):
        """Replace the underlying track list and reset loaded count."""
        self.beginResetModel()
        self._tracks = list(tracks)
        self._loaded_count = min(PAGE_SIZE, len(self._tracks))
        self.endResetModel()

    def load_more(self) -> bool:
        """
        Expose the next PAGE_SIZE rows to the view.
        Returns True if more rows were added, False if already at the end.
        """
        if self._loaded_count >= len(self._tracks):
            return False

        old = self._loaded_count
        new = min(self._loaded_count + PAGE_SIZE, len(self._tracks))
        self.beginInsertRows(QModelIndex(), old, new - 1)
        self._loaded_count = new
        self.endInsertRows()
        return True

    def total_count(self) -> int:
        return len(self._tracks)

    def track_at(self, row: int):
        if 0 <= row < len(self._tracks):
            return self._tracks[row]
        return None

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _format(track, row: int) -> str:
        title = (
            getattr(track, "track_name", None)
            or getattr(track, "track_title", None)
            or "Unknown"
        )
        artists = getattr(track, "artists", None) or []
        if artists:
            artist = getattr(artists[0], "artist_name", "") or ""
        else:
            artist = ""
        number = row + 1
        if artist:
            return f"{number}.  {title}  —  {artist}"
        return f"{number}.  {title}"


# ── _NowPlayingCard ───────────────────────────────────────────────────────────


class _NowPlayingCard(QFrame):
    """
    The pinned 'Now Playing' widget that sits above the scrollable list.
    Always visible — shows the current track and a play indicator.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("NowPlayingCard")
        self.setFrameShape(QFrame.NoFrame)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setStyleSheet(f"""
            QFrame#NowPlayingCard {{
                background: qlineargradient(
                    x1:0, y1:0, x2:1, y2:0,
                    stop:0 rgba(133,153,234,0.18),
                    stop:1 rgba(133,153,234,0.06)
                );
                border: none;
                border-left: 3px solid {_C_ACCENT};
                border-radius: 0px;
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(2)

        # "NOW PLAYING" label
        tag = QLabel("NOW PLAYING")
        tag.setStyleSheet(f"""
            color: {_C_ACCENT};
            font-size: 9px;
            font-weight: bold;
            letter-spacing: 0.12em;
            background: transparent;
        """)
        layout.addWidget(tag)

        # Track title
        self._title = QLabel("—")
        title_font = QFont("Cambria", 13, QFont.Bold)
        self._title.setFont(title_font)
        self._title.setStyleSheet(f"color: {_C_TEXT}; background: transparent;")
        self._title.setWordWrap(False)
        self._title.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        layout.addWidget(self._title)

        # Artist — Album
        self._sub = QLabel("—")
        self._sub.setStyleSheet(
            f"color: {_C_DIM}; font-size: 11px; background: transparent;"
        )
        layout.addWidget(self._sub)

    def update_track(self, track):
        if track is None:
            self._title.setText("—")
            self._sub.setText("—")
            return

        title = (
            getattr(track, "track_name", None)
            or getattr(track, "track_title", None)
            or "Unknown"
        )
        self._title.setText(title)

        artists = getattr(track, "artists", None) or []
        artist = getattr(artists[0], "artist_name", "") if artists else ""
        album_obj = getattr(track, "album", None)
        album = getattr(album_obj, "album_name", "") if album_obj else ""

        parts = [p for p in [artist, album] if p]
        self._sub.setText("  —  ".join(parts) if parts else "—")

    def clear(self):
        self._title.setText("—")
        self._sub.setText("—")


# ── QueueDockWidget ───────────────────────────────────────────────────────────


class QueueDockWidget(QWidget):
    """Dockable queue panel with a pinned now-playing card and lazy list."""

    track_double_clicked = Signal(Path)
    queue_modified = Signal()

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.queue_manager = controller.mediaplayer.queue_manager

        # Debounce rapid queue_changed bursts (e.g. during bulk adds)
        self._update_timer = QTimer(self)
        self._update_timer.setSingleShot(True)
        self._update_timer.setInterval(80)
        self._update_timer.timeout.connect(self._refresh_display)

        self.queue_manager.queue_changed.connect(self._update_timer.start)
        self.queue_manager.bulk_add_started.connect(self._on_bulk_start)
        self.queue_manager.bulk_add_finished.connect(self._on_bulk_finish)

        self._init_ui()
        self._refresh_display()

    # ── UI construction ───────────────────────────────────────────────────────

    def _init_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Pinned now-playing card ───────────────────────────────────────
        self._now_playing_card = _NowPlayingCard()
        root.addWidget(self._now_playing_card)

        # ── Thin divider ──────────────────────────────────────────────────
        divider = QFrame()
        divider.setFrameShape(QFrame.HLine)
        divider.setStyleSheet(f"background: {_C_BORDER}; border: none;")
        divider.setFixedHeight(1)
        root.addWidget(divider)

        # ── Section header row ────────────────────────────────────────────
        header = QWidget()
        header.setStyleSheet(f"background: {_C_SURFACE};")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(14, 6, 10, 6)
        header_layout.setSpacing(8)

        self._section_label = QLabel("UPCOMING")
        self._section_label.setStyleSheet(f"""
            color: {_C_DIM};
            font-size: 9px;
            font-weight: bold;
            letter-spacing: 0.12em;
            background: transparent;
        """)

        self._count_label = QLabel("")
        self._count_label.setStyleSheet(f"""
            color: {_C_DIM};
            font-size: 9px;
            background: transparent;
        """)

        self._status_label = QLabel("")
        self._status_label.setStyleSheet(
            f"color: {_C_ACCENT}; font-size: 10px; background: transparent;"
        )

        btn_style = f"""
            QPushButton {{
                background: transparent;
                color: {_C_DIM};
                border: 1px solid rgba(133,153,234,0.22);
                border-radius: 6px;
                padding: 3px 10px;
                font-size: 11px;
            }}
            QPushButton:hover {{
                color: {_C_GOLD};
                border-color: {_C_GOLD};
                background: rgba(234,214,133,0.08);
            }}
            QPushButton:pressed {{
                background: rgba(133,153,234,0.15);
                color: {_C_ACCENT};
                border-color: {_C_ACCENT};
            }}
        """

        self._shuffle_btn = QPushButton("⇌ Shuffle")
        self._shuffle_btn.setStyleSheet(btn_style)
        self._shuffle_btn.setToolTip("Shuffle upcoming tracks")
        self._shuffle_btn.clicked.connect(self._on_shuffle)

        self._clear_btn = QPushButton("✕ Clear")
        self._clear_btn.setStyleSheet(btn_style)
        self._clear_btn.setToolTip("Clear the entire queue")
        self._clear_btn.clicked.connect(self._on_clear)

        header_layout.addWidget(self._section_label)
        header_layout.addWidget(self._count_label)
        header_layout.addWidget(self._status_label)
        header_layout.addStretch()
        header_layout.addWidget(self._shuffle_btn)
        header_layout.addWidget(self._clear_btn)

        root.addWidget(header)

        # ── Upcoming list ─────────────────────────────────────────────────
        self._model = _QueueModel(self)

        self._list_view = QListView()
        self._list_view.setModel(self._model)
        self._list_view.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._list_view.setDragDropMode(QAbstractItemView.InternalMove)
        self._list_view.setDefaultDropAction(Qt.MoveAction)
        self._list_view.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._list_view.setUniformItemSizes(True)  # big performance win for long lists
        self._list_view.setSpacing(1)
        self._list_view.setContextMenuPolicy(Qt.CustomContextMenu)
        self._list_view.customContextMenuRequested.connect(self._show_context_menu)
        self._list_view.doubleClicked.connect(self._on_double_clicked)
        self._list_view.setStyleSheet(f"""
            QListView {{
                background: {_C_BASE};
                border: none;
                outline: none;
                font-family: "Cambria", "Georgia", serif;
                font-size: 12px;
                color: {_C_TEXT};
            }}
            QListView::item {{
                padding: 7px 14px;
                border-bottom: 1px solid rgba(30,31,43,0.6);
                border-radius: 0px;
                color: {_C_TEXT};
            }}
            QListView::item:hover {{
                background: rgba(234,133,153,0.1);
                color: {_C_PINK};
            }}
            QListView::item:selected {{
                background: rgba(133,153,234,0.2);
                color: {_C_TEXT};
            }}
            QScrollBar:vertical {{
                background: {_C_BASE};
                width: 6px;
                margin: 0;
            }}
            QScrollBar::handle:vertical {{
                background: rgba(133,153,234,0.35);
                border-radius: 3px;
                min-height: 30px;
            }}
            QScrollBar::handle:vertical:hover {{
                background: rgba(133,153,234,0.6);
            }}
            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical {{
                height: 0;
            }}
        """)

        # Detect scroll-near-bottom to trigger lazy load
        self._list_view.verticalScrollBar().valueChanged.connect(self._on_scroll)

        root.addWidget(self._list_view)

        # ── Remove-selected footer ────────────────────────────────────────
        footer = QWidget()
        footer.setStyleSheet(f"background: {_C_SURFACE};")
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(10, 6, 10, 6)
        footer_layout.setSpacing(8)

        self._remove_btn = QPushButton("Remove Selected")
        self._remove_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                color: {_C_DIM};
                border: 1px solid rgba(234,133,153,0.3);
                border-radius: 6px;
                padding: 3px 12px;
                font-size: 11px;
            }}
            QPushButton:hover {{
                color: {_C_PINK};
                border-color: {_C_PINK};
                background: rgba(234,133,153,0.08);
            }}
            QPushButton:pressed {{
                background: rgba(234,133,153,0.15);
            }}
        """)
        self._remove_btn.clicked.connect(self._remove_selected)

        footer_layout.addWidget(self._remove_btn)
        footer_layout.addStretch()

        root.addWidget(footer)

        # ── Context menu actions ──────────────────────────────────────────
        self._ctx_edit_single = QAction("Edit Track", self)
        self._ctx_edit_single.triggered.connect(self._edit_selected_track)

        self._ctx_edit_multi = QAction("Edit Selected Tracks", self)
        self._ctx_edit_multi.triggered.connect(self._edit_multiple_tracks)

        self._ctx_play_next = QAction("Play Next", self)
        self._ctx_play_next.triggered.connect(self._move_selected_to_next)

        self._ctx_remove = QAction("Remove from Queue", self)
        self._ctx_remove.triggered.connect(self._remove_selected)

    # ── Display refresh ───────────────────────────────────────────────────────

    def _refresh_display(self):
        """Rebuild the model from the current queue state."""
        qm = self.queue_manager

        # Update pinned card
        current = qm.get_current_track()
        self._now_playing_card.update_track(current)

        # Upcoming = queue[1:]  (everything after current)
        upcoming = qm.queue[1:]
        self._model.reset_data(upcoming)

        # Update count label
        total = len(upcoming)
        shown = self._model.rowCount()
        if total == 0:
            self._count_label.setText("")
        elif shown < total:
            self._count_label.setText(f"{shown:,} of {total:,} tracks")
        else:
            self._count_label.setText(f"{total:,} track{'s' if total != 1 else ''}")

    def refresh_queue(self):
        """Public slot — called by main_window on global refresh."""
        self._refresh_display()

    # ── Lazy loading ──────────────────────────────────────────────────────────

    def _on_scroll(self, value: int):
        """Load the next page when the user scrolls near the bottom."""
        bar = self._list_view.verticalScrollBar()
        if bar.maximum() == 0:
            return
        rows_from_bottom = (bar.maximum() - value) // max(1, bar.singleStep())
        if rows_from_bottom < SCROLL_THRESHOLD:
            if self._model.load_more():
                total = self._model.total_count()
                shown = self._model.rowCount()
                if shown < total:
                    self._count_label.setText(f"{shown:,} of {total:,} tracks")
                else:
                    self._count_label.setText(f"{total:,} tracks")

    # ── Bulk-add status ───────────────────────────────────────────────────────

    def _on_bulk_start(self, count: int):
        self._status_label.setText(f"Adding {count:,} tracks…")
        self._shuffle_btn.setEnabled(False)

    def _on_bulk_finish(self, count: int):
        self._status_label.setText("")
        self._shuffle_btn.setEnabled(True)

    # ── Interactions ──────────────────────────────────────────────────────────

    def _on_double_clicked(self, index: QModelIndex):
        """
        Double-clicking an upcoming track jumps to it.
        We advance the queue until that track is at the front, then play it.
        """
        track = self._model.track_at(index.row())
        if track is None:
            return

        file_path = getattr(track, "track_file_path", None)
        if file_path:
            # The track is at queue[row + 1] (because model is queue[1:])
            real_index = index.row() + 1
            # Move the clicked track to position 0 by re-inserting
            if 0 < real_index < len(self.queue_manager.queue):
                # Remove from its current position and insert at front
                self.queue_manager.queue.insert(
                    0, self.queue_manager.queue.pop(real_index)
                )
                self.queue_manager.queue_changed.emit()
            self.track_double_clicked.emit(Path(file_path))

    def _on_shuffle(self):
        self.queue_manager.shuffle_queue()

    def _on_clear(self):
        self.queue_manager.clear_queue()

    def _remove_selected(self):
        """Remove all selected rows from the queue."""
        indexes = self._list_view.selectedIndexes()
        if not indexes:
            return
        # Sort descending so we pop from the back first and indices stay valid
        rows = sorted({idx.row() for idx in indexes}, reverse=True)
        for row in rows:
            # row in the model is queue[row + 1]
            real_index = row + 1
            if 0 < real_index < len(self.queue_manager.queue):
                self.queue_manager.queue.pop(real_index)
        self.queue_manager.queue_changed.emit()
        self.queue_modified.emit()

    def _move_selected_to_next(self):
        """Move selected tracks to play immediately after the current track."""
        indexes = self._list_view.selectedIndexes()
        if not indexes:
            return
        rows = sorted({idx.row() for idx in indexes})
        tracks = [
            self.queue_manager.queue[r + 1]
            for r in rows
            if 0 < r + 1 < len(self.queue_manager.queue)
        ]
        # Remove from original positions (descending)
        for row in sorted(rows, reverse=True):
            real = row + 1
            if 0 < real < len(self.queue_manager.queue):
                self.queue_manager.queue.pop(real)
        # Insert at position 1 (right after current)
        for i, track in enumerate(tracks):
            self.queue_manager.queue.insert(1 + i, track)
        self.queue_manager.queue_changed.emit()
        self.queue_modified.emit()

    # ── Context menu ──────────────────────────────────────────────────────────

    def _show_context_menu(self, pos):
        indexes = self._list_view.selectedIndexes()
        if not indexes:
            return

        menu = QMenu(self)
        menu.setStyleSheet(f"""
            QMenu {{
                background: {_C_ELEVATED};
                border: 1px solid rgba(133,153,234,0.3);
                border-radius: 8px;
                padding: 4px;
                color: {_C_TEXT};
            }}
            QMenu::item {{
                padding: 7px 20px;
                border-radius: 4px;
            }}
            QMenu::item:selected {{
                background: rgba(133,153,234,0.2);
                color: {_C_TEXT};
            }}
            QMenu::separator {{
                height: 1px;
                background: rgba(133,153,234,0.15);
                margin: 3px 8px;
            }}
        """)

        count = len({idx.row() for idx in indexes})
        if count == 1:
            menu.addAction(self._ctx_edit_single)
        else:
            menu.addAction(self._ctx_edit_multi)
        menu.addAction(self._ctx_play_next)
        menu.addSeparator()
        menu.addAction(self._ctx_remove)
        menu.exec_(self._list_view.mapToGlobal(pos))

    # ── Track editing ─────────────────────────────────────────────────────────

    def _edit_selected_track(self):
        indexes = self._list_view.selectedIndexes()
        if not indexes:
            return
        row = indexes[0].row()
        track = self._model.track_at(row)
        if track:
            dlg = TrackEditDialog(track, self.controller, self)
            dlg.field_modified.connect(self._on_track_modified)
            dlg.exec_()

    def _edit_multiple_tracks(self):
        indexes = self._list_view.selectedIndexes()
        if not indexes:
            return
        rows = sorted({idx.row() for idx in indexes})
        tracks = [t for t in (self._model.track_at(r) for r in rows) if t]
        if len(tracks) >= 2:
            dlg = MultiTrackEditDialog(tracks, self.controller, self)
            dlg.field_modified.connect(self._on_track_modified)
            dlg.exec_()

    def _on_track_modified(self):
        self._refresh_display()
        self.queue_modified.emit()
