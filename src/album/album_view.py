"""UI view for albums in music library"""

import random
import sqlite3
from typing import ClassVar

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)
from sqlalchemy.exc import SQLAlchemyError

from src.album.album_art_worker import ArtCacheWorker
from src.album.album_context_menu import AlbumContextMenuMixin
from src.album.album_flowlayout import FlowLayout
from src.album.base_album_edit import AlbumEditor
from src.album.base_album_widget import AlbumWidget
from src.common.layout_utils import clear_layout
from src.core.logger_config import logger
from src.image.artwork_cache import get_artwork_cache

# ---------------------------------------------------------------------------
# Main view
# ---------------------------------------------------------------------------


class AlbumView(AlbumContextMenuMixin, QWidget):
    """Enhanced album view with responsive grid layout, interactive controls,
    search/filter functionality, and lazy loading.
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
            [
                ("Year (Newest First)", "year", True),
                ("Year (Oldest First)", "year", False),
            ],
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
        (
            "Random",
            [
                ("Shuffle", "random", False),
            ],
        ),
    ]

    def __init__(self, controller):
        super().__init__()
        self.controller = controller
        self.current_size = 200

        self.all_albums: list = []
        self.filtered_albums: list = []
        self.display_count = 20
        self.load_chunk = 20

        # Sorting state – defaults to "Title (A–Z)"
        self._sort_criteria = "title"
        self._sort_descending = False
        # Stable per-album random keys, used by the "Random" sort so the
        # shuffle order doesn't change on every filter/search re-apply —
        # only when the user (re-)selects the Random option.
        self._random_keys: dict = {}

        # Filter row visibility
        self._filter_row_visible = True

        # Debounce timer for cover size slider
        self._resize_timer = QTimer(self)
        self._resize_timer.setSingleShot(True)
        self._resize_timer.setInterval(120)
        self._resize_timer.timeout.connect(self._do_resize_art)

        # Debounce timer for search bar
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(180)
        self._search_timer.timeout.connect(self._apply_filters)

        # Background resolution of album-art cache misses for the Art
        # filter (see _cancel_art_worker/_start_art_worker) — lets albums
        # with an uncached/stale artwork_cache row populate into the grid
        # as they're discovered instead of blocking the filter pass on
        # every miss.
        self._art_worker: ArtCacheWorker | None = None
        self._art_filter_generation = 0
        self._art_batch: list = []
        self._art_needs_resort = False
        self._art_batch_timer = QTimer(self)
        self._art_batch_timer.setSingleShot(True)
        self._art_batch_timer.setInterval(150)
        self._art_batch_timer.timeout.connect(self._flush_art_batch)

        self._init_ui()
        self.load_albums()

    # =========================================================================
    # UI Construction
    # =========================================================================

    def _init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(8)

        main_layout.addLayout(self._build_top_controls())

        # Filter bar is a QWidget so we can show/hide it cleanly
        self._filter_row_widget = QWidget()
        filter_layout = self._build_filter_bar()
        self._filter_row_widget.setLayout(filter_layout)
        main_layout.addWidget(self._filter_row_widget)

        main_layout.addWidget(self._build_scroll_area())

    def _build_top_controls(self) -> QHBoxLayout:
        row = QHBoxLayout()

        # Search
        self.search_bar = QLineEdit()
        self.search_bar.setPlaceholderText("Search albums, artists, year…")
        self.search_bar.setClearButtonEnabled(True)
        self.search_bar.textChanged.connect(self._on_search_changed)
        row.addWidget(self.search_bar, stretch=3)

        # Sort combo — grouped under bold, unselectable category headers
        row.addWidget(QLabel("Sort:"))
        self.sort_combo = QComboBox()
        model = QStandardItemModel(self.sort_combo)
        for group_label, options in self._SORT_GROUPS:
            header = QStandardItem(group_label)
            header.setFlags(Qt.NoItemFlags)
            font = header.font()
            font.setBold(True)
            header.setFont(font)
            model.appendRow(header)
            for label, criteria, descending in options:
                item = QStandardItem(f"    {label}")
                item.setData((criteria, descending), Qt.UserRole)
                model.appendRow(item)
        self.sort_combo.setModel(model)
        self.sort_combo.currentIndexChanged.connect(self._on_sort_changed)
        row.addWidget(self.sort_combo, stretch=2)

        # Cover size slider
        row.addWidget(QLabel("Size:"))
        self.size_slider = QSlider(Qt.Horizontal)
        self.size_slider.setRange(100, 400)
        self.size_slider.setValue(self.current_size)
        self.size_slider.setMaximumWidth(120)
        self.size_slider.valueChanged.connect(self._resize_art)
        row.addWidget(self.size_slider)

        # Filter row toggle
        self._filter_toggle_btn = QPushButton("▾ Filters")
        self._filter_toggle_btn.setCheckable(True)
        self._filter_toggle_btn.setChecked(True)
        self._filter_toggle_btn.setToolTip("Show/hide filter row")
        self._filter_toggle_btn.toggled.connect(self._toggle_filter_row)
        row.addWidget(self._filter_toggle_btn)

        return row

    def _build_filter_bar(self) -> QHBoxLayout:
        """Secondary row with advanced filter chips."""
        row = QHBoxLayout()
        row.setSpacing(12)
        row.setContentsMargins(0, 0, 0, 0)

        # Year range
        row.addWidget(QLabel("Year:"))
        self.year_from = _AnySpinBox()
        self.year_from.setFixedWidth(70)
        self.year_from.valueChanged.connect(self._apply_filters)
        row.addWidget(self.year_from)
        row.addWidget(QLabel("–"))
        self.year_to = _AnySpinBox()
        self.year_to.setFixedWidth(70)
        self.year_to.valueChanged.connect(self._apply_filters)
        row.addWidget(self.year_to)

        # Min track count
        row.addWidget(QLabel("Min tracks:"))
        self.min_tracks = _AnySpinBox()
        self.min_tracks.setFixedWidth(65)
        self.min_tracks.valueChanged.connect(self._apply_filters)
        row.addWidget(self.min_tracks)

        # Possibly Incomplete filter
        row.addWidget(QLabel("Completeness:"))
        self.incomplete_combo = QComboBox()
        self.incomplete_combo.addItems([
            "Any",
            "Possibly Incomplete",
            "Likely Complete",
        ])
        self.incomplete_combo.currentIndexChanged.connect(self._apply_filters)
        row.addWidget(self.incomplete_combo)

        # Is Fixed filter
        row.addWidget(QLabel("Fixed:"))
        self.fixed_combo = QComboBox()
        self.fixed_combo.addItems(["Any", "Fixed Only", "Not Fixed"])
        self.fixed_combo.currentIndexChanged.connect(self._apply_filters)
        row.addWidget(self.fixed_combo)

        # Album Art filter
        row.addWidget(QLabel("Art:"))
        self.art_combo = QComboBox()
        self.art_combo.addItems(["Any", "No Art", "Has Art"])
        self.art_combo.currentIndexChanged.connect(self._apply_filters)
        row.addWidget(self.art_combo)

        # Stats label
        self.stats_label = QLabel()
        self.stats_label.setProperty("textRole", "muted")
        row.addWidget(self.stats_label)

        row.addStretch()

        # Clear filters
        clear_btn = QPushButton("Clear Filters")
        clear_btn.setFlat(True)
        clear_btn.setToolTip("Reset all filters")
        clear_btn.clicked.connect(self._clear_filters)
        row.addWidget(clear_btn)

        return row

    def _build_scroll_area(self) -> QScrollArea:
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_content = QWidget()
        self.grid_layout = FlowLayout(self.scroll_content)
        self.scroll_content.setLayout(self.grid_layout)
        self.scroll_area.setWidget(self.scroll_content)

        self.scroll_area.verticalScrollBar().valueChanged.connect(
            self._check_scroll_position
        )
        self.scroll_area.setContextMenuPolicy(Qt.CustomContextMenu)
        self.scroll_area.customContextMenuRequested.connect(self._show_context_menu)

        return self.scroll_area

    def _toggle_filter_row(self, visible: bool):
        self._filter_row_widget.setVisible(visible)
        self._filter_toggle_btn.setText("▾ Filters" if visible else "▸ Filters")

    # =========================================================================
    # Loading & Filtering
    # =========================================================================

    def load_albums(self):
        """Load all albums from the controller and refresh the grid."""
        try:
            self.all_albums = self.controller.get.get_all_entities("Album") or []
            self._restore_sort_combo()
            self._apply_filters()
        except (SQLAlchemyError, sqlite3.Error, AttributeError) as e:
            logger.exception("Failed to load albums")
            QMessageBox.critical(self, "Error", f"Failed to load albums:\n{e}")

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

        # ── Is Fixed filter ───────────────────────────────────────────
        fixed_mode = params["fixed_mode"]
        if fixed_mode != "Any":
            is_fixed = bool(getattr(album, "is_fixed", False))
            if fixed_mode == "Fixed Only" and not is_fixed:
                return False
            if fixed_mode == "Not Fixed" and is_fixed:
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
        possibly_incomplete, is_fixed, art) against self.all_albums.

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

        results, pending_art, art_mode, art_generation = (
            self._compute_filtered_results()
        )

        self.filtered_albums = results
        self._sort_filtered()
        self._update_stats()

        self.display_count = self.load_chunk
        self._refresh_album_widgets()
        QTimer.singleShot(100, self._check_viewport_fill)

        if pending_art:
            self._start_art_worker(
                pending_art, art_mode, self._sort_criteria, art_generation
            )

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

    def _on_art_resolved(
        self, album_id: int, generation: int, art_mode: str, sort_criteria: str
    ):
        if generation != self._art_filter_generation:
            return  # A newer filter/sort run has already superseded this one.

        cache = get_artwork_cache()
        if cache is None:
            return

        already_shown = any(
            getattr(a, "album_id", None) == album_id for a in self.filtered_albums
        )

        if not already_shown:
            # This album was excluded by the Art filter while its status was
            # still unknown - now that the worker has warmed its cache row,
            # re-check whether it actually belongs.
            if art_mode == "Any":
                return
            album = next(
                (
                    a
                    for a in self.all_albums
                    if getattr(a, "album_id", None) == album_id
                ),
                None,
            )
            if album is None:
                return
            has_art = bool(cache.peek_has_art(album, "front"))
            matches = (art_mode == "No Art" and not has_art) or (
                art_mode == "Has Art" and has_art
            )
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
            getattr(a, "album_id", None)
            for a in self.filtered_albums[: self.display_count]
        ]
        if self._art_batch:
            self.filtered_albums.extend(self._art_batch)
            self._art_batch.clear()
        self._art_needs_resort = False
        self._sort_filtered()
        self._update_stats()
        new_visible_ids = [
            getattr(a, "album_id", None)
            for a in self.filtered_albums[: self.display_count]
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

    def _update_stats(self):
        total = len(self.all_albums)
        showing = len(self.filtered_albums)
        if showing == total:
            self.stats_label.setText(f"{total} album{'s' if total != 1 else ''}")
        else:
            self.stats_label.setText(f"{showing} of {total} albums")

    # =========================================================================
    # Sorting
    # =========================================================================

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
            self.filtered_albums.sort(
                key=self._sort_key,
                reverse=self._sort_descending,
            )
        except TypeError as e:
            logger.warning(f"Sorting failed: {e}")

    def _sort_key(self, album):
        try:
            c = self._sort_criteria

            if c == "title":
                return getattr(album, "album_name", "").lower()

            elif c == "artist":
                artists = (
                    getattr(album, "album_artists", None)
                    or getattr(album, "artists", None)
                    or []
                )
                if artists:
                    first = artists[0]
                    if hasattr(first, "artist_name"):
                        return first.artist_name.lower()
                    if isinstance(first, str):
                        return first.lower()
                    if isinstance(first, dict):
                        return (
                            first.get("artist_name") or first.get("name") or ""
                        ).lower()
                return ""

            elif c == "year":
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
                return (
                    getattr(album, "average_rating", 0)
                    or getattr(album, "user_rating", 0)
                    or 0
                )

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
                _, dims = (
                    cache.peek_dimensions(album, "front") if cache else (True, None)
                )
                if dims:
                    return dims[0] * dims[1]
                return 0

            return getattr(album, "album_name", "").lower()

        except Exception:  # ruff: ignore[blind-except]
            logger.exception(
                f"Sort key failed for album {getattr(album, 'album_name', '?')} "
                f"(criteria={self._sort_criteria})"
            )
            return ""

    # =========================================================================
    # Widget Grid
    # =========================================================================

    def _refresh_album_widgets(self):
        """Rebuild the grid from scratch up to display_count."""
        clear_layout(self.grid_layout)
        for album in self.filtered_albums[: self.display_count]:
            self._add_album_widget(album)
        self.scroll_content.updateGeometry()
        self.grid_layout.update()

    def _append_more_album_widgets(self):
        prev = self.display_count
        self.display_count = min(
            self.display_count + self.load_chunk, len(self.filtered_albums)
        )
        for album in self.filtered_albums[prev : self.display_count]:
            self._add_album_widget(album)
        self.grid_layout.update()

    def _add_album_widget(self, album):
        widget = AlbumWidget(album, self.current_size)
        widget.clicked.connect(self._on_album_clicked)
        self.grid_layout.addWidget(widget)
        # A freshly reparented widget's "show" is deferred to the next
        # event-loop turn, so isVisible() is still False if a layout pass
        # (e.g. the forced repaint() on first view switch) runs synchronously
        # right after -- FlowLayout skips positioning invisible widgets,
        # leaving it stuck at its default geometry overlapping slot 0. Force
        # it visible now so the next layout pass actually places it.
        widget.show()

    def _check_scroll_position(self, value: int):
        bar = self.scroll_area.verticalScrollBar()
        if value >= bar.maximum() - 50 and self.display_count < len(
            self.filtered_albums
        ):
            self._append_more_album_widgets()

    def _check_viewport_fill(self):
        bar = self.scroll_area.verticalScrollBar()
        if bar.maximum() == 0 and self.display_count < len(self.filtered_albums):
            self._append_more_album_widgets()
            QTimer.singleShot(100, self._check_viewport_fill)

    def _resize_art(self, size: int):
        self.current_size = size
        self._resize_timer.start()  # restart timer on each tick

    def _do_resize_art(self):
        for i in range(self.grid_layout.count()):
            widget = self.grid_layout.itemAt(i).widget()
            if widget is not None:
                widget.update_size(self.current_size)
        self.grid_layout.update()

    # =========================================================================
    # Album Detail
    # =========================================================================

    def _on_album_clicked(self, album):
        self._show_album_details(album)

    def _show_album_details(self, album):
        """Open the AlbumEditor directly as a standalone dialog."""
        try:
            fresh = self.controller.get.get_entity_object(
                "Album", album_id=album.album_id
            )
            if fresh:
                album = fresh
        except SQLAlchemyError as e:
            logger.warning(
                f"Failed to refresh album {album.album_id} before editing: {e}"
            )

        dialog = AlbumEditor(self.controller, album)

        def _on_editor_closed(_):
            self._patch_album_after_edit(album.album_id)

        dialog.finished.connect(_on_editor_closed)
        dialog.exec()

    def _patch_album_after_edit(self, album_id):
        """Refresh a single album in-place after editing.

        Fetches a fresh copy from the DB, swaps it into all_albums and
        filtered_albums, then re-sorts and repaints only the affected widget —
        preserving scroll position and lazy-load progress entirely.
        If the album is no longer retrievable (deleted externally, etc.) we
        fall back to a full reload.
        """
        try:
            fresh = self.controller.get.get_entity_object("Album", album_id=album_id)
        except SQLAlchemyError as e:
            logger.warning(f"Failed to refresh album {album_id} after edit: {e}")
            fresh = None

        if fresh is None:
            # Album gone — full reload is the safest fallback
            self.load_albums()
            return

        # --- Patch all_albums list ---
        for i, a in enumerate(self.all_albums):
            if getattr(a, "album_id", None) == album_id:
                self.all_albums[i] = fresh
                break

        # --- Patch filtered_albums list ---
        patched_idx = None
        for i, a in enumerate(self.filtered_albums):
            if getattr(a, "album_id", None) == album_id:
                self.filtered_albums[i] = fresh
                patched_idx = i
                break

        if patched_idx is None:
            # Album was filtered out before; it may now match — re-filter
            # from scratch but without touching the scroll position.
            self._apply_filters_preserve_scroll()
            return

        # The edit may have made the album no longer satisfy the active
        # filter (e.g. is_fixed flipped while filtering "Not Fixed") - drop
        # it from the grid in place rather than leaving a stale match visible.
        verdict = self._album_matches_filters(fresh, self._get_current_filter_params())
        if verdict is False:
            self.filtered_albums.pop(patched_idx)
            if patched_idx < self.display_count:
                item = self.grid_layout.takeAt(patched_idx)
                w = item.widget() if item is not None else None
                if w is not None:
                    w.hide()
                    w.deleteLater()
                self.display_count -= 1
                self.grid_layout.update()
                self.scroll_content.updateGeometry()
                bar = self.scroll_area.verticalScrollBar()
                if bar.maximum() == 0 and self.display_count < len(
                    self.filtered_albums
                ):
                    self._append_more_album_widgets()
            self._update_stats()
            return

        # Re-sort in place and find where the patched album landed
        self._sort_filtered()
        new_idx = next(
            (
                i
                for i, a in enumerate(self.filtered_albums)
                if getattr(a, "album_id", None) == album_id
            ),
            None,
        )

        # Only patch the single widget in place when the edit didn't change
        # its position in the (possibly re-sorted) list — if the order
        # changed, a full rebuild is required to reflect the new positions.
        if (
            new_idx == patched_idx
            and new_idx is not None
            and new_idx < self.display_count
        ):
            item = self.grid_layout.itemAt(new_idx)
            w = item.widget() if item is not None else None
            if w is not None and hasattr(w, "refresh_album"):
                w.refresh_album(fresh)
                return

        self._apply_filters_preserve_scroll()

    def _apply_filters_preserve_scroll(self):
        """Re-run filters and rebuild the grid while preserving scroll position
        and the current display_count (lazy-load progress)."""
        self._cancel_art_worker()

        saved_scroll = self.scroll_area.verticalScrollBar().value()
        saved_display = self.display_count

        results, pending_art, art_mode, art_generation = (
            self._compute_filtered_results()
        )

        self.filtered_albums = results
        self._sort_filtered()
        self._update_stats()

        # Clamp display_count to the new result set size, but don't shrink below
        # what we were already showing — the user's lazy-load progress is preserved.
        self.display_count = min(
            max(saved_display, self.load_chunk), len(self.filtered_albums)
        )
        self._refresh_album_widgets()
        QTimer.singleShot(
            0, lambda: self.scroll_area.verticalScrollBar().setValue(saved_scroll)
        )

        if pending_art:
            self._start_art_worker(
                pending_art, art_mode, self._sort_criteria, art_generation
            )

    # =========================================================================
    # Helpers
    # =========================================================================

    @staticmethod
    def _get_track_count(album) -> int:
        tracks = getattr(album, "tracks", None)
        if tracks is not None:
            try:
                return len(tracks)
            except TypeError:
                pass
        count = getattr(album, "track_count", None)
        if count is not None:
            try:
                return int(count)
            except (TypeError, ValueError):
                pass
        return 0

    @staticmethod
    def _get_album_artist_count(album) -> int:
        artists = getattr(album, "album_artists", None)
        return len(artists) if artists else 0

    @staticmethod
    def _get_artist_names(album) -> list[str]:
        names = []
        for attr in ("album_artists", "artists"):
            artists = getattr(album, attr, None) or []
            for a in artists:
                if hasattr(a, "artist_name"):
                    names.append(a.artist_name.lower())
                elif isinstance(a, str):
                    names.append(a.lower())
                elif isinstance(a, dict):
                    n = a.get("artist_name") or a.get("name") or ""
                    names.append(n.lower())
        return names

    @staticmethod
    def _get_genre_names(album) -> list[str]:
        names = []
        genres = getattr(album, "genres", None) or []
        for g in genres:
            if hasattr(g, "genre_name"):
                names.append(g.genre_name.lower())
            elif isinstance(g, str):
                names.append(g.lower())
        return names


class _AnySpinBox(QSpinBox):
    """SpinBox that shows 'Any' when value is 0 and accepts direct number entry
    without requiring the user to clear the placeholder text first."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setRange(0, 9999)
        self.setValue(0)
        self.setSpecialValueText("Any")

    def textFromValue(self, value: int) -> str:
        return "" if value == 0 else str(value)

    def valueFromText(self, text: str) -> int:
        text = text.strip()
        if not text:
            return 0
        try:
            return int(text)
        except ValueError:
            return 0

    def fixup(self, text: str) -> str:
        return text.strip() or "0"
