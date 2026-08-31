"""
track_view_data.py — lazy DB loading, batch pagination, sorting, and status
text for TrackView.
"""

from PySide6.QtCore import QObject, Qt, QThread, Signal
from PySide6.QtGui import QStandardItem
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from src.core.logger_config import logger
from src.db.db_tables import Album, Artist, Disc, Role, TrackArtistRole
from src.track.track_view_filter import LAZY_BATCH_SIZE, SortWorker

# Track fields whose value comes from a relationship rather than a plain
# Column. Reading these lazily (`getattr(track, field_name)`) triggers a
# separate DB query per track — fine for one row, ruinous for a full-library
# sort. `_build_lookup_caches`/`TrackLookupCacheWorker` fetch all of them in
# a few bulk queries instead.
_ALBUM_DERIVED_FIELDS = ("album_name", "release_year", "release_month", "release_day")

# Relationship-derived columns that a lookup-cache refresh can change.
_CACHE_DEPENDENT_FIELDS = frozenset({"primary_artist_names", "disc_number", *_ALBUM_DERIVED_FIELDS})


def _oxford_join(names: list) -> str:
    """Join names as 'A' / 'A & B' / 'A, B, & C' (Oxford comma for 3+)."""
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return f"{names[0]} & {names[1]}"
    return f"{', '.join(names[:-1])}, & {names[-1]}"


def _format_primary_artist_names(primary_names: list, featured_names: list | None = None) -> str:
    """Oxford-comma join, mirroring Track.primary_artist_names in src/db_tables/track.py."""
    primary_names = [n.strip() for n in primary_names if n and n.strip()]
    if not primary_names:
        return "Unknown Artist"

    result = _oxford_join(primary_names)

    featured_names = [n.strip() for n in (featured_names or []) if n and n.strip()]
    if featured_names:
        result = f"{result} Featuring {_oxford_join(featured_names)}"

    return result


def _fetch_lookup_caches(session):
    """
    Bulk-fetch every relationship-derived value (album info, disc number,
    primary artist names) in a handful of JOIN queries. Shared by the
    synchronous first-load path and the background `TrackLookupCacheWorker`.
    """
    album_cache = {
        album_id: {
            "album_name": name,
            "release_year": year,
            "release_month": month,
            "release_day": day,
        }
        for album_id, name, year, month, day in session.execute(
            select(
                Album.album_id,
                Album.album_name,
                Album.release_year,
                Album.release_month,
                Album.release_day,
            )
        )
    }

    disc_number_cache = dict(session.execute(select(Disc.disc_id, Disc.disc_number)).all())

    by_track: dict[int, list[tuple[str, str, str]]] = {}
    rows = session.execute(
        select(TrackArtistRole.track_id, Artist.artist_name, Artist.sort_name, Role.role_name)
        .join(Artist, TrackArtistRole.artist_id == Artist.artist_id)
        .join(Role, TrackArtistRole.role_id == Role.role_id)
        # Match the (track_id, artist_id, role_id) composite PK index
        # order that a per-track lazy load would return, so cached names
        # come out in the same order as Track.primary_artist_names.
        .order_by(TrackArtistRole.track_id, TrackArtistRole.artist_id, TrackArtistRole.role_id)
    ).all()
    for track_id, artist_name, sort_name, role_name in rows:
        by_track.setdefault(track_id, []).append((artist_name, sort_name, role_name))

    artist_name_cache = {
        track_id: _format_primary_artist_names(
            [name for name, _sort, role_name in entries if role_name == "Primary Artist"],
            [name for name, _sort, role_name in entries if role_name == "Featured Artist"],
        )
        for track_id, entries in by_track.items()
    }

    # Parallel cache, keyed and ordered identically, but built from each
    # artist's filing name (Artist.sort_name, falling back to the display
    # name). Used only as the sort key for the Artist column -- never
    # displayed. See `_field_value`.
    artist_sort_cache = {
        track_id: _format_primary_artist_names(
            [
                (sort_name or name)
                for name, sort_name, role_name in entries
                if role_name == "Primary Artist"
            ],
            [
                (sort_name or name)
                for name, sort_name, role_name in entries
                if role_name == "Featured Artist"
            ],
        )
        for track_id, entries in by_track.items()
    }

    return album_cache, disc_number_cache, artist_name_cache, artist_sort_cache


