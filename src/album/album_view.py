"""UI view for albums in music library"""

import random

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from src.album.album_delete_dialog import DeleteEmptyAlbumsDialog
from src.album.album_flowlayout import FlowLayout
from src.album.album_merge import AlbumMerge
from src.album.album_new import NewAlbumDialog
from src.album.base_album_edit import AlbumEditor
from src.album.base_album_widget import AlbumWidget
from src.core.logger_config import logger
from src.core.status_utility import show_status_message
from src.image.artwork_cache import get_artwork_cache

# ---------------------------------------------------------------------------
# Main view
# ---------------------------------------------------------------------------


class AlbumView(QWidget):
    """Enhanced album view with responsive grid layout, interactive controls,
    search/filter functionality, and lazy loading.
    """

    # Sort options grouped under a category header for a friendlier combo box.
    # Each group: (group_label, [(item_label, criteria_key, descending), ...])
    _SORT_GROUPS: list[tuple[str, list[tuple[str, str, bool]]]] = [
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
        row.addWidget(QLabel("Incomplete:"))
        self.incomplete_combo = QComboBox()
        self.incomplete_combo.addItems(["Any", "Possibly Incomplete", "Complete"])
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
    # Context Menu
    # =========================================================================

    def _show_context_menu(self, position):
        menu = QMenu(self)

        new_action = menu.addAction("➕ New Album…")
        new_action.triggered.connect(self._create_new_album)

        menu.addSeparator()

        global_pos = self.scroll_area.mapToGlobal(position)
        widget_at = self.scroll_content.childAt(
            self.scroll_content.mapFromGlobal(global_pos)
        )
        target_album_widget = self._find_album_widget_ancestor(widget_at)

        if target_album_widget is not None:
            album = target_album_widget.album

            queue_action = menu.addAction(
                f"▶ Add to Queue: {getattr(album, 'album_name', 'Album')}"
            )
            queue_action.triggered.connect(lambda: self._add_album_to_queue(album))

            menu.addSeparator()

            # --- Merge submenu ---
            merge_menu = menu.addMenu(
                f"🔀 Merge {getattr(album, 'album_name', 'Album')}…"
            )
            merge_this_action = merge_menu.addAction("Merge this album into another…")
            merge_this_action.triggered.connect(lambda: self._merge_album(album))

            menu.addSeparator()

            delete_action = menu.addAction(
                f"🗑 Delete {getattr(album, 'album_name', 'Album')}"
            )
            delete_action.triggered.connect(lambda: self._delete_album(album))
            menu.addSeparator()

        del_empty_action = menu.addAction("🧹 Delete Empty Albums…")
        del_empty_action.triggered.connect(self._delete_empty_albums)

        find_dupes_action = menu.addAction("🔀 Find Duplicate Albums…")
        find_dupes_action.triggered.connect(self._find_duplicate_albums)

        menu.exec_(self.scroll_area.mapToGlobal(position))

    def _merge_album(self, album):
        """Open the merge dialog with this album pre-loaded as source."""
        engine = AlbumMerge(self.controller)
        merged = engine.merge_known(album, parent=self)
        if merged:
            self.load_albums()

    def _find_duplicate_albums(self):
        """Open the duplicate-finder list."""
        engine = AlbumMerge(self.controller)
        engine.open_list(parent=self)
        self.load_albums()

    def _add_album_to_queue(self, album):
        """Add all tracks of the given album to the playback queue."""
        qm = self._get_queue_manager()
        if not qm:
            return

        tracks = self._get_album_tracks(album)
        if not tracks:
            logger.warning(
                f"No tracks found for album '{getattr(album, 'album_name', '?')}'"
            )
            return

        qm.add_tracks_to_queue(tracks)
        logger.info(
            f"Added {len(tracks)} tracks from album '{album.album_name}' to queue"
        )

    def _get_queue_manager(self):
        """Retrieve the queue manager from the controller or media player."""
        if hasattr(self.controller, "queue_manager"):
            return self.controller.queue_manager
        if hasattr(self.controller, "mediaplayer") and hasattr(
            self.controller.mediaplayer, "queue_manager"
        ):
            return self.controller.mediaplayer.queue_manager
        logger.warning("Queue manager not found in controller")
        return None

    def _get_album_tracks(self, album):
        """
        Get all tracks belonging to an album.
        Tries album.tracks, album.track_set, and finally a direct DB query."""

        if hasattr(album, "tracks") and album.tracks is not None:
            return list(album.tracks)

        # Last resort: query all tracks and filter by album_id
        try:
            all_tracks = self.controller.get.get_all_entities("Track") or []
            return [
                t for t in all_tracks if getattr(t, "album_id", None) == album.album_id
            ]
        except Exception as e:
            logger.error(f"Failed to fetch tracks for album {album.album_id}: {e}")
            return []

    @staticmethod
    def _find_album_widget_ancestor(widget) -> "AlbumWidget | None":
        """Walk up the widget hierarchy to find an AlbumWidget."""
        while widget is not None:
            if isinstance(widget, AlbumWidget):
                return widget
            widget = widget.parent()
        return None

    # =========================================================================
    # Album CRUD
    # =========================================================================

    def _create_new_album(self):
        """Open dialog to create a new album."""
        dlg = NewAlbumDialog(self.controller, self)
        if dlg.exec_() != QDialog.Accepted:
            return

        try:
            kwargs = {
                "album_name": dlg.album_name,
                "is_compilation": dlg.is_compilation,
            }
            if dlg.release_year is not None:
                kwargs["release_year"] = dlg.release_year

            new_album = self.controller.add.add_entity("Album", **kwargs)

            # Optionally link an artist
            if dlg.artist_name:
                artist = self.controller.get.get_entity_object(
                    "Artist", artist_name=dlg.artist_name
                )
                if not artist:
                    artist = self.controller.add.add_entity(
                        "Artist", artist_name=dlg.artist_name
                    )
                self.controller.add.add_entity(
                    "AlbumRoleAssociation",
                    album_id=new_album.album_id,
                    artist_id=artist.artist_id,
                    role_id=1,
                )

            logger.info(f"Created new album: {new_album.album_name}")
            self.load_albums()

            # Open the detail view immediately so the user can fill it in
            self._show_album_details(new_album)

        except Exception as e:
            logger.exception("Failed to create album")
            QMessageBox.critical(self, "Error", f"Could not create album:\n{e}")

    def _delete_album(self, album):
        """Confirm and delete a single album."""
        name = getattr(album, "album_name", "this album")
        track_count = self._get_track_count(album)

        msg = f'Delete "{name}"?'
        if track_count:
            msg += (
                f"\n\nThis album has {track_count} track(s). "
                "Tracks will be disassociated but not removed from your library."
            )
        msg += "\n\nThis action cannot be undone."

        reply = QMessageBox.question(
            self,
            "Confirm Delete",
            msg,
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        try:
            self.controller.delete.delete_entity("Album", album.album_id)
            logger.info(f"Deleted album: {name} (id={album.album_id})")
            self.load_albums()
        except Exception as e:
            logger.exception("Failed to delete album")
            QMessageBox.critical(self, "Error", f"Could not delete album:\n{e}")

    def _delete_empty_albums(self):
        """Find and delete all empty albums after user confirmation."""
        try:
            all_albums = self.controller.get.get_all_entities("Album")
            empty_albums = [a for a in all_albums if self._get_track_count(a) == 0]

            if not empty_albums:
                show_status_message(self, "No empty albums found in your library.")
                return

            confirm_dialog = DeleteEmptyAlbumsDialog(empty_albums, self)
            if confirm_dialog.exec_() != QDialog.Accepted:
                return

            deleted = 0
            for album in empty_albums:
                try:
                    self.controller.delete.delete_entity("Album", album.album_id)
                    deleted += 1
                except Exception as e:
                    logger.warning(f"Failed to delete album {album.album_name}: {e}")

            show_status_message(self, f"Deleted {deleted} empty album(s).")
            self.load_albums()

        except Exception as e:
            logger.exception("Failed to delete empty albums")
            QMessageBox.critical(self, "Error", f"Failed to delete empty albums:\n{e}")

    # =========================================================================
    # Loading & Filtering
    # =========================================================================

    def load_albums(self):
        """Load all albums from the controller and refresh the grid."""
        try:
            self.all_albums = self.controller.get.get_all_entities("Album") or []
            self._restore_sort_combo()
            self._apply_filters()
        except Exception as e:
            logger.exception("Failed to load albums")
            QMessageBox.critical(self, "Error", f"Failed to load albums:\n{e}")

    def _on_search_changed(self, text: str):
        # Debounce: only filter after typing pauses
        self._search_timer.start()

    def _apply_filters(self):
        """Apply all active filters (search text, year range, track count, possibly_incomplete, is_fixed)."""
        text = self.search_bar.text().strip().lower()
        year_from = self.year_from.value()
        year_to = self.year_to.value()
        min_tracks = self.min_tracks.value()
        incomplete_mode = self.incomplete_combo.currentText()
        fixed_mode = self.fixed_combo.currentText()

        results = []
        for album in self.all_albums:
            # ── Text search ──────────────────────────────────────────────
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
                    continue

            # ── Year range ───────────────────────────────────────────────
            album_year = getattr(album, "release_year", None)
            if album_year:
                try:
                    yr = int(album_year)
                    if year_from > 0 and yr < year_from:
                        continue
                    if year_to > 0 and yr > year_to:
                        continue
                except (TypeError, ValueError):
                    pass
            else:
                # If we have a strict year filter and album has no year, skip it
                if year_from > 0 or year_to > 0:
                    continue

            # ── Min track count ──────────────────────────────────────────
            if min_tracks > 0 and self._get_track_count(album) < min_tracks:
                continue

            # ── Possibly Incomplete filter ────────────────────────────────
            if incomplete_mode != "Any":
                is_incomplete = bool(getattr(album, "possibly_incomplete", False))
                if incomplete_mode == "Possibly Incomplete" and not is_incomplete:
                    continue
                if incomplete_mode == "Complete" and is_incomplete:
                    continue

            # ── Is Fixed filter ───────────────────────────────────────────
            if fixed_mode != "Any":
                is_fixed = bool(getattr(album, "is_fixed", False))
                if fixed_mode == "Fixed Only" and not is_fixed:
                    continue
                if fixed_mode == "Not Fixed" and is_fixed:
                    continue

            # ── Album Art filter ──────────────────────────────────────────
            art_mode = self.art_combo.currentText()
            if art_mode != "Any":
                cache = get_artwork_cache()
                has_art = bool(cache.has_art(album, "front")) if cache else False
                if art_mode == "No Art" and has_art:
                    continue
                if art_mode == "Has Art" and not has_art:
                    continue

            results.append(album)

        self.filtered_albums = results
        self._sort_filtered()
        self._update_stats()

        self.display_count = self.load_chunk
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
        except Exception as e:
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
                # Sort by pixel area of the front cover image.
                cache = get_artwork_cache()
                dims = cache.get_dimensions(album, "front") if cache else None
                if dims:
                    return dims[0] * dims[1]
                return 0

            return getattr(album, "album_name", "").lower()

        except Exception as e:
            logger.warning(
                f"Sort key failed for album {getattr(album, 'album_name', '?')} "
                f"(criteria={self._sort_criteria}): {e}"
            )
            return ""

    # =========================================================================
    # Widget Grid
    # =========================================================================

    def _refresh_album_widgets(self):
        """Rebuild the grid from scratch up to display_count."""
        self._clear_layout(self.grid_layout)
        QApplication.processEvents()
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
        except Exception:
            pass

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
        except Exception:
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
        if new_idx == patched_idx and new_idx is not None and new_idx < self.display_count:
            item = self.grid_layout.itemAt(new_idx)
            w = item.widget() if item is not None else None
            if w is not None and hasattr(w, "refresh_album"):
                w.refresh_album(fresh)
                return

        self._apply_filters_preserve_scroll()

    def _apply_filters_preserve_scroll(self):
        """Re-run filters and rebuild the grid while preserving scroll position
        and the current display_count (lazy-load progress)."""
        saved_scroll = self.scroll_area.verticalScrollBar().value()
        saved_display = self.display_count

        text = self.search_bar.text().strip().lower()
        year_from = self.year_from.value()
        year_to = self.year_to.value()
        min_tracks = self.min_tracks.value()
        incomplete_mode = self.incomplete_combo.currentText()
        fixed_mode = self.fixed_combo.currentText()
        art_mode = self.art_combo.currentText()

        results = []
        for album in self.all_albums:
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
                    continue

            album_year = getattr(album, "release_year", None)
            if album_year:
                try:
                    yr = int(album_year)
                    if year_from > 0 and yr < year_from:
                        continue
                    if year_to > 0 and yr > year_to:
                        continue
                except (TypeError, ValueError):
                    pass
            else:
                if year_from > 0 or year_to > 0:
                    continue

            if min_tracks > 0 and self._get_track_count(album) < min_tracks:
                continue

            if incomplete_mode != "Any":
                is_incomplete = bool(getattr(album, "possibly_incomplete", False))
                if incomplete_mode == "Possibly Incomplete" and not is_incomplete:
                    continue
                if incomplete_mode == "Complete" and is_incomplete:
                    continue

            if fixed_mode != "Any":
                is_fixed = bool(getattr(album, "is_fixed", False))
                if fixed_mode == "Fixed Only" and not is_fixed:
                    continue
                if fixed_mode == "Not Fixed" and is_fixed:
                    continue

            if art_mode != "Any":
                cache = get_artwork_cache()
                has_art = bool(cache.has_art(album, "front")) if cache else False
                if art_mode == "No Art" and has_art:
                    continue
                if art_mode == "Has Art" and not has_art:
                    continue

            results.append(album)

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

    @staticmethod
    def _clear_layout(layout):
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
            elif item.layout():
                AlbumView._clear_layout(item.layout())


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
