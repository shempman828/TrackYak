"""
track_view.py — TrackView

The widget itself is intentionally thin: it owns __init__ and wiring, while
behavior lives in the mixins below (each is one former section of this file):

    track_view_filter.py    — FilterWorker + shared constants (LAZY_BATCH_SIZE, etc.)
    track_view_toolbar.py   — toolbar / search-column picker construction
    track_view_columns.py   — column setup, visibility, ordering, persistence
    track_view_data.py      — lazy DB loading, batch pagination, sorting
    track_view_search.py    — background search/filter application
    track_view_actions.py   — clipboard, drag/drop, queue, playback
    track_view_editing.py   — edit/delete dialogs, context menu, moods
"""

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut, QStandardItemModel
from PySide6.QtWidgets import QTableView, QVBoxLayout, QWidget

from src.core.logger_config import logger
from src.db.db_mapping_tracks import TRACK_FIELDS
from src.track.track_view_actions import TrackViewActionsMixin
from src.track.track_view_columns import TrackViewColumnsMixin
from src.track.track_view_data import TrackViewDataMixin
from src.track.track_view_editing import TrackViewEditingMixin
from src.track.track_view_filter import FilterWorker  # noqa: F401 (re-exported for callers)
from src.track.track_view_search import TrackViewSearchMixin
from src.track.track_view_toolbar import TrackViewToolbarMixin


class TrackView(
    QWidget,
    TrackViewToolbarMixin,
    TrackViewColumnsMixin,
    TrackViewDataMixin,
    TrackViewSearchMixin,
    TrackViewActionsMixin,
    TrackViewEditingMixin,
):
    """
    Main library track view with lazy loading.

    self._all_tracks       — full track list from DB, loaded ONCE and cached.
    self._loaded_count     — rows currently pushed into the Qt model.
    self._filtered_tracks  — active subset when a search filter is live.
    self._filter_active    — True while a search filter is applied.
    """

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
        self._album_cache: dict = {}
        self._disc_number_cache: dict = {}

        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(4, 4, 4, 4)
        self.layout.setSpacing(4)

        # ── Toolbar row 1: search + column filter + actions ───────────────
        self._build_toolbar()

        # ── Table setup ───────────────────────────────────────────────────
        self._initialize_columns()

        self.model = QStandardItemModel()
        self.table = QTableView(self)
        self.table.setModel(self.model)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.show_context_menu)

        self._setup_table()
        self.load_column_state()

        self.layout.addWidget(self.table)

        self.table.doubleClicked.connect(self.on_double_clicked)
        self.table.verticalScrollBar().valueChanged.connect(self._on_scroll)

        # ── Keyboard shortcuts ────────────────────────────────────────────
        copy_shortcut = QShortcut(QKeySequence.Copy, self.table)
        copy_shortcut.activated.connect(self._copy_selected_rows)

        delete_shortcut = QShortcut(QKeySequence.Delete, self.table)
        delete_shortcut.activated.connect(self.delete_selected_tracks)

        # Initial load
        logger.debug("TrackView initialized, starting initial track load")
        self.load_tracks_on_startup()
