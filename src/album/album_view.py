"""UI view for albums in music library"""

import sqlite3

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QFontMetrics, QStandardItem, QStandardItemModel
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
from sqlalchemy.orm import selectinload

from src.album.album_context_menu import AlbumContextMenuMixin
from src.album.album_filtering import AlbumFilteringMixin
from src.album.album_flowlayout import FlowLayout
from src.album.album_sorting import AlbumSortingMixin
from src.album.base_album_edit import AlbumEditor
from src.album.base_album_widget import AlbumWidget
from src.common.layout_utils import FlowLayoutContainer, clear_layout
from src.db.db_tables import Album, AlbumRoleAssociation
from src.foundation.display_settings import apply_scaled_style
from src.foundation.logger_config import logger

# Relationships the search predicate (album_filtering._album_matches_filters)
# and every non-default sort key (album_sorting._sort_key) walk for each of
# self.all_albums. Loaded lazily, a single search or sort over a large library
# fires 1 + 2N SELECTs per album on the UI thread -- seconds of frozen UI.
# selectin-loading them up front keeps it to three extra queries total. Keep
# this list in sync with what those two modules touch.
_ALBUM_LIST_LOAD_OPTIONS = (
    selectinload(Album.album_roles).selectinload(AlbumRoleAssociation.artist),
    selectinload(Album.album_roles).selectinload(AlbumRoleAssociation.role),
    selectinload(Album.tracks),
)

# ---------------------------------------------------------------------------
# Main view
# ---------------------------------------------------------------------------


