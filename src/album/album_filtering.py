"""
album_filtering.py

Filtering pipeline for AlbumView: filter-widget snapshot, per-album
predicate, the cold-cache Art-filter background-worker pipeline, and
filter-state persistence.
"""

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QComboBox

from src.album.album_art_worker import ArtCacheWorker
from src.core.config_setup import app_config
from src.image.artwork_cache import get_artwork_cache


class AlbumFilteringMixin:
    """
    Expects the host class to provide: self.search_bar, self.year_from,
    self.year_to, self.min_tracks, self.incomplete_combo, self.fixed_combo,
    self.art_combo, self.stats_label, self.all_albums, self.filtered_albums,
    self.display_count, self.load_chunk, self._sort_criteria,
    self._art_worker, self._art_filter_generation, self._art_batch,
    self._art_needs_resort, self._art_batch_timer, self._filter_save_timer,
    self._search_timer, self._get_track_count(), self._get_artist_names(),
    self._get_genre_names(), self._sort_filtered(), self._refresh_album_widgets(),
    self._check_viewport_fill(), self._restore_sort_combo(), and to be a
    QWidget subclass.
    """

    def _on_search_changed(self, text: str):
        # Debounce: only filter after typing pauses
        self._search_timer.start()

    def _get_current_filter_params(self):
        """Snapshot the filter widget state, plus the artwork cache handle
        needed to evaluate the Art filter/sort. Shared by all callers that
        need to test albums (in bulk or individually) against the currently
        active filters.
        """
        art_mode = self.art_combo.currentText()
        needs_art_cache = art_mode != "Any" or self._sort_criteria == "art_dimensions"
        return {
            "text": self.search_bar.text().strip().lower(),
            "year_from": self.year_from.value(),
            "year_to": self.year_to.value(),
            "min_tracks": self.min_tracks.value(),
            "incomplete_mode": self.incomplete_combo.currentText(),
            "fixed_mode": self.fixed_combo.currentText(),
            "art_mode": art_mode,
            "art_generation": self._art_filter_generation,
            "art_cache": get_artwork_cache() if needs_art_cache else None,
        }

    def _album_matches_filters(self, album, params):
        """Test a single album against the filter snapshot from
        _get_current_filter_params().

        Returns True (matches), False (excluded), or None (Art filter can't
        be resolved synchronously - caller should treat it as pending/unknown
        rather than excluding it).
        """
        # ── Text search ──────────────────────────────────────────────
        text = params["text"]
        if text:
            title = getattr(album, "album_name", "").lower()
            year_str = str(getattr(album, "release_year", "")).lower()
            artist_names = self._get_artist_names(album)
            genre_names = self._get_genre_names(album)

            if not (
                text in title
                or text in year_str
                or any(text in a for a in artist_names)
                or any(text in g for g in genre_names)
            ):
                return False

        # ── Year range ───────────────────────────────────────────────
        year_from = params["year_from"]
        year_to = params["year_to"]
        album_year = getattr(album, "release_year", None)
        if album_year:
            try:
                yr = int(album_year)
                if year_from > 0 and yr < year_from:
                    return False
                if year_to > 0 and yr > year_to:
                    return False
            except (TypeError, ValueError):
                pass
        else:
            # If we have a strict year filter and album has no year, skip it
            if year_from > 0 or year_to > 0:
                return False

        # ── Min track count ──────────────────────────────────────────
        min_tracks = params["min_tracks"]
        if min_tracks > 0 and self._get_track_count(album) < min_tracks:
            return False

        # ── Possibly Incomplete filter ────────────────────────────────
        incomplete_mode = params["incomplete_mode"]
        if incomplete_mode != "Any":
            is_incomplete = bool(getattr(album, "possibly_incomplete", False))
            if incomplete_mode == "Possibly Incomplete" and not is_incomplete:
                return False
            if incomplete_mode == "Likely Complete" and is_incomplete:
                return False

        # ── Metadata review-tier filter ─────────────────────────────────
        fixed_mode = params["fixed_mode"]
        if fixed_mode != "Any":
            first_pass = bool(getattr(album, "first_pass", False))
            second_pass = bool(getattr(album, "second_pass", False))
            if fixed_mode == "Not Started" and first_pass:
                return False
            if fixed_mode == "First Pass" and not (first_pass and not second_pass):
                return False
            if fixed_mode == "Second Pass" and not second_pass:
                return False

        # ── Album Art filter ──────────────────────────────────────────
        art_mode = params["art_mode"]
        if art_mode != "Any":
            art_cache = params["art_cache"]
            if art_cache is None:
                has_art = False
            else:
                known = art_cache.peek_has_art(album, "front")
                if known is None:
                    # Cache miss/stale - resolving it means reading and
                    # decoding the audio file, which is too slow to do
                    # inline. Caller queues it for the background worker
                    # instead of blocking here.
                    return None
                has_art = known
            if art_mode == "No Art" and has_art:
                return False
            if art_mode == "Has Art" and not has_art:
                return False

        return True

    def _compute_filtered_results(self):
        """Run all active filters (search text, year range, track count,
        possibly_incomplete, first_pass/second_pass, art) against self.all_albums.

        Returns (results, pending_art, art_mode, art_generation). Shared by
        _apply_filters and _apply_filters_preserve_scroll, which differ only
        in how they handle display_count/scroll position afterwards.
        """
        params = self._get_current_filter_params()
        art_cache = params["art_cache"]

        results = []
        pending_art = []
        for album in self.all_albums:
            verdict = self._album_matches_filters(album, params)
            if verdict is None:
                pending_art.append(album)
                continue
            if verdict:
                results.append(album)

        # ── Art Size sort ──────────────────────────────────────────────────
        # Sorting by pixel area needs each album's dimensions, which have the
        # same cold-cache cost as the Art filter above - queue anything
        # unresolved rather than blocking the sort.
        if self._sort_criteria == "art_dimensions" and art_cache is not None:
            for album in results:
                known, _ = art_cache.peek_dimensions(album, "front")
                if not known:
                    pending_art.append(album)

        return results, pending_art, params["art_mode"], params["art_generation"]

    def _apply_filters(self):
        """Apply all active filters and rebuild the grid from the top."""
        self._cancel_art_worker()

        results, pending_art, art_mode, art_generation = self._compute_filtered_results()

        self.filtered_albums = results
        self._sort_filtered()
        self._update_stats()

        self.display_count = self.load_chunk
        self._refresh_album_widgets()
        QTimer.singleShot(100, self._check_viewport_fill)

        if pending_art:
            self._start_art_worker(pending_art, art_mode, self._sort_criteria, art_generation)

        self._filter_save_timer.start()

    def _cancel_art_worker(self):
        """Stop any in-flight background art-cache resolution and
        invalidate its results, since a new filter/sort run supersedes it.

        `wait()` here only blocks on however long is left of the single
        file the worker is mid-extraction on - far cheaper than the old
        behavior of blocking the filter pass on every pending album.
        Bumping the generation counter also guards against a `resolved`
        signal that was already queued on the event loop before
        request_cancel() took effect from being applied to the new filter.
        """
        if self._art_worker is not None and self._art_worker.isRunning():
            self._art_worker.request_cancel()
            self._art_worker.wait()
        self._art_worker = None
        self._art_batch_timer.stop()
        self._art_batch.clear()
        self._art_needs_resort = False
        self._art_filter_generation += 1

    def _start_art_worker(
        self, pending_albums: list, art_mode: str, sort_criteria: str, generation: int
    ):
        cache = get_artwork_cache()
        if cache is None:
            return

        worker = ArtCacheWorker(pending_albums, cache, "front")
        worker.resolved.connect(
            lambda album_id, gen=generation, mode=art_mode, sort_c=sort_criteria: (
                self._on_art_resolved(album_id, gen, mode, sort_c)
            )
        )
        self._art_worker = worker
        worker.start()

    def _on_art_resolved(self, album_id: int, generation: int, art_mode: str, sort_criteria: str):
        if generation != self._art_filter_generation:
            return  # A newer filter/sort run has already superseded this one.

        cache = get_artwork_cache()
        if cache is None:
            return

        already_shown = any(getattr(a, "album_id", None) == album_id for a in self.filtered_albums)

        if not already_shown:
            # This album was excluded by the Art filter while its status was
            # still unknown - now that the worker has warmed its cache row,
            # re-check whether it actually belongs.
            if art_mode == "Any":
                return
            album = next(
                (a for a in self.all_albums if getattr(a, "album_id", None) == album_id), None
            )
            if album is None:
                return
            has_art = bool(cache.peek_has_art(album, "front"))
            matches = (art_mode == "No Art" and not has_art) or (art_mode == "Has Art" and has_art)
            if not matches:
                return
            self._art_batch.append(album)
        elif sort_criteria != "art_dimensions":
            # Already in the grid and nothing about its sort position needs
            # updating - the resolved dimensions are irrelevant right now.
            return

        self._art_needs_resort = True
        self._art_batch_timer.start()

    def _flush_art_batch(self):
        """Apply everything the background worker has resolved since the
        last flush: add newly-matching albums to the grid and/or re-sort
        for updated Art Size positions. Batched on a timer rather than
        applied one album at a time so a cold cache doesn't trigger a full
        re-sort + widget rebuild per album."""
        if not self._art_batch and not self._art_needs_resort:
            return
        prev_visible_ids = [
            getattr(a, "album_id", None) for a in self.filtered_albums[: self.display_count]
        ]
        if self._art_batch:
            self.filtered_albums.extend(self._art_batch)
            self._art_batch.clear()
        self._art_needs_resort = False
        self._sort_filtered()
        self._update_stats()
        new_visible_ids = [
            getattr(a, "album_id", None) for a in self.filtered_albums[: self.display_count]
        ]
        if new_visible_ids == prev_visible_ids:
            # Nothing about the currently visible slice moved - e.g. the
            # newly-resolved album sorts below the display cutoff. Its
            # widget will appear on its own via _append_more_album_widgets
            # once the user scrolls to it, so there's nothing to redraw.
            return
        self._refresh_album_widgets()
        QTimer.singleShot(100, self._check_viewport_fill)

    def _clear_filters(self):
        self.search_bar.clear()
        self.year_from.setValue(0)
        self.year_to.setValue(0)
        self.min_tracks.setValue(0)
        self.incomplete_combo.setCurrentIndex(0)
        self.fixed_combo.setCurrentIndex(0)
        self.art_combo.setCurrentIndex(0)
        self._sort_criteria = "title"
        self._sort_descending = False
        self._restore_sort_combo()

    def _get_filter_state(self) -> dict:
        """Snapshot the filter widgets' values for persistence."""
        return {
            "search": self.search_bar.text(),
            "year_from": self.year_from.value(),
            "year_to": self.year_to.value(),
            "min_tracks": self.min_tracks.value(),
            "incomplete_mode": self.incomplete_combo.currentText(),
            "fixed_mode": self.fixed_combo.currentText(),
            "art_mode": self.art_combo.currentText(),
        }

    def _save_filter_state(self):
        app_config.set_album_view_filters(self._get_filter_state())
        app_config.save()

    def _set_combo_text(self, combo: QComboBox, text: str | None):
        if not text:
            return
        idx = combo.findText(text)
        if idx >= 0:
            combo.blockSignals(True)
            combo.setCurrentIndex(idx)
            combo.blockSignals(False)

    def _restore_filter_state(self):
        """Restore filter widget values persisted from the previous session."""
        state = app_config.get_album_view_filters()
        if not state:
            return

        self.search_bar.blockSignals(True)
        self.search_bar.setText(state.get("search", ""))
        self.search_bar.blockSignals(False)

        self.year_from.blockSignals(True)
        self.year_from.setValue(state.get("year_from", 0))
        self.year_from.blockSignals(False)

        self.year_to.blockSignals(True)
        self.year_to.setValue(state.get("year_to", 0))
        self.year_to.blockSignals(False)

        self.min_tracks.blockSignals(True)
        self.min_tracks.setValue(state.get("min_tracks", 0))
        self.min_tracks.blockSignals(False)

        self._set_combo_text(self.incomplete_combo, state.get("incomplete_mode"))
        self._set_combo_text(self.fixed_combo, state.get("fixed_mode"))
        self._set_combo_text(self.art_combo, state.get("art_mode"))

    def _update_stats(self):
        total = len(self.all_albums)
        showing = len(self.filtered_albums)
        if showing == total:
            self.stats_label.setText(f"{total} album{'s' if total != 1 else ''}")
        else:
            self.stats_label.setText(f"{showing} of {total} albums")
