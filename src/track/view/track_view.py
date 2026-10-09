"""TrackView: the main library track list (behavior lives in the mixins it combines)."""

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QKeySequence, QShortcut, QStandardItemModel
from PySide6.QtWidgets import QMenu, QToolButton, QVBoxLayout, QWidget

from src.db.db_mapping_tracks import TRACK_FIELDS
from src.foundation.logger_config import logger
from src.track.track_shuffle import play_tracks
from src.track.view.track_table import TrackTable
from src.track.view.track_toolbar import make_primary_tool_button
from src.track.view.track_view_actions import TrackViewActionsMixin
from src.track.view.track_view_columns import TrackViewColumnsMixin
from src.track.view.track_view_data import TrackViewDataMixin
from src.track.view.track_view_editing import TrackViewEditingMixin
from src.track.view.track_view_filter import FilterWorker
from src.track.view.track_view_search import TrackViewSearchMixin
from src.track.view.track_view_toolbar import TrackViewToolbarMixin


class TrackView(QWidget, TrackViewToolbarMixin, TrackViewColumnsMixin, TrackViewDataMixin, TrackViewSearchMixin, TrackViewActionsMixin, TrackViewEditingMixin):
    """Main library track view: the whole library, loaded once and pushed to the model in batches."""

    _shuffle_default_order = True
    # The whole library: filter once typing pauses, not on every keystroke.
    _search_debounce_ms = 250
    _empty_library_text = "Your library is empty.\nUse File ▸ Import Directory to add music."

    def __init__(self, controller, music_player):
        super().__init__()
        self.controller = controller
        self.player = music_player
        self.track_fields = TRACK_FIELDS
        self._filtered_tracks: list = []
        self._all_tracks: list = []
        self._loaded_count: int = 0
        self._filter_active: bool = False
        self._tracks_loaded: bool = False
        self._filter_worker: FilterWorker | None = None
        self._sort_worker = None
        self._lookup_thread = None
        self._lookup_worker = None
        self._artist_name_cache: dict = {}
        self._artist_sort_cache: dict = {}
        self._album_cache: dict = {}
        self._disc_number_cache: dict = {}

        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(6, 6, 6, 6)
        self.layout.setSpacing(6)

        # ── Toolbar: search + scope, playback, queue/view menus, summary ──
        self._build_toolbar()

        # ── Table setup ───────────────────────────────────────────────────
        self._initialize_columns()

        self.model = QStandardItemModel()
        self.table = TrackTable(self)
        self.table.setModel(self.model)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.show_context_menu)

        self._setup_table()
        self.load_column_state()

        self.layout.addWidget(self.table, 1)
        self._install_table_chrome()

        self.table.doubleClicked.connect(self.on_double_clicked)
        self.table.verticalScrollBar().valueChanged.connect(self._on_scroll)

        # ── Keyboard shortcuts ────────────────────────────────────────────
        copy_shortcut = QShortcut(QKeySequence.Copy, self.table)
        copy_shortcut.activated.connect(self._copy_selected_rows)

        delete_shortcut = QShortcut(QKeySequence.Delete, self.table)
        delete_shortcut.activated.connect(self.delete_selected_tracks)

        # Initial load runs on the next event-loop pass, so the window can
        # paint the "Loading library…" state before the full-library query.
        self._update_status()
        logger.debug("TrackView initialized, scheduling initial track load")
        QTimer.singleShot(0, self._initial_load)

    def _initial_load(self):
        """Load the library unless a nav revisit already did."""
        # A nav revisit may already have loaded the library.
        if not self._tracks_loaded:
            self.load_tracks_on_startup()

    # ── Toolbar mixin hooks ───────────────────────────────────────────────

    def _add_toolbar_actions(self, toolbar):
        """Add Play, Shuffle, the Queue menu and the View menu."""
        toolbar.add_action(make_primary_tool_button("▶  Play", "Play the listed tracks in order", lambda: self._play_visible(shuffle=False)))
        toolbar.add_action(make_primary_tool_button("⤮  Shuffle", "Shuffle the listed tracks and play", lambda: self._play_visible(shuffle=True)))

        queue_btn = QToolButton(self)
        queue_btn.setText("＋ Queue")  # noqa: RUF001
        queue_btn.setToolTip("Add tracks to the playback queue")
        queue_btn.setPopupMode(QToolButton.InstantPopup)
        queue_menu = QMenu(queue_btn)
        queue_menu.addAction("Add Listed Tracks to Queue", self._add_filtered_to_queue)
        queue_menu.addAction("Shuffle Listed Tracks to Queue", lambda: self._add_filtered_to_queue(shuffle=True))
        queue_menu.addSeparator()
        queue_menu.addAction("Shuffle Entire Library to Queue", self._add_all_to_queue)
        queue_btn.setMenu(queue_menu)
        toolbar.add_action(queue_btn)

        view_btn = QToolButton(self)
        view_btn.setText("⚙ View")
        view_btn.setToolTip("Columns and library refresh. Right-click a column header to show or hide columns.")
        view_btn.setPopupMode(QToolButton.InstantPopup)
        view_menu = QMenu(view_btn)
        view_menu.addAction("Column Order && Visibility…", self.show_column_customization)
        view_menu.addSeparator()
        view_menu.addAction("Refresh Library", self._force_reload)
        view_btn.setMenu(view_menu)
        toolbar.add_action(view_btn)

    def _selection_bar_actions(self):
        """Return the (play_next, queue, edit, delete) callables for the selection bar."""
        return (lambda: self.add_selected_to_queue(insert_next=True), lambda: self.add_selected_to_queue(False), self.edit_selected_track, self.delete_selected_tracks)

    def _now_playing_source(self):
        """Return the player, for the now-playing highlight."""
        return self.player

    def _play_visible(self, shuffle: bool):
        """Play the listed tracks, in order or shuffled."""
        play_tracks(self, self.controller, self._visible_source(), shuffle=shuffle)
