"""
album_sorting.py

Sort-option data and sort-key logic for AlbumView.
"""

import random
from typing import ClassVar

from PySide6.QtCore import Qt

from src.core.logger_config import logger
from src.image.artwork_cache import get_artwork_cache


class AlbumSortingMixin:
    """
    Expects the host class to provide: self.sort_combo, self.filtered_albums,
    self._sort_criteria, self._sort_descending, self._random_keys,
    self._get_track_count(), self._get_album_artist_count(),
    self._apply_filters().
    """

    # Sort options grouped under a category header for a friendlier combo box.
    # Each group: (group_label, [(item_label, criteria_key, descending), ...])
    _SORT_GROUPS: ClassVar[list[tuple[str, list[tuple[str, str, bool]]]]] = [
        (
            "Name",
            [
                ("Title (A–Z)", "title", False),
                ("Title (Z–A)", "title", True),
                ("Artist (A–Z)", "artist", False),
                ("Artist (Z–A)", "artist", True),
            ],
        ),
        (
            "Release Date",
            [("Year (Newest First)", "year", True), ("Year (Oldest First)", "year", False)],
        ),
        (
            "Popularity",
            [
                ("Most Played", "play_count", True),
                ("Least Played", "play_count", False),
                ("Highest Rated", "rating", True),
                ("Lowest Rated", "rating", False),
            ],
        ),
        (
            "Size",
            [
                ("Track Count (Most First)", "track_count", True),
                ("Track Count (Fewest First)", "track_count", False),
                ("Album Artist Count (Most First)", "album_artist_count", True),
                ("Album Artist Count (Fewest First)", "album_artist_count", False),
                ("Duration (Longest First)", "length", True),
                ("Duration (Shortest First)", "length", False),
                ("Art Size (Largest First)", "art_dimensions", True),
                ("Art Size (Smallest First)", "art_dimensions", False),
            ],
        ),
        ("Random", [("Shuffle", "random", False)]),
    ]

    def _on_sort_changed(self, index: int):
        data = self.sort_combo.itemData(index, Qt.UserRole)
        if not data:
            # Group header row — not selectable, ignore.
            return
        criteria, descending = data
        if criteria == "random":
            # Re-roll the shuffle order each time Random is (re-)selected.
            self._random_keys = {}
        self._sort_criteria = criteria
        self._sort_descending = descending
        self._apply_filters()

    def _restore_sort_combo(self):
        """Set the sort combo to match the current internal sort state, without triggering a re-sort."""
        model = self.sort_combo.model()
        for i in range(model.rowCount()):
            data = model.item(i).data(Qt.UserRole)
            if data == (self._sort_criteria, self._sort_descending):
                self.sort_combo.blockSignals(True)
                self.sort_combo.setCurrentIndex(i)
                self.sort_combo.blockSignals(False)
                break

    def _sort_filtered(self):
        try:
            self.filtered_albums.sort(key=self._sort_key, reverse=self._sort_descending)
        except TypeError as e:
            logger.warning(f"Sorting failed: {e}")

    def _sort_key(self, album):
        try:
            c = self._sort_criteria

            if c == "title":
                return getattr(album, "album_name", "").lower()

            if c == "artist":
                artists = (
                    getattr(album, "album_artists", None) or getattr(album, "artists", None) or []
                )
                if artists:
                    first = artists[0]
                    if hasattr(first, "artist_name"):
                        return first.artist_name.lower()
                    if isinstance(first, str):
                        return first.lower()
                    if isinstance(first, dict):
                        return (first.get("artist_name") or first.get("name") or "").lower()
                return ""

            if c == "year":
                y = getattr(album, "release_year", None)
                try:
                    return int(y) if y else 0
                except (TypeError, ValueError):
                    return 0

            elif c == "track_count":
                return self._get_track_count(album)

            elif c == "album_artist_count":
                return self._get_album_artist_count(album)

            elif c == "play_count":
                return getattr(album, "total_plays", 0) or 0

            elif c == "rating":
                return getattr(album, "average_rating", 0) or getattr(album, "user_rating", 0) or 0

            elif c == "length":
                return getattr(album, "total_duration", 0) or 0

            elif c == "random":
                key = getattr(album, "album_id", None)
                if key is None:
                    key = id(album)
                return self._random_keys.setdefault(key, random.random())

            elif c == "art_dimensions":
                # Sort by pixel area of the front cover image. Uses the
                # non-blocking peek - any album whose cache row is missing
                # or stale sorts as 0 for now and gets queued for the
                # background worker by the caller (_apply_filters), which
                # re-sorts once its real dimensions are known.
                cache = get_artwork_cache()
                _, dims = cache.peek_dimensions(album, "front") if cache else (True, None)
                if dims:
                    return dims[0] * dims[1]
                return 0

            return getattr(album, "album_name", "").lower()

        except Exception:
            logger.exception(
                f"Sort key failed for album {getattr(album, 'album_name', '?')} "
                f"(criteria={self._sort_criteria})"
            )
            return ""
