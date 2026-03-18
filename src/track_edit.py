# track_edit.py
"""
Track editing dialog.
"""

from __future__ import annotations

from typing import Any, Dict, List, Union

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QListWidget,
    QMessageBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from src.logger_config import logger
from src.track_edit_album import AlbumsTab
from src.track_edit_awards import AwardsTab
from src.track_edit_basetab import _BaseTab
from src.track_edit_fieldform import FieldFormTab
from src.track_edit_genres import GenresTab
from src.track_edit_indentity import IdentificationTab
from src.track_edit_lyrics import LyricsTab
from src.track_edit_moods import MoodsTab
from src.track_edit_places import PlacesTab
from src.track_edit_roles import RolesTab
from src.track_edit_samples import SamplesTab
from src.track_edit_usedin import UsedInTab

# ---------------------------------------------------------------------------
# TrackEditDialog — the main dialog
# ---------------------------------------------------------------------------


class TrackEditDialog(QDialog):
    """
    Edit one track — or bulk-edit many at once.

    Usage:
        # Single track
        dlg = TrackEditDialog(track, controller, parent)
        # Multiple tracks
        dlg = TrackEditDialog([t1, t2, t3], controller, parent)
    """

    field_modified = Signal()

    def __init__(
        self,
        track_or_tracks: Union[Any, List],
        controller,
        parent=None,
    ):
        # Qt.Window makes this a proper independent top-level window
        # so it can be moved freely, separate from the parent window.
        super().__init__(parent, Qt.Window)

        # Normalise to a list
        if isinstance(track_or_tracks, list):
            self.tracks = track_or_tracks
        else:
            self.tracks = [track_or_tracks]

        self.controller = controller
        self.is_multi = len(self.tracks) > 1

        # Convenience property
        self.track = self.tracks[0]

        title = (
            f"Edit {len(self.tracks)} Tracks"
            if self.is_multi
            else f"Edit Track: {self.track.track_name}"
        )
        self.setWindowTitle(title)
        self.setMinimumSize(900, 650)

        self._build_ui()
        self._load_all()

    # ── UI Construction ───────────────────────────────────────────────────

    def _build_ui(self):
        root = QHBoxLayout(self)

        # Left sidebar — tab navigation list
        self._sidebar = QListWidget()
        self._sidebar.setFixedWidth(155)
        self._sidebar.currentRowChanged.connect(self._on_nav)
        root.addWidget(self._sidebar)

        # Right side: stacked tab widgets + button box
        right = QVBoxLayout()
        self._stack = QStackedWidget()
        right.addWidget(self._stack)

        btn_box = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        btn_box.accepted.connect(self._on_save)
        btn_box.rejected.connect(self.reject)
        right.addWidget(btn_box)

        right_widget = QWidget()
        right_widget.setLayout(right)
        root.addWidget(right_widget, stretch=1)

        # Build all tabs
        self._tabs: List[_BaseTab] = []
        self._add_tab("Basic Info", FieldFormTab("Basic", self.tracks, self.controller))
        self._add_tab("Lyrics", LyricsTab(self.tracks, self.controller))
        self._add_tab("Dates", FieldFormTab("Date", self.tracks, self.controller))
        self._add_tab(
            "Classical", FieldFormTab("Classical", self.tracks, self.controller)
        )
        self._add_tab(
            "Properties", FieldFormTab("Properties", self.tracks, self.controller)
        )
        self._add_tab("Identification", IdentificationTab(self.tracks, self.controller))
        self._add_tab("User Data", FieldFormTab("User", self.tracks, self.controller))
        self._add_tab("Artists & Roles", RolesTab(self.tracks, self.controller))
        self._add_tab("Genres", GenresTab(self.tracks, self.controller))
        self._add_tab("Places", PlacesTab(self.tracks, self.controller))
        self._add_tab("Moods", MoodsTab(self.tracks, self.controller))
        self._add_tab("Awards", AwardsTab(self.tracks, self.controller))
        self._add_tab("Aliases", FieldFormTab("Alias", self.tracks, self.controller))
        self._add_tab("Samples", SamplesTab(self.tracks, self.controller))
        self._add_tab("Albums", AlbumsTab(self.tracks, self.controller))
        self._add_tab("Used In", UsedInTab(self.tracks, self.controller))
        self._add_tab(
            "Advanced", FieldFormTab("Advanced", self.tracks, self.controller)
        )

        # Keyboard shortcuts Ctrl+1 … Ctrl+9 for first 9 tabs
        for i in range(min(9, len(self._tabs))):
            sc = QShortcut(QKeySequence(f"Ctrl+{i + 1}"), self)
            sc.activated.connect(lambda idx=i: self._sidebar.setCurrentRow(idx))

        self._sidebar.setCurrentRow(0)

    def _add_tab(self, label: str, tab: _BaseTab):
        self._sidebar.addItem(label)
        self._stack.addWidget(tab)
        self._tabs.append(tab)

    def _on_nav(self, row: int):
        self._stack.setCurrentIndex(row)

    # ── Data ──────────────────────────────────────────────────────────────

    def _load_all(self):
        for tab in self._tabs:
            try:
                tab.load(self.tracks)
            except Exception as e:
                logger.error(
                    f"Error loading tab {type(tab).__name__}: {e}", exc_info=True
                )

    # ── Save ──────────────────────────────────────────────────────────────

    def _on_save(self):
        try:
            # Collect scalar field changes from all tabs
            all_changes: Dict[str, Any] = {}
            for tab in self._tabs:
                try:
                    changes = tab.collect_changes()
                    all_changes.update(changes)
                except Exception as e:
                    logger.error(
                        f"Error collecting changes from {type(tab).__name__}: {e}"
                    )

            if all_changes:
                for track in self.tracks:
                    self.controller.update.update_entity(
                        "Track", track.track_id, **all_changes
                    )
                logger.info(
                    f"Saved {len(self.tracks)} track(s), "
                    f"fields: {list(all_changes.keys())}"
                )

            self.field_modified.emit()
            self.accept()

        except Exception as e:
            logger.error(f"Error saving track(s): {e}", exc_info=True)
            QMessageBox.critical(self, "Save Error", f"Failed to save:\n{e}")


# ---------------------------------------------------------------------------
# Backwards-compatibility alias so existing callers don't need changes
# ---------------------------------------------------------------------------

# Any code that imported MultiTrackEditDialog can now pass a list to
# TrackEditDialog instead. We keep the name around to avoid import errors.
MultiTrackEditDialog = TrackEditDialog
