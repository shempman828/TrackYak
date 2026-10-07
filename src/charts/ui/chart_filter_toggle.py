"""
chart_filter_toggle.py

MatchFilterToggle: a three-option pill-chip segmented control ("All" /
"Matched Only" / "Unmatched Only"), replacing the plain QComboBox
match-status filter previously used by ChartWeekBrowserTab and
ChartSearchTab. Reuses the app's existing filterChip QSS idiom (see the
Legend button in influences_view.py) instead of introducing a new visual
language -- a fixed three-way choice reads faster as chips than as a combo
box.

Exposes the same currentText()/currentIndexChanged(int) surface the host
tabs already call on their match_filter attribute, so it drops in wherever
a QComboBox with those three items used to sit.
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QButtonGroup, QHBoxLayout, QPushButton, QWidget

OPTIONS = ["All", "Matched Only", "Unmatched Only"]


class MatchFilterToggle(QWidget):
    currentIndexChanged = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        for index, label in enumerate(OPTIONS):
            button = QPushButton(label)
            button.setProperty("class", "filterChip")
            button.setCheckable(True)
            button.setCursor(Qt.PointingHandCursor)
            button.clicked.connect(lambda _checked, i=index: self.currentIndexChanged.emit(i))
            layout.addWidget(button)
            self._group.addButton(button, index)
        self._group.button(0).setChecked(True)

    def currentText(self) -> str:
        return OPTIONS[max(self._group.checkedId(), 0)]

    def currentIndex(self) -> int:
        return self._group.checkedId()
