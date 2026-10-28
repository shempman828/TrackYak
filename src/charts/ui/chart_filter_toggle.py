"""Shared filter controls of the chart tabs: the match-status chip toggle and the chart combo."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QButtonGroup, QComboBox, QHBoxLayout, QPushButton, QWidget

OPTIONS = ["All", "Matched Only", "Unmatched Only"]
ALL_CHARTS_LABEL = "All Charts"


class MatchFilterToggle(QWidget):
    """Three-way "All / Matched Only / Unmatched Only" chip toggle with a QComboBox-like API."""

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
            # Same filterChip QSS idiom as the influences view's Legend button.
            button.setProperty("class", "filterChip")
            button.setCheckable(True)
            button.setCursor(Qt.PointingHandCursor)
            button.clicked.connect(lambda _checked, i=index: self.currentIndexChanged.emit(i))
            layout.addWidget(button)
            self._group.addButton(button, index)
        self._group.button(0).setChecked(True)

    def currentText(self) -> str:
        """Return the label of the checked option."""
        return OPTIONS[max(self._group.checkedId(), 0)]

    def currentIndex(self) -> int:
        """Return the index of the checked option."""
        return self._group.checkedId()


def repopulate_chart_combo(combo: QComboBox, charts: list, lead_label: str | None = ALL_CHARTS_LABEL) -> list:
    """Refill combo from Chart rows, keep the current selection by text, and return [(key, id, name)]."""
    # The revisit-refresh calls this on every return to ChartsView; keeping the
    # selection stops the combo snapping back to its first item.
    rows = [(c.chart_key, c.chart_id, c.chart_name) for c in charts]
    prev = combo.currentText()
    combo.blockSignals(True)
    combo.clear()
    if lead_label is not None:
        combo.addItem(lead_label)
    for _, _, name in rows:
        combo.addItem(name)
    restored = combo.findText(prev)
    combo.setCurrentIndex(restored if restored >= 0 else 0)
    combo.blockSignals(False)
    return rows
