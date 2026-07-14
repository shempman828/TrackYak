"""
track_view_actions.py — clipboard copy, drag support, queue helpers, and
playback actions for TrackView.
"""

import random
from pathlib import Path

from PySide6.QtCore import QByteArray, QMimeData, Qt
from PySide6.QtGui import QDrag, QPainter, QPixmap
from PySide6.QtWidgets import QApplication

from src.logger_config import logger


class TrackViewActionsMixin:
    """Clipboard, drag-and-drop, queue, and double-click playback behavior."""

    # =========================================================================
    #  Clipboard — Ctrl+C copies selected rows with column headers
    # =========================================================================

    def _copy_selected_rows(self):
        selected = self.table.selectionModel().selectedRows()
        if not selected:
            return

        list(self.columns.keys())
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
