# track_edit.py
"""
Track editing dialog.
"""

from __future__ import annotations

from typing import Any

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
from sqlalchemy.exc import SQLAlchemyError

from src.core.logger_config import logger
from src.track.track_edit_advanced import AdvancedTab
from src.track.track_edit_album import AlbumsTab
from src.track.track_edit_awards import AwardsTab
from src.track.track_edit_basetab import _BaseTab
from src.track.track_edit_fieldform import FieldFormTab
from src.track.track_edit_genres import GenresTab
from src.track.track_edit_identity import IdentificationTab
from src.track.track_edit_lyrics import LyricsTab
from src.track.track_edit_moods import MoodsTab
from src.track.track_edit_places import PlacesTab
from src.track.track_edit_roles import RolesTab
from src.track.track_edit_samples import SamplesTab
from src.track.track_edit_usedin import UsedInTab

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
        track_or_tracks: Any | list,
        controller,
        parent=None,
    ):
        # Qt.Window makes this a proper independent top-level window
        # so it can be moved freely, separate from the parent window.
        super().__init__(parent, Qt.Window)
        # Dialog is shown non-modally (see callers' .show() usage) so it
        # doesn't block the parent window; clean up automatically on close
        # since there's no exec() return value to trigger disposal.
        self.setAttribute(Qt.WA_DeleteOnClose)

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

        # _build_ui() selects sidebar row 0, which builds and loads that
        # tab via _on_nav -> _ensure_tab_built. Every other tab builds and
        # loads itself lazily on first visit.
        self._build_ui()

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

        # Tabs are built and loaded lazily, on first navigation to each one
        # (see _ensure_tab_built), rather than all 18 up front -- some tabs
        # (e.g. Samples, Roles) do real DB work in __init__/load(), which
        # used to make every dialog open pay for every tab regardless of
        # which ones the user actually visits. Only the sidebar row is
        # cheap to build eagerly, so that's all _add_tab does here.
        self._tab_factories: list = []
        self._tabs: list[_BaseTab | None] = []
        self._add_tab(
            "Basic", lambda: FieldFormTab("Basic", self.tracks, self.controller)
        )
        self._add_tab("Artists & Roles", lambda: RolesTab(self.tracks, self.controller))
        self._add_tab("Albums", lambda: AlbumsTab(self.tracks, self.controller))
        self._add_tab(
            "Dates", lambda: FieldFormTab("Date", self.tracks, self.controller)
        )
        self._add_tab("Genres", lambda: GenresTab(self.tracks, self.controller))
        self._add_tab(
            "Description",
            lambda: FieldFormTab("Description", self.tracks, self.controller),
        )
        self._add_tab("Lyrics", lambda: LyricsTab(self.tracks, self.controller))
        self._add_tab(
            "Classical",
            lambda: FieldFormTab("Classical", self.tracks, self.controller),
        )
        self._add_tab(
            "Properties",
            lambda: FieldFormTab("Properties", self.tracks, self.controller),
        )
        self._add_tab("Moods", lambda: MoodsTab(self.tracks, self.controller))
        self._add_tab("Places", lambda: PlacesTab(self.tracks, self.controller))
        self._add_tab("Awards", lambda: AwardsTab(self.tracks, self.controller))
        self._add_tab(
            "User Data", lambda: FieldFormTab("User", self.tracks, self.controller)
        )
        self._add_tab(
            "Identification",
            lambda: IdentificationTab(self.tracks, self.controller),
        )
        self._add_tab("Used In", lambda: UsedInTab(self.tracks, self.controller))
        self._add_tab(
            "Aliases", lambda: FieldFormTab("Alias", self.tracks, self.controller)
        )
        self._add_tab("Samples", lambda: SamplesTab(self.tracks, self.controller))
        self._add_tab("Advanced", self._make_advanced_tab)

        # Keyboard shortcuts Ctrl+1 … Ctrl+9 for first 9 tabs
        for i in range(min(9, len(self._tabs))):
            sc = QShortcut(QKeySequence(f"Ctrl+{i + 1}"), self)
            sc.activated.connect(lambda idx=i: self._sidebar.setCurrentRow(idx))

        self._sidebar.setCurrentRow(0)

    def _make_advanced_tab(self):
        advanced_tab = AdvancedTab(self.tracks, self.controller)
        advanced_tab.tracks_analyzed.connect(self._on_tracks_analyzed)
        return advanced_tab

    def _add_tab(self, label: str, factory):
        self._sidebar.addItem(label)
        self._stack.addWidget(QWidget())  # placeholder, replaced on first visit
        self._tab_factories.append(factory)
        self._tabs.append(None)

    def _on_nav(self, row: int):
        self._ensure_tab_built(row)
        self._stack.setCurrentIndex(row)

    def _ensure_tab_built(self, row: int) -> _BaseTab:
        tab = self._tabs[row]
        if tab is not None:
            return tab

        factory = self._tab_factories[row]
        try:
            tab = factory()
            tab.load(self.tracks)
        except Exception as e:
            # Intentional broad boundary catch: dispatches to 18 heterogeneous
            # tab classes (DB reads, dict/attr access, UI construction) via the
            # shared _BaseTab interface -- a bug in any one tab must not block
            # the whole edit dialog from opening.
            logger.error(f"Error building/loading tab at row {row}: {e}", exc_info=True)
            tab = QWidget()

        placeholder = self._stack.widget(row)
        self._stack.insertWidget(row, tab)
        self._stack.removeWidget(placeholder)
        placeholder.deleteLater()
        self._tabs[row] = tab
        return tab

    # ── Data ──────────────────────────────────────────────────────────────

    def _on_tracks_analyzed(self):
        """Audio analysis updated the track(s) in place (e.g. bpm, key,
        gain) — refresh every already-built tab's displayed values. This
        leaves any unsaved edits the user already made untouched. Tabs the
        user hasn't visited yet pick up the fresh values naturally when
        they're built."""
        for tab in self._tabs:
            if tab is None:
                continue
            try:
                tab.refresh_values(self.tracks)
            except Exception as e:
                # Intentional broad boundary catch: refresh_values is
                # overridden differently by each of the 18 tab classes -- a
                # bug in one tab's refresh must not stop the rest from
                # picking up the new analysis values.
                logger.error(
                    f"Error refreshing tab {type(tab).__name__}: {e}", exc_info=True
                )

    # ── Save ──────────────────────────────────────────────────────────────

    def _on_save(self):
        try:
            # Collect scalar field changes from all tabs
            all_changes: dict[str, Any] = {}
            for tab in self._tabs:
                if tab is None:
                    continue  # never visited -> no user edits to collect
                try:
                    changes = tab.collect_changes()
                    all_changes.update(changes)
                except Exception as e:
                    # Intentional broad boundary catch: collect_changes is
                    # overridden differently by each of the 18 tab classes --
                    # a bug in one tab's collection must not prevent saving
                    # the changes already gathered from the others.
                    logger.exception(
                        f"Error collecting changes from {type(tab).__name__}: {e}"
                    )

            if all_changes:
                track_ids = [track.track_id for track in self.tracks]
                self.controller.update.update_entities(
                    "Track", track_ids, **all_changes
                )
                logger.info(
                    f"Saved {len(self.tracks)} track(s), "
                    f"fields: {list(all_changes.keys())}"
                )

            self.field_modified.emit()
            self.accept()

        except (SQLAlchemyError, RuntimeError) as e:
            logger.error(f"Error saving track(s): {e}", exc_info=True)
            QMessageBox.critical(self, "Save Error", f"Failed to save:\n{e}")

    # ── Cleanup ───────────────────────────────────────────────────────────

    def closeEvent(self, event) -> None:
        for tab in self._tabs:
            if tab is None:
                continue  # never built -> nothing to clean up
            try:
                tab.cleanup()
            except Exception as e:
                # Intentional broad boundary catch: shutdown/cleanup code for
                # 18 heterogeneous tab classes (some tear down background
                # QThreads) -- one tab failing to clean up must not stop the
                # rest from releasing their resources while the dialog closes.
                logger.exception(f"Error cleaning up tab {type(tab).__name__}: {e}")
        super().closeEvent(event)


# ---------------------------------------------------------------------------
# Backwards-compatibility alias so existing callers don't need changes
# ---------------------------------------------------------------------------

# Any code that imported MultiTrackEditDialog can now pass a list to
# TrackEditDialog instead. We keep the name around to avoid import errors.
MultiTrackEditDialog = TrackEditDialog
