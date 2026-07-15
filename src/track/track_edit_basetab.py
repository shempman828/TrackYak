# ---------------------------------------------------------------------------
# Base class for all tabs
# ---------------------------------------------------------------------------
from __future__ import annotations

from typing import Any, Dict

from PySide6.QtWidgets import (
    QWidget,
)


class _BaseTab(QWidget):
    """
    Every tab subclass must implement:
      load(tracks)          — populate widgets from the track(s)
      collect_changes()     — return {field_name: new_value} for scalar fields
                              (relationship tabs return {} — they write directly)
    """

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

    def collect_changes(self) -> Dict[str, Any]:
        return {}

    def _mark_dirty(self, field_name: str) -> None:
        self._dirty.add(field_name)

    def _has_changed(self, field_name: str, new_value) -> bool:
        """Return True if new_value differs meaningfully from the original."""
        old = getattr(self.track, field_name, None)
        if old is None and new_value in (None, "", 0, 0.0, False):
            return False
        return str(old).strip() != str(new_value).strip()
