"""Toolbar, search scope, empty states, selection bar and now-playing wiring for both track views."""
# Hosts provide self.layout, self.table/self.model, _add_toolbar_actions(), _selection_bar_actions()
# and _now_playing_source(); they may override _search_debounce_ms and _empty_library_text.

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu, QPushButton

from src.charts.ui.chart_table_placeholder import install_empty_placeholder
from src.foundation.logger_config import logger
from src.track.view.track_table import RIGHT_ALIGNED_COLUMNS, TrackRowDelegate
from src.track.view.track_toolbar import SelectionBar, TrackToolbar
from src.track.view.track_view_filter import SEARCH_ALL


def format_length(seconds: float) -> str:
    """Total length of a track list: '812 h 4 min', '47 min', '0 min'."""
    minutes = int(seconds // 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:,} h {minutes} min" if hours else f"{minutes} min"


def format_clock(seconds: float) -> str:
    """Length of a selection as a clock: '24:41' or '1:02:09'."""
    total = int(seconds)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


class TrackViewToolbarMixin:
    """Builds the shared track-list chrome around a host's table."""

    _search_debounce_ms = 0
    _empty_library_text = "No tracks to show."

    # ── Toolbar ───────────────────────────────────────────────────────────

    def _build_toolbar(self):
        """Build the shared toolbar and connect live search."""
        toolbar = TrackToolbar(self)
        self.toolbar = toolbar
        # Aliases kept for existing callers (mood_dialog.py reparents
        # search_bar / status_label; tests read search_bar).
        self.search_bar = toolbar.search_bar
        self.search_column_btn = toolbar.search_column_btn
        self._search_column_menu = toolbar.search_column_menu
        self.status_label = toolbar.status_label
        self._search_field_name = SEARCH_ALL
        self._search_field_label = "All Columns"

        if self._search_debounce_ms:
            # Live search on a large list: filter once typing pauses; Enter
            # filters at once.
            self._search_timer = QTimer(self)
            self._search_timer.setSingleShot(True)
            self._search_timer.setInterval(self._search_debounce_ms)
            self._search_timer.timeout.connect(self._apply_search_filter)
            self.search_bar.textChanged.connect(self._on_search_text_changed)
            self.search_bar.returnPressed.connect(self._apply_search_now)
        else:
            # Small lists (a mood, a playlist, a duplicate group): filtering
            # on every keystroke stays cheap.
            self.search_bar.textChanged.connect(lambda _text=None: self._apply_search_filter())
        self.search_bar.textChanged.connect(lambda _text=None: self._sync_scope_chip())
        toolbar.scope_cleared.connect(self._reset_search_scope)

        self._add_toolbar_actions(toolbar)
        self.layout.addWidget(toolbar)

    def _add_toolbar_actions(self, toolbar: TrackToolbar):
        """Hook: add host-specific action buttons to `toolbar`."""

    def _populate_search_combo(self):
        """Build the search-scope menu: All Columns, then one submenu per field category."""
        menu = self._search_column_menu
        menu.clear()

        all_action = QAction("All Columns", menu)
        all_action.setData(SEARCH_ALL)
        all_action.triggered.connect(self._on_search_column_selected)
        menu.addAction(all_action)
        menu.addSeparator()

        # Group columns by their FieldSpec category
        category_groups: dict[str, list] = {}
        for field_name, friendly in self.columns.items():
            field_config = self.track_fields.get(field_name)
            cat = (field_config.category or "Other") if field_config else "Other"
            category_groups.setdefault(cat, []).append((field_name, friendly))

        for cat, fields in sorted(category_groups.items()):
            submenu = QMenu(cat, menu)
            for field_name, friendly in fields:
                act = QAction(friendly, submenu)
                act.setData(field_name)
                act.triggered.connect(self._on_search_column_selected)
                submenu.addAction(act)
            menu.addMenu(submenu)

    def _on_search_column_selected(self):
        """Scope the search to the picked column (or all columns)."""
        action = self.sender()
        if not action:
            return
        self._set_search_scope(action.data() or SEARCH_ALL, action.text())

    def _set_search_scope(self, field_name: str, label: str):
        """Set the search scope and search again."""
        self._search_field_name = field_name
        self._search_field_label = label if field_name != SEARCH_ALL else "All Columns"
        self.search_column_btn.setText(f"{self._search_field_label} ▾")
        logger.debug(f"Search column changed to '{self._search_field_label}'")
        self._sync_scope_chip()
        # Re-run the search immediately with the new column choice
        self._apply_search_filter()

    def _reset_search_scope(self, *_args):
        """Search all columns again."""
        self._set_search_scope(SEARCH_ALL, "All Columns")

    def _sync_scope_chip(self):
        """Show the scope chip only for a single-column search."""
        scoped = self._search_field_name != SEARCH_ALL
        self.toolbar.set_scope(self._search_field_label if scoped else None, self.search_bar.text().strip())

    def _apply_search_now(self):
        """Search at once (Enter), skipping the pause timer."""
        timer = getattr(self, "_search_timer", None)
        if timer is not None:
            timer.stop()
        self._apply_search_filter()

    # ── Table chrome: delegate, header menu, empty state, selection bar ──

    def _install_table_chrome(self):
        """Install the row delegate, header menu, empty state, selection bar and now-playing link."""
        self._row_delegate = TrackRowDelegate(self.table, list(self.columns.keys()))
        self.table.setItemDelegate(self._row_delegate)

        # Right-click any column header to show/hide columns.
        header = self.table.horizontalHeader()
        header.setContextMenuPolicy(Qt.CustomContextMenu)
        header.customContextMenuRequested.connect(lambda _pos: self.show_column_menu())

        self._empty_placeholder = install_empty_placeholder(self.table, self._empty_library_text)
        # Offered under a scoped no-match message: widen the search instead
        # of leaving the user at a dead end.
        self._empty_action = QPushButton("Search all columns", self.table.viewport())
        self._empty_action.setObjectName("EmptyStateAction")
        self._empty_action.setCursor(Qt.PointingHandCursor)
        self._empty_action.clicked.connect(self._reset_search_scope)
        self._empty_action.hide()
        viewport_layout = self.table.viewport().layout()
        viewport_layout.insertStretch(0)
        viewport_layout.addWidget(self._empty_action, 0, Qt.AlignHCenter)
        viewport_layout.addStretch()

        play_next, queue, edit, delete = self._selection_bar_actions()
        self.selection_bar = SelectionBar(play_next, queue, edit, delete, self.table.clearSelection, self)
        self.layout.addWidget(self.selection_bar)
        self._selection_timer = QTimer(self)
        self._selection_timer.setSingleShot(True)
        self._selection_timer.setInterval(0)
        self._selection_timer.timeout.connect(self._update_selection_bar)
        self.table.selectionModel().selectionChanged.connect(lambda *_: self._selection_timer.start())
        # setModel/setRowCount(0) don't emit selectionChanged; re-check on reset.
        self.model.modelReset.connect(lambda: self._selection_timer.start())
        self.model.rowsRemoved.connect(lambda *_: self._selection_timer.start())

        player = self._now_playing_source()
        if player is not None and hasattr(player, "track_changed"):
            player.track_changed.connect(self.table.set_now_playing)
            current = getattr(player, "current_file", None)
            if current:
                self.table.set_now_playing(current)

    def _selection_bar_actions(self):
        """Hook: return the (play_next, queue, edit, delete) callables."""
        raise NotImplementedError

    def _now_playing_source(self):
        """Hook: return the player, or None."""
        return

    def _visible_source(self) -> list:
        """Return the listed tracks (the filter results while a search is active)."""
        return self._filtered_tracks if self._filter_active else self._all_tracks

    def _update_selection_bar(self):
        """Show the selection bar with count and length for 2+ selected rows."""
        bar = getattr(self, "selection_bar", None)
        if bar is None:
            return
        rows = sorted({i.row() for i in self.table.selectionModel().selectedRows()})
        if len(rows) < 2:
            bar.hide()
            return
        source = self._visible_source()
        seconds = sum(float(getattr(source[r], "duration", 0) or 0) for r in rows if r < len(source))
        bar.summary.setText(f"{len(rows):,} selected  ·  {format_clock(seconds)}")
        bar.show()

    def _sync_empty_state(self):
        """Show the right empty-state message (loading, no match, empty library)."""
        placeholder = getattr(self, "_empty_placeholder", None)
        if placeholder is None:
            return
        scoped_no_match = self._filter_active and not self._filtered_tracks and self._search_field_name != SEARCH_ALL
        self._empty_action.setVisible(scoped_no_match)
        if not getattr(self, "_tracks_loaded", True):
            placeholder.setText("Loading library…")
            placeholder.show()
            return
        if self._filter_active:
            if self._filtered_tracks:
                placeholder.hide()
                return
            text = f"No tracks match “{self.search_bar.text().strip()}”"
            text += f" in {self._search_field_label}." if scoped_no_match else "."
            placeholder.setText(text)
            placeholder.show()
            return
        placeholder.setText(self._empty_library_text)
        placeholder.setVisible(not self._all_tracks)

    # ── Status line ───────────────────────────────────────────────────────

    def _list_length(self, tracks: list) -> float:
        """Return the summed duration of `tracks`, cached per list."""
        # Ends' identities guard against a freed list's id being reused.
        key = (id(tracks), len(tracks), id(tracks[0]) if tracks else None, id(tracks[-1]) if tracks else None)
        cache = getattr(self, "_length_cache", None)
        if cache and cache[0] == key:
            return cache[1]
        total = sum(float(getattr(t, "duration", 0) or 0) for t in tracks)
        self._length_cache = (key, total)
        return total

    def _summary_text(self) -> str:
        """Return the "N tracks · length" summary line."""
        if not getattr(self, "_tracks_loaded", True):
            return "Loading…"
        total = len(self._all_tracks)
        if self._filter_active:
            shown = self._filtered_tracks
            text = f"{len(shown):,} of {total:,} tracks"
        else:
            shown = self._all_tracks
            text = f"{total:,} track{'s' if total != 1 else ''}"
        seconds = self._list_length(shown)
        return f"{text}  ·  {format_length(seconds)}" if seconds else text


def numeric_alignment(field_name: str, value):
    """Alignment for a cell, or None for the default (left)."""
    if field_name in RIGHT_ALIGNED_COLUMNS or (isinstance(value, (int, float)) and not isinstance(value, bool)):
        return Qt.AlignRight | Qt.AlignVCenter
    return None
