from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QSpinBox, QWidget


class NullableSpinBox(QWidget):
    """A QSpinBox paired with a 'Set' checkbox.

    When the checkbox is unchecked the value is treated as NULL on save.
    When checked the spin-box value is used.

    This solves the problem of not being able to clear a QSpinBox back to NULL
    once a value has been entered.
    """

    def __init__(
        self, min_val: int = 0, max_val: int = 9999, current_value=None, parent=None
    ):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self._spin = QSpinBox()
        self._spin.setRange(min_val, max_val)

        self._check = QCheckBox("Set")
        self._check.setToolTip("Uncheck to save this field as empty (no value).")

        if current_value is not None:
            self._spin.setValue(int(current_value))
            self._check.setChecked(True)
        else:
            self._spin.setValue(min_val)
            self._check.setChecked(False)
            self._spin.setEnabled(False)

        self._check.toggled.connect(self._spin.setEnabled)

        layout.addWidget(self._check)
        layout.addWidget(self._spin)
        layout.addStretch()

    def value(self):
        """Return the int value, or None if the checkbox is unchecked."""
        return self._spin.value() if self._check.isChecked() else None
