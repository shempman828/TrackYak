# ---------------------------------------------------------------------------
# Base class for all tabs
# ---------------------------------------------------------------------------
from __future__ import annotations

from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QWidget


class _BaseTab(QWidget):
    """
    Every tab subclass must implement:
      load(tracks)          — populate widgets from the track(s)
      collect_changes()     — return {field_name: new_value} for scalar fields
                              (relationship tabs return {} — they write directly)
    """

    # Emitted whenever the user edits a scalar field (see _mark_dirty), so the
    # dialog can refresh its unsaved-change count and per-tab dirty markers.
    changed = Signal()

    # True for relationship tabs (genres, roles, places, …) whose add/remove
    # actions write to the DB at once instead of waiting for Save. The dialog
    # shows a notice on these tabs so the two save models aren't confused.
    saves_immediately = False

    def __init__(self, tracks: list, controller, parent=None):
        super().__init__(parent)
        # Always a list — even for single-track editing
        self.tracks = tracks
        self.controller = controller
        self.is_multi = len(tracks) > 1
        # Tracks which scalar fields the user has touched
        self._dirty: set = set()

    @property
    def track(self):
        """Convenience: the single track (only valid when is_multi is False)."""
        return self.tracks[0]

    def load(self, tracks: list) -> None:
        raise NotImplementedError

    def collect_changes(self) -> dict[str, Any]:
        return {}

    def refresh_values(self, tracks: list) -> None:
        """Re-sync displayed values from `tracks` after a background update
        (e.g. audio analysis) without disturbing unsaved edits. Default is a
        no-op; override in tabs that display fields analysis can populate."""

    def cleanup(self) -> None:
        """Called when the owning dialog is closing. Default is a no-op;
        override in tabs that own background threads or other resources
        that must be stopped before the tab is destroyed."""

    def pending_changes(self) -> set[str]:
        """Names of the fields this tab would write on Save. Cheap and free
        of side effects (unlike collect_changes) -- called on every edit to
        keep the dialog's unsaved-change count current."""
        return set(self._dirty)

    def _mark_dirty(self, field_name: str) -> None:
        self._dirty.add(field_name)
        self.changed.emit()

    def _has_changed(self, field_name: str, new_value) -> bool:
        """Return True if new_value differs meaningfully from the original."""
        old = getattr(self.track, field_name, None)
        if old is None and new_value in (None, "", 0, 0.0, False):
            return False
        return str(old).strip() != str(new_value).strip()
