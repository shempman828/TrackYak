"""track_view_search.py — background search/filter application for TrackView."""

from src.foundation.logger_config import logger
from src.track.view.track_view_filter import FilterWorker


class TrackViewSearchMixin:
    """Kicks off and consumes results from the background FilterWorker."""

    def _on_search_text_changed(self, text: str):
        """
        Search only runs on Enter (see _build_toolbar's returnPressed wiring).
        The exception is clearing the field, which restores the full list
        immediately rather than leaving a stale filtered view on screen.
        """
        if not text.strip():
            self._apply_search_filter()

    def _apply_search_filter(self):
        """Kicks off a background worker to filter tracks without blocking the UI."""
        search_text = self.search_bar.text().strip().lower()

        # Stop any already-running worker first — a cancelled worker emits
        # nothing, so its stale results can't overwrite what comes next.
        if self._filter_worker and self._filter_worker.isRunning():
            self._filter_worker.request_cancel()
            self._filter_worker.wait()

        if not search_text:
            # Nothing typed — restore the full list immediately
            self._filter_active = False
            self._filtered_tracks = []
            self._loaded_count = 0
            self.model.setRowCount(0)
            self._append_next_batch(self._all_tracks)
            self._update_status()
            return

        # Show the search is in progress; _on_filter_done replaces this
        # with the match count once results arrive.
        self.status_label.setText("Searching…")

        field_name = self._search_field_name

        self._filter_worker = FilterWorker(self._all_tracks, search_text, field_name, self._get_artist_name, self._format_value, self._field_value)
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
