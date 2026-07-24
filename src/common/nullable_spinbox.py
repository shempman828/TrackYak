from PySide6.QtCore import Signal
from PySide6.QtWidgets import QCheckBox, QDoubleSpinBox, QHBoxLayout, QSpinBox, QWidget


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
    """A QSpinBox/QDoubleSpinBox paired with a 'Set' checkbox.

    When the checkbox is unchecked the value is treated as NULL on save.
    When checked the spin-box value is used.

    This solves the problem of not being able to clear a QSpinBox back to
    NULL once a value has been entered.
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
        checkbox_text: str = "Set",
        parent=None,
    ):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self._is_float = is_float
        self._spin = _NoScrollDoubleSpinBox() if is_float else _NoScrollSpinBox()
        self._spin.setRange(min_val, max_val)
        if is_float:
            self._spin.setDecimals(decimals)
            if step is not None:
                self._spin.setSingleStep(step)

        self._check = QCheckBox(checkbox_text)
        self._check.setToolTip("Uncheck to save this field as empty (no value).")

        if current_value is not None:
            self._spin.setValue(current_value if is_float else int(current_value))
            self._check.setChecked(True)
        else:
            self._spin.setValue(min_val)
            self._check.setChecked(False)
            self._spin.setEnabled(False)

        self._check.toggled.connect(self._spin.setEnabled)
        self._check.toggled.connect(lambda _checked: self.valueChanged.emit())
        self._spin.valueChanged.connect(lambda _v: self.valueChanged.emit())

        layout.addWidget(self._check)
        layout.addWidget(self._spin, 1)
        if checkbox_text:
            layout.addStretch()

    def value(self):
        """Return the numeric value, or None if the checkbox is unchecked."""
        return self._spin.value() if self._check.isChecked() else None

    def setValue(self, value) -> None:
        """Write a value (or None) into the widget without emitting signals."""
        self._spin.blockSignals(True)
        self._check.blockSignals(True)
        try:
            if value is None:
                self._check.setChecked(False)
                self._spin.setEnabled(False)
                self._spin.setValue(self._spin.minimum())
            else:
                self._spin.setValue(value)
                self._check.setChecked(True)
                self._spin.setEnabled(True)
        finally:
            self._spin.blockSignals(False)
            self._check.blockSignals(False)
