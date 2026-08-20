"""
threshold_tier_widget.py

ThresholdTierWidget: a row of chips ("Min 10 / Min 100 / Min 1000") that
switches which threshold tier a paired LeaderboardListWidget displays --
used by every power-of-10 stat (publishers, places, countries, genres,
composers, artist-by-gender, per-role leaderboards). A plain button row
rather than a QPainter widget, since it's an interactive control, not a
data visualization.
"""

from typing import Sequence

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QButtonGroup, QHBoxLayout, QPushButton, QWidget


class ThresholdTierWidget(QWidget):
    """Emits `tier_changed(threshold: int)` whenever the user picks a
    different minimum-sample-size tier."""

    tier_changed = Signal(int)

    def __init__(self, thresholds: Sequence[int] = (10, 100, 1000), parent=None):
        super().__init__(parent)
        self._thresholds = list(thresholds)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self._button_group = QButtonGroup(self)
        self._button_group.setExclusive(True)

        for i, threshold in enumerate(self._thresholds):
            button = QPushButton(f"Min {threshold:,}")
            button.setCheckable(True)
            if i == 0:
                button.setChecked(True)
            self._button_group.addButton(button, threshold)
            layout.addWidget(button)

        layout.addStretch()
        self._button_group.idClicked.connect(self.tier_changed.emit)

    def current_threshold(self) -> int:
        checked = self._button_group.checkedButton()
        return self._button_group.id(checked) if checked else self._thresholds[0]

    def set_thresholds_available(self, available: Sequence[int]):
        """Disable chips for tiers with no qualifying data, so the user
        can't select an empty tier. If the currently-checked tier just
        became unavailable, switch to the first available one instead of
        leaving a disabled chip checked (and its paired list empty)."""
        for button in self._button_group.buttons():
            threshold = self._button_group.id(button)
            button.setEnabled(threshold in available)

        checked = self._button_group.checkedButton()
        current = self._button_group.id(checked) if checked else None
        if available and current not in available:
            for button in self._button_group.buttons():
                if self._button_group.id(button) == available[0]:
                    button.setChecked(True)
                    self.tier_changed.emit(available[0])
                    break