class AlbumView(AlbumContextMenuMixin, AlbumFilteringMixin, AlbumSortingMixin, QWidget):
    """Enhanced album view with responsive grid layout, interactive controls,
    search/filter functionality, and lazy loading.

    Filtering (widget snapshot, per-album predicate, Art-filter background
    worker, filter-state persistence) lives in AlbumFilteringMixin
    (album_filtering.py). Sort-option data and sort-key logic live in
    AlbumSortingMixin (album_sorting.py). This class owns UI construction,
    the widget grid/lazy-load, and album-detail editing, and composes the
    other two.
    """

    def __init__(self, controller):
        super().__init__()
        self.controller = controller
        self.current_size = 200

        self.all_albums: list = []
        self.filtered_albums: list = []
        self.display_count = 20
        self.load_chunk = 20

        # Sorting state - defaults to "Title (A-Z)"
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
        self._art_worker = None
        self._art_filter_generation = 0
        self._art_batch: list = []
        self._art_needs_resort = False
        self._art_batch_timer = QTimer(self)
        self._art_batch_timer.setSingleShot(True)
        self._art_batch_timer.setInterval(150)
        self._art_batch_timer.timeout.connect(self._flush_art_batch)

        # Debounce timer for persisting filter state, so rapid changes (e.g.
        # typing a year into a spinbox) don't hit disk on every keystroke.
        self._filter_save_timer = QTimer(self)
        self._filter_save_timer.setSingleShot(True)
        self._filter_save_timer.setInterval(400)
        self._filter_save_timer.timeout.connect(self._save_filter_state)

        self._init_ui()
        self._restore_filter_state()
        self.load_albums()

    # =========================================================================
    # UI Construction
    # =========================================================================

    def _init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(8)

        main_layout.addLayout(self._build_top_controls())

        # Filter bar is a QWidget so we can show/hide it cleanly. It's a
        # FlowLayoutContainer (not a plain QWidget) so the row wraps onto a
        # second line -- and reserves the height for it -- instead of
        # crushing/overlapping when a larger UI font won't fit in one row.
        self._filter_row_widget = FlowLayoutContainer()
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

    def _build_filter_bar(self) -> FlowLayout:
        """Secondary row with advanced filter chips."""
        row = FlowLayout(margin=0, h_spacing=12, v_spacing=6)

        def add(widget):
            row.addWidget(widget)
            # A freshly created widget's "show" is deferred to the next
            # event-loop turn, so isVisible() is still False if a layout
            # pass (e.g. the forced repaint() on first view switch) runs
            # synchronously right after -- FlowLayout skips positioning
            # invisible widgets. Force it visible now, same fix as
            # _add_album_widget's grid_layout.addWidget below.
            widget.show()
            return widget

        # Year range
        add(QLabel("Year:"))
        self.year_from = _AnySpinBox()
        self.year_from.valueChanged.connect(self._apply_filters)
        add(self.year_from)
        add(QLabel("–"))  # noqa: RUF001 (en-dash range separator)
        self.year_to = _AnySpinBox()
        self.year_to.valueChanged.connect(self._apply_filters)
        add(self.year_to)

        # Min track count
        add(QLabel("Min tracks:"))
        self.min_tracks = _AnySpinBox()
        self.min_tracks.valueChanged.connect(self._apply_filters)
        add(self.min_tracks)

        # Possibly Incomplete filter
        add(QLabel("Completeness:"))
        self.incomplete_combo = QComboBox()
        self.incomplete_combo.addItems(["Any", "Possibly Incomplete", "Likely Complete"])
        self.incomplete_combo.currentIndexChanged.connect(self._apply_filters)
        add(_shrink_combo_to_content(self.incomplete_combo))

        # Metadata review-tier filter
        add(QLabel("Metadata:"))
        self.fixed_combo = QComboBox()
        self.fixed_combo.addItems(["Any", "Not Started", "First Pass", "Second Pass"])
        self.fixed_combo.currentIndexChanged.connect(self._apply_filters)
        add(_shrink_combo_to_content(self.fixed_combo))

        # Album Art filter
        add(QLabel("Art:"))
        self.art_combo = QComboBox()
        self.art_combo.addItems(["Any", "No Art", "Has Art"])
        self.art_combo.currentIndexChanged.connect(self._apply_filters)
        add(_shrink_combo_to_content(self.art_combo))

        # Album (release) type filter -- options populated from the library in
        # _populate_dynamic_filter_combos() once albums are loaded.
        add(QLabel("Type:"))
        self.type_combo = QComboBox()
        self.type_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.type_combo.addItem("Any")
        self.type_combo.currentIndexChanged.connect(self._apply_filters)
        add(_shrink_combo_to_content(self.type_combo))

        # Media format filter -- options populated from the library too.
        add(QLabel("Media:"))
        self.media_combo = QComboBox()
        self.media_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.media_combo.addItem("Any")
        self.media_combo.currentIndexChanged.connect(self._apply_filters)
        add(_shrink_combo_to_content(self.media_combo))

        # Stats label
        self.stats_label = QLabel()
        self.stats_label.setProperty("textRole", "muted")
        add(self.stats_label)

        # Clear filters
        clear_btn = QPushButton("Clear Filters")
        clear_btn.setFlat(True)
        clear_btn.setToolTip("Reset all filters")
        clear_btn.clicked.connect(self._clear_filters)
        add(clear_btn)

        return row

    def _build_scroll_area(self) -> QScrollArea:
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_content = QWidget()
        self.grid_layout = FlowLayout(self.scroll_content)
        self.scroll_content.setLayout(self.grid_layout)
        self.scroll_area.setWidget(self.scroll_content)

        self.scroll_area.verticalScrollBar().valueChanged.connect(self._check_scroll_position)
        self.scroll_area.setContextMenuPolicy(Qt.CustomContextMenu)
        self.scroll_area.customContextMenuRequested.connect(self._show_context_menu)

        return self.scroll_area

    def _toggle_filter_row(self, visible: bool):
        self._filter_row_widget.setVisible(visible)
        self._filter_toggle_btn.setText("▾ Filters" if visible else "▸ Filters")

    # =========================================================================
    # Loading
    # =========================================================================

    def load_albums(self):
        """Load all albums from the controller and refresh the grid."""
        try:
            self.all_albums = (
                self.controller.get.get_all_entities("Album", load_options=_ALBUM_LIST_LOAD_OPTIONS)
                or []
            )
            self._populate_dynamic_filter_combos()
            self._restore_sort_combo()
            self._apply_filters()
        except (SQLAlchemyError, sqlite3.Error, AttributeError) as e:
            logger.exception("Failed to load albums")
            QMessageBox.critical(self, "Error", f"Failed to load albums:\n{e}")

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
        self.display_count = min(self.display_count + self.load_chunk, len(self.filtered_albums))
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
        if value >= bar.maximum() - 50 and self.display_count < len(self.filtered_albums):
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
            fresh = self.controller.get.get_entity_object("Album", album_id=album.album_id)
            if fresh:
                album = fresh
        except SQLAlchemyError as e:
            logger.warning(f"Failed to refresh album {album.album_id} before editing: {e}")

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
        # filter (e.g. first_pass/second_pass flipped while filtering "Not
        # Started") - drop it from the grid in place rather than leaving a
        # stale match visible.
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
                # QScrollArea only recomputes the scrollbar range on a later
                # event-loop turn, so bar.maximum() read synchronously here
                # would still reflect the pre-removal value. Defer the
                # viewport-fill check (same pattern as _apply_filters and
                # _flush_art_batch) so it sees the real post-layout state.
                QTimer.singleShot(100, self._check_viewport_fill)
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
        self._cancel_art_worker()

        saved_scroll = self.scroll_area.verticalScrollBar().value()
        saved_display = self.display_count

        results, pending_art, art_mode, art_generation = self._compute_filtered_results()

        self.filtered_albums = results
        self._sort_filtered()
        self._update_stats()

        # Clamp display_count to the new result set size, but don't shrink below
        # what we were already showing — the user's lazy-load progress is preserved.
        self.display_count = min(max(saved_display, self.load_chunk), len(self.filtered_albums))
        self._refresh_album_widgets()
        QTimer.singleShot(0, lambda: self.scroll_area.verticalScrollBar().setValue(saved_scroll))

        if pending_art:
            self._start_art_worker(pending_art, art_mode, self._sort_criteria, art_generation)

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


def _shrink_combo_to_content(combo: QComboBox) -> QComboBox:
    """The theme QSS gives every QComboBox a 80px min-width, which is wider
    than several of the filter bar's combos actually need (e.g. "Has Art").
    A widget-level style override wins over that app-level rule, but only
    once the widget has been polished with it in place -- otherwise
    sizeHint() still reports the stale, QSS-only width.
    """
    apply_scaled_style(combo, "min-width: 0px;")
    combo.ensurePolished()
    return combo


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

    def sizeHint(self) -> QSize:
        # QSpinBox's built-in sizeHint() reserves room for spin-button
        # chrome even though the theme QSS shrinks those buttons to
        # nothing, leaving a lot of dead space around a value that's at
        # most 4 digits. Size to the widest text we'll actually show
        # ("9999" or "Any") instead.
        fm = QFontMetrics(self.font())
        text_width = max(
            fm.horizontalAdvance(str(self.maximum())), fm.horizontalAdvance(self.specialValueText())
        )
        return QSize(text_width + 28, super().sizeHint().height())

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()
