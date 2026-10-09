"""Base class for all track edit dialog tabs."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QWidget


class _BaseTab(QWidget):
    """Common interface of a track edit tab: load, collect, refresh and clean up."""

    # Emitted whenever the user edits a scalar field (see _mark_dirty).
    changed = Signal()

    # True for relationship tabs whose add/remove actions write to the DB at once
    # instead of waiting for Save; the dialog shows a notice on these tabs.
    saves_immediately = False

    def __init__(self, tracks: list, controller, parent=None):
        super().__init__(parent)
        self.tracks = tracks  # always a list, even for single-track editing
        self.controller = controller
        self.is_multi = len(tracks) > 1
        self._dirty: set = set()  # scalar fields the user has touched

    @property
    def track(self):
        """Return the first track (the only one when is_multi is False)."""
        return self.tracks[0]

    def load(self, tracks: list) -> None:
        """Fill the widgets from `tracks`."""
        raise NotImplementedError

    def collect_changes(self) -> dict[str, Any]:
        """Return {field_name: new_value} for the scalar fields to save."""
        return {}  # relationship tabs write directly

    def refresh_values(self, tracks: list) -> None:
        """Show fresh values from `tracks` without touching unsaved edits."""

    def cleanup(self) -> None:
        """Stop background work before the dialog destroys the tab."""

    def pending_changes(self) -> set[str]:
        """Return the names of the fields this tab would write on Save."""
        # Called on every edit, so it must be cheap and free of side effects.
        return set(self._dirty)

    def _mark_dirty(self, field_name: str) -> None:
        """Record that the user edited `field_name`."""
        self._dirty.add(field_name)
        self.changed.emit()

    def _has_changed(self, field_name: str, new_value) -> bool:
        """Return True if new_value differs meaningfully from the original."""
        old = getattr(self.track, field_name, None)
        if old is None and new_value in (None, "", 0, 0.0, False):
            return False
        return str(old).strip() != str(new_value).strip()
