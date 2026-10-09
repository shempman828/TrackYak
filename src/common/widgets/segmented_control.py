"""
SegmentedControl: a row of joined, mutually exclusive toggle buttons -- the
macOS/iOS-style segmented picker -- for short fixed choices (view switchers,
modes, presets) that read faster laid out than hidden in a QComboBox.

Exposes the QComboBox surface callers usually touch (currentIndex/Text,
setCurrentIndex/Text, currentIndexChanged/currentTextChanged, count,
itemText), so it drops in where a combo with a handful of items used to sit.
As with QComboBox, programmatic changes emit the change signals too; wrap
them in blockSignals() to load state silently.

Styled in dark_mode.qss via QWidget#SegmentedControl and each button's
`segment` property ("first" | "middle" | "last" | "only").
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QButtonGroup, QHBoxLayout, QPushButton, QWidget


class SegmentedControl(QWidget):
    currentIndexChanged = Signal(int)
    currentTextChanged = Signal(str)

    def __init__(self, options: list[str], parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("SegmentedControl")
        self.setAttribute(Qt.WA_StyledBackground, True)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        last = len(options) - 1
        for index, label in enumerate(options):
            button = QPushButton(label)
            button.setCheckable(True)
            button.setCursor(Qt.PointingHandCursor)
            if last == 0:
                position = "only"
            elif index == 0:
                position = "first"
            elif index == last:
                position = "last"
            else:
                position = "middle"
            button.setProperty("segment", position)
            layout.addWidget(button)
            self._group.addButton(button, index)
        self._group.idClicked.connect(self._on_clicked)

        self._current = 0 if options else -1
        if options:
            self._group.button(0).setChecked(True)

    # -- QComboBox-like API ---------------------------------------------------

    def count(self) -> int:
        return len(self._group.buttons())

    def itemText(self, index: int) -> str:
        button = self._group.button(index)
        return button.text() if button else ""

    def setItemText(self, index: int, text: str) -> None:
        button = self._group.button(index)
        if button:
            button.setText(text)

    def setItemToolTip(self, index: int, tip: str) -> None:
        button = self._group.button(index)
        if button:
            button.setToolTip(tip)

    def button(self, index: int) -> QPushButton | None:
        """The segment's QPushButton, for per-segment properties (e.g. an attention dot)."""
        return self._group.button(index)

    def currentIndex(self) -> int:
        return self._current

    def currentText(self) -> str:
        return self.itemText(self._current)

    def setCurrentIndex(self, index: int) -> None:
        button = self._group.button(index)
        if button is None:
            return
        button.setChecked(True)
        self._set_current(index)

    def setCurrentText(self, text: str) -> None:
        for button in self._group.buttons():
            if button.text() == text:
                self.setCurrentIndex(self._group.id(button))
                return

    # -- internals ------------------------------------------------------------

    def _on_clicked(self, index: int) -> None:
        self._set_current(index)

    def _set_current(self, index: int) -> None:
        if index == self._current:
            return
        self._current = index
        self.currentIndexChanged.emit(index)
        self.currentTextChanged.emit(self.itemText(index))
