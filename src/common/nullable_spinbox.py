from PySide6.QtCore import Signal
from PySide6.QtWidgets import QDoubleSpinBox, QHBoxLayout, QSpinBox, QWidget

# QSpinBox is backed by a 32-bit C++ int, so this is the true floor. When a
# caller's min_val already sits on it, there's no room left below to carve
# out a sentinel, so the usable minimum is nudged up by one instead.
_INT32_MIN = -2_147_483_648

# Text shown when the spin box sits on its sentinel (i.e. "no value set").
_EMPTY_TEXT = "—"


class _NoScrollSpinBox(QSpinBox):
    """QSpinBox that ignores wheel events unless it already has focus.

    Qt delivers wheel events to whatever widget is under the cursor,
    regardless of focus, so merely scrolling a form past a spin box
    silently bumps its value and fires valueChanged.
    """

    def wheelEvent(self, event):
        if not self.hasFocus():
            event.ignore()
        else:
            super().wheelEvent(event)


class _NoScrollDoubleSpinBox(QDoubleSpinBox):
    """QDoubleSpinBox variant of _NoScrollSpinBox — see its docstring."""

    def wheelEvent(self, event):
        if not self.hasFocus():
            event.ignore()
        else:
            super().wheelEvent(event)


class NullableSpinBox(QWidget):
    """A QSpinBox/QDoubleSpinBox that can represent NULL without a helper checkbox.

    The widget's range is extended one step below `min_val` and that extra
    slot is given special-value text ("—"), so stepping/typing below the
    configured minimum is how you clear the field back to NULL, and any
    other value means it's set. This keeps the control a single, always-
    editable spin box instead of pairing it with a "Set" checkbox.
    """

    valueChanged = Signal()

    def __init__(
        self,
        min_val=0,
        max_val=9999,
        current_value=None,
        is_float: bool = False,
        decimals: int = 4,
        step=None,
        parent=None,
    ):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._is_float = is_float

        if not is_float and min_val <= _INT32_MIN:
            min_val = _INT32_MIN + 1
        self._sentinel = min_val - 1

        self._spin = _NoScrollDoubleSpinBox() if is_float else _NoScrollSpinBox()
        self._spin.setRange(self._sentinel, max_val)
        self._spin.setSpecialValueText(_EMPTY_TEXT)
        if is_float:
            self._spin.setDecimals(decimals)
            if step is not None:
                self._spin.setSingleStep(step)

        if current_value is not None:
            self._spin.setValue(current_value if is_float else int(current_value))
        else:
            self._spin.setValue(self._sentinel)

        self._spin.valueChanged.connect(lambda _v: self.valueChanged.emit())

        layout.addWidget(self._spin)

    def value(self):
        """Return the numeric value, or None if the spin box is at its sentinel."""
        v = self._spin.value()
        return None if v <= self._sentinel else v

    def setValue(self, value) -> None:
        """Write a value (or None) into the widget without emitting signals."""
        self._spin.blockSignals(True)
        try:
            self._spin.setValue(self._sentinel if value is None else value)
        finally:
            self._spin.blockSignals(False)
