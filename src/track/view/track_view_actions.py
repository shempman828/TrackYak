"""Clipboard copy, drag support, queue helpers, and playback actions for TrackView."""

from pathlib import Path
import random

from PySide6.QtCore import QByteArray, QMimeData, Qt
from PySide6.QtGui import QDrag, QPainter, QPixmap
from PySide6.QtWidgets import QApplication

from src.foundation.censor import censor_text
from src.foundation.logger_config import logger

TRACK_ID_MIME = "application/x-track-id"

# Above this many tracks the queue is filled on a background thread.
_ASYNC_QUEUE_THRESHOLD = 500


def start_track_drag(source, tracks: list) -> None:
    """Start a copy drag that carries the ids of `tracks` as TRACK_ID_MIME."""
    track_ids = [t.track_id for t in tracks]
    if not track_ids:
        return
    mime = QMimeData()
    mime.setData(TRACK_ID_MIME, QByteArray(",".join(str(i) for i in track_ids).encode()))
    mime.setText(", ".join((t.track_name or "") for t in tracks[:50]))

    drag = QDrag(source)
    drag.setMimeData(mime)
    pixmap = QPixmap(200, 30)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setPen(Qt.white)
    painter.drawText(pixmap.rect(), Qt.AlignCenter, f"{len(track_ids)} track(s)")
    painter.end()
    drag.setPixmap(pixmap)
    drag.exec_(Qt.CopyAction)


def copy_selected_rows(table, model, columns: dict) -> None:
    """Copy the selected rows (visible columns, in visual order, with a header row) to the clipboard."""
    selected = table.selectionModel().selectedRows()
    if not selected:
        return
    header = table.horizontalHeader()
    visual_order = [header.logicalIndex(v) for v in range(header.count()) if not table.isColumnHidden(header.logicalIndex(v))]
    labels = list(columns.values())
    lines = ["\t".join(labels[i] for i in visual_order)]
    for index in sorted(selected, key=lambda i: i.row()):
        lines.append("\t".join((item.text() if (item := model.item(index.row(), col)) else "") for col in visual_order))
    QApplication.clipboard().setText("\n".join(lines))
    logger.debug(f"Copied {len(selected)} row(s) to clipboard")


def format_track_value(value, field_name: str) -> str:
    """Format one track field value for a table cell or CSV."""
    if value is None:
        return ""
    if field_name == "duration" and isinstance(value, (int, float)):
        m, s = divmod(int(value), 60)
        return f"{m}:{s:02d}"
    if field_name == "file_size" and isinstance(value, (int, float)):
        return f"{value / (1024 * 1024):.1f} MB"
    if field_name in ("track_name", "album_name", "lyrics"):
        return censor_text(str(value))
    return str(value)


class TrackViewActionsMixin:
    """Clipboard, drag-and-drop, queue, and double-click playback behavior."""

    # =========================================================================
    #  Clipboard — Ctrl+C copies selected rows with column headers
    # =========================================================================

    def _copy_selected_rows(self):
        """Copy the selected rows to the clipboard."""
        copy_selected_rows(self.table, self.model, self.columns)

    # =========================================================================
    #  Value helpers
    # =========================================================================

    def _get_artist_name(self, track) -> str:
        """Return the cached primary artist names of `track` (safe on worker threads)."""
        # Never lazy-load track relationships here: FilterWorker calls this off the main thread.
        return self._artist_name_cache.get(track.track_id, "Unknown Artist")

    def _format_value(self, value, field_name: str, field_config) -> str:
        """Format one field value for display."""
        return format_track_value(value, field_name)

    # =========================================================================
    #  Drag support
    # =========================================================================

    def startDrag(self, supported_actions):
        """Drag the selected tracks as TRACK_ID_MIME (installed on the table)."""
        start_track_drag(self.table, self._get_selected_track_objects())

    # =========================================================================
    #  Queue helpers
    # =========================================================================

    def _get_queue_manager(self):
        """Return the playback queue manager, or None."""
        queue_manager = getattr(self.controller, "queue_manager", None)
        if queue_manager is None and hasattr(self.controller, "mediaplayer"):
            queue_manager = getattr(self.controller.mediaplayer, "queue_manager", None)
        if queue_manager is None:
            logger.warning("Queue manager not found in controller")
        return queue_manager

    def _add_all_to_queue(self):
        """Shuffle the whole library into the queue."""
        qm = self._get_queue_manager()
        if not qm:
            return
        tracks = list(self._all_tracks)
        logger.info(f"Shuffling {len(tracks):,} tracks to queue")
        if len(tracks) > _ASYNC_QUEUE_THRESHOLD:
            qm.add_tracks_async(tracks, shuffle=True)
        else:
            random.shuffle(tracks)
            qm.add_tracks_to_queue(tracks)

    def _add_filtered_to_queue(self, shuffle: bool = False):
        """Add the listed tracks to the queue, in order or shuffled."""
        qm = self._get_queue_manager()
        if not qm:
            return
        tracks = list(self._visible_source())
        if shuffle:
            random.shuffle(tracks)
        if len(tracks) > _ASYNC_QUEUE_THRESHOLD:
            qm.add_tracks_async(tracks, shuffle=False)
        else:
            qm.add_tracks_to_queue(tracks)
        logger.info(f"{'Shuffled' if shuffle else 'Added'} {len(tracks):,} filtered tracks to queue")

    def _get_selected_track_objects(self) -> list:
        """Return the Track objects of the selected rows, in row order."""
        # Model rows map 1:1 onto the visible source list, so no DB query is needed.
        source = self._visible_source()
        rows = sorted({index.row() for index in self.table.selectionModel().selectedRows()})
        return [source[row] for row in rows if 0 <= row < len(source)]

    def add_selected_to_queue(self, insert_next: bool = False):
        """Add the selected tracks to the queue (or after the current track)."""
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
        logger.info(f"Added {len(tracks)} track(s) to queue (next={insert_next})")

    # =========================================================================
    #  Playback
    # =========================================================================

    def on_double_clicked(self, index):
        """Play the double-clicked track."""
        source = self._visible_source()
        row = index.row()
        if not 0 <= row < len(source):
            return
        file_path = getattr(source[row], "track_file_path", None)
        try:
            if file_path and self.controller.mediaplayer.load_track(Path(file_path)):
                self.player.play()
            else:
                logger.warning(f"Failed to load track: {file_path}")
        except (AttributeError, OSError, RuntimeError) as e:
            logger.error(f"Error playing track: {e}")
