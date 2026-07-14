"""
track_view_data.py — lazy DB loading, batch pagination, sorting, and status
text for TrackView.
"""

from PySide6.QtCore import Qt
from PySide6.QtGui import QStandardItem

from src.logger_config import logger
from src.track_view_filter import LAZY_BATCH_SIZE


class TrackViewDataMixin:
    """Lazy loading of tracks from the DB into the Qt model, plus sorting."""

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

    def _on_header_clicked(self, logical_index: int):
        """
        Sort the backing track list by the clicked column and reload from scratch.

        - Clicking a new column sorts ascending.
        - Clicking the same column again flips between ascending and descending.
        - If a search/filter is active we sort only the filtered results.
        - Sorting always resets lazy loading so you see the top of the sorted list first.
        """
        if self._sort_column_index == logical_index:
            self._sort_ascending = not self._sort_ascending
        else:
            self._sort_column_index = logical_index
            self._sort_ascending = True

        header = self.table.horizontalHeader()
        header.setSortIndicator(
            logical_index,
            Qt.AscendingOrder if self._sort_ascending else Qt.DescendingOrder,
        )

        # Pick the right source list: filtered results or everything
        if self._filter_active:
            self._filtered_tracks = self._sorted(self._filtered_tracks, logical_index)
        else:
            self._all_tracks = self._sorted(self._all_tracks, logical_index)

        # Reset lazy loading and repopulate from the now-sorted list
        self._loaded_count = 0
        self.model.setRowCount(0)
        source = self._filtered_tracks if self._filter_active else self._all_tracks
        self._append_next_batch(source)
        self._update_status()

    def _sorted(self, track_list: list, logical_index: int) -> list:
        """
        Return a new list sorted by the column at `logical_index`.

        Uses the same numeric UserRole data that _append_next_batch stores,
        so numeric fields (duration, file_size, …) sort as numbers.
        """
        column_keys = list(self.columns.keys())
        if logical_index < 0 or logical_index >= len(column_keys):
            return track_list

        field_name = column_keys[logical_index]

        def sort_key(track):
            if field_name == "artist_name":
                raw = self._get_artist_name(track)
            else:
                raw = getattr(track, field_name, None)

            if raw is None:
                # Put missing values at the end regardless of direction
                return (1, "")
            if isinstance(raw, (int, float)):
                return (0, raw)
            return (0, str(raw).lower())

        return sorted(track_list, key=sort_key, reverse=not self._sort_ascending)

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
    #  Fill model fully (used by _add_filtered_to_queue edge case)
    # =========================================================================

    def _fill_model_completely(self):
        source = self._filtered_tracks if self._filter_active else self._all_tracks
        while self._loaded_count < len(source):
            self._append_next_batch(source)
