"""
track_view_filter.py — background search/filter worker for TrackView.
"""

from PySide6.QtCore import QThread, Signal

from src.db_mapping_tracks import TRACK_FIELDS

# How many rows to load into the Qt model in each batch.
LAZY_BATCH_SIZE = 200

# Search debounce delay in ms — prevents filtering on every keystroke.
SEARCH_DEBOUNCE_MS = 300

# Sentinel value for the "All Columns" search option.
SEARCH_ALL = "__all__"


class FilterWorker(QThread):
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
            if self._field_name == SEARCH_ALL:
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
