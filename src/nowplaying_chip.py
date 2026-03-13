# ──────────────────────────────────────────────────────────────────────────────
#  Chip widgets
# ──────────────────────────────────────────────────────────────────────────────
from typing import List

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QScrollArea, QWidget


class _Chip(QLabel):
    """Pill-shaped metadata chip."""

    _SS = (
        "QLabel { background: rgba(133,153,234,0.13);"
        " border: 1px solid rgba(133,153,234,0.28);"
        " border-radius: 10px; color: rgba(200,208,244,0.80);"
        " font-size: 10px; padding: 2px 10px; }"
    )

    def __init__(self, icon_str: str, value: str, parent=None):
        super().__init__(parent)
        self._icon = icon_str
        self.setStyleSheet(self._SS)
        self.set_value(value)

    def set_value(self, value: str):
        self.setText(f"{self._icon}  {value}" if self._icon else value)


class _ScrollingChipRow(QScrollArea):
    """Horizontally scrolling row of chips, no scrollbar visible."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setFixedHeight(36)
        self.setStyleSheet("background: transparent; border: none;")
        self.setWidgetResizable(False)

        self._inner = QWidget()
        self._inner.setStyleSheet("background: transparent;")
        self._row = QHBoxLayout(self._inner)
        self._row.setContentsMargins(0, 4, 0, 4)
        self._row.setSpacing(6)
        self.setWidget(self._inner)

    def set_chips(self, chips: List[_Chip]):
        while self._row.count():
            item = self._row.takeAt(0)
            if item.widget():
                item.widget().setParent(None)
        for chip in chips:
            self._row.addWidget(chip)
        self._row.addStretch()
        self._inner.adjustSize()