class TrackLookupCacheWorker(QObject):
    """
    Runs on a background thread. Rebuilds the artist/album/disc lookup
    caches with 3 bulk queries and emits the results back to the main
    thread — mirrors RoleLoaderWorker in src/role/role_view.py.

    Used when the user revisits the Tracks nav item: switching views must
    stay instant, so the (already-cached) tracks are re-displayed
    immediately and this worker refreshes the relationship-derived caches
    (which may have gone stale if artists/albums were edited on another
    view) without blocking the GUI thread.
    """

    # Payload: (album_cache, disc_number_cache, artist_name_cache, artist_sort_cache)
    finished = Signal(object, object, object, object)
    error = Signal(str)

    def __init__(self, controller):
        super().__init__()
        self.controller = controller

    def run(self):
        try:
            caches = _fetch_lookup_caches(self.controller.get.session)
            self.finished.emit(*caches)
        except Exception as e:
            # Intentional broad boundary catch: this runs on a background thread
            # and must not let an exception be lost silently.
            logger.exception("Failed to load track view lookup caches")
            self.error.emit(str(e))


class TrackViewDataMixin:
    """Lazy loading of tracks from the DB into the Qt model, plus sorting."""

    def load_tracks_on_startup(self):
        """
        Load all tracks from DB into self._all_tracks (once).
        Only pushes the first LAZY_BATCH_SIZE rows into the Qt model.

        On first load, the lookup caches are built synchronously (nothing to
        show yet anyway). On a revisit (nav dock switch back to Tracks),
        tracks are already cached, so the model is refreshed immediately from
        the existing caches and the lookup caches are rebuilt in the
        background — that keeps the switch itself instant even though a
        full-library JOIN is happening under the hood.
        """
        if not self._tracks_loaded:
            try:
                tracks = self.controller.get.get_all_entities("Track")
                self._all_tracks = tracks or []
                self._tracks_loaded = True
                logger.info(f"Fetched {len(self._all_tracks):,} tracks from DB (one-time).")
            except SQLAlchemyError as e:
                logger.error(f"Error fetching tracks: {e}")
                self._all_tracks = []

            self._build_lookup_caches()
            self._filter_active = False
            self._filtered_tracks = []
            self._loaded_count = 0
            self.model.setRowCount(0)
            self._append_next_batch(self._all_tracks)
            self._update_status()
        else:
            self._filter_active = False
            self._filtered_tracks = []
            self._loaded_count = 0
            self.model.setRowCount(0)
            self._append_next_batch(self._all_tracks)
            self._update_status()
            self._refresh_lookup_caches_async()

    def _force_reload(self):
        """Explicitly re-query the DB (Refresh)."""
        self._tracks_loaded = False
        self.load_tracks_on_startup()

    def load_data(self, tracks: list):
        """External callers (e.g. main_window refresh) can push a new track list."""
        self._all_tracks = tracks or []
        self._tracks_loaded = True
        self._build_lookup_caches()
        self._filter_active = False
        self._filtered_tracks = []
        self._loaded_count = 0
        self.model.setRowCount(0)
        self._append_next_batch(self._all_tracks)
        self._update_status()

    def _build_lookup_caches(self):
        """
        Bulk-fetch every relationship-derived value (album info, disc number,
        primary artist names) in a handful of JOIN queries, keyed by the
        already-loaded FK/PK columns (album_id, disc_id, track_id).

        This is what makes sorting fast: `_field_value` below never has to
        lazy-load a relationship per track, so sort_key() is a pure in-memory
        dict lookup and is safe to run on the background SortWorker thread
        (the ORM session itself is main-thread-only).

        Used for the synchronous first-load / explicit-refresh paths. See
        `_refresh_lookup_caches_async` for the background-thread version
        used when revisiting the Tracks nav item.
        """
        (
            self._album_cache,
            self._disc_number_cache,
            self._artist_name_cache,
            self._artist_sort_cache,
        ) = _fetch_lookup_caches(self.controller.get.session)

    def _refresh_lookup_caches_async(self):
        """Rebuild the lookup caches on a background thread (nav-switch revisit path)."""
        try:
            if self._lookup_thread and self._lookup_thread.isRunning():
                return
        except RuntimeError:
            self._lookup_thread = None

        self._lookup_thread = QThread(self)
        self._lookup_worker = TrackLookupCacheWorker(self.controller)
        self._lookup_worker.moveToThread(self._lookup_thread)

        self._lookup_thread.started.connect(self._lookup_worker.run)
        self._lookup_worker.finished.connect(self._on_lookup_caches_loaded)
        self._lookup_worker.error.connect(self._on_lookup_caches_error)
        self._lookup_worker.finished.connect(self._lookup_thread.quit)
        self._lookup_worker.error.connect(self._lookup_thread.quit)
        self._lookup_thread.finished.connect(self._lookup_thread.deleteLater)

        self._lookup_thread.start()

    def _on_lookup_caches_loaded(
        self, album_cache, disc_number_cache, artist_name_cache, artist_sort_cache
    ):
        """Called on the main thread once TrackLookupCacheWorker finishes."""
        self._album_cache = album_cache
        self._disc_number_cache = disc_number_cache
        self._artist_name_cache = artist_name_cache
        self._artist_sort_cache = artist_sort_cache
        self._refresh_visible_rows()

    def _on_lookup_caches_error(self, message: str):
        logger.error(f"Error refreshing track lookup caches: {message}")

    def _refresh_visible_rows(self):
        """
        Re-render the relationship-derived columns (artist/album/disc names)
        for rows already pushed into the model, picking up any changes from
        the just-completed background cache rebuild.
        """
        source = self._filtered_tracks if self._filter_active else self._all_tracks
        column_keys = list(self.columns.keys())
        relevant_columns = [
            (i, field_name)
            for i, field_name in enumerate(column_keys)
            if field_name in _CACHE_DEPENDENT_FIELDS
        ]
        if not relevant_columns:
            return

        row_count = min(self._loaded_count, len(source))
        for row in range(row_count):
            track = source[row]
            for col_index, field_name in relevant_columns:
                field_config = self.track_fields.get(field_name)
                value = self._field_value(track, field_name)
                display_value = self._format_value(value, field_name, field_config)
                item = self.model.item(row, col_index)
                if item is None:
                    continue
                item.setText(display_value)
                item.setData(
                    value if isinstance(value, (int, float)) else display_value, Qt.UserRole
                )

    @staticmethod
    def _format_primary_artist_names(
        primary_names: list, featured_names: list | None = None
    ) -> str:
        """Oxford-comma join, mirroring Track.primary_artist_names in src/db_tables/track.py."""
        return _format_primary_artist_names(primary_names, featured_names)

    def _field_value(self, track, field_name: str):
        """
        Return the raw value for `field_name`, using the precomputed caches
        for relationship-derived fields instead of touching the ORM
        relationship directly.
        """
        if field_name == "primary_artist_names":
            return self._artist_name_cache.get(track.track_id, "Unknown Artist")
        if field_name == "primary_artist_names__sort":
            # Sort-only pseudo-field: order the Artist column by filing name
            # (Artist.sort_name) while the visible cell keeps the display name.
            return self._artist_sort_cache.get(track.track_id) or self._artist_name_cache.get(
                track.track_id, "Unknown Artist"
            )
        if field_name == "disc_number":
            return self._disc_number_cache.get(track.disc_id)
        if field_name in _ALBUM_DERIVED_FIELDS:
            album = self._album_cache.get(track.album_id)
            return album.get(field_name) if album else None
        return getattr(track, field_name, None)

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
                value = self._field_value(track, field_name)

                display_value = self._format_value(value, field_name, field_config)
                item = QStandardItem(display_value)
                item.setEditable(False)
                item.setData(
                    value if isinstance(value, (int, float)) else display_value, Qt.UserRole
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
        - The actual sort runs on a background SortWorker so a large library
          doesn't freeze the UI; the table is disabled and the status label
          shows "Sorting…" until it finishes.
        """
        if self._sort_worker and self._sort_worker.isRunning():
            self._sort_worker.request_cancel()
            self._sort_worker.wait()

        column_keys = list(self.columns.keys())
        if logical_index < 0 or logical_index >= len(column_keys):
            return

        if self._sort_column_index == logical_index:
            self._sort_ascending = not self._sort_ascending
        else:
            self._sort_column_index = logical_index
            self._sort_ascending = True

        header = self.table.horizontalHeader()
        header.setSortIndicator(
            logical_index, Qt.AscendingOrder if self._sort_ascending else Qt.DescendingOrder
        )

        field_name = column_keys[logical_index]
        source = self._filtered_tracks if self._filter_active else self._all_tracks

        self.table.setEnabled(False)
        self.status_label.setText("Sorting…")

        # The Artist column displays the plain name join but files by
        # Artist.sort_name -- route the sort through the pseudo-field.
        sort_field = (
            "primary_artist_names__sort" if field_name == "primary_artist_names" else field_name
        )
        self._sort_worker = SortWorker(source, self._field_value, sort_field, self._sort_ascending)
        self._sort_worker.finished.connect(self._on_sort_done)
        self._sort_worker.start()

    def _on_sort_done(self, sorted_tracks: list):
        """Called on the main thread once the background SortWorker finishes."""
        if self._filter_active:
            self._filtered_tracks = sorted_tracks
        else:
            self._all_tracks = sorted_tracks

        self._loaded_count = 0
        self.model.setRowCount(0)
        source = self._filtered_tracks if self._filter_active else self._all_tracks
        self._append_next_batch(source)
        self.table.setEnabled(True)
        self._update_status()

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
            self.status_label.setText(f"Showing {self._loaded_count:,} / {total:,} tracks")
