"""A QLineEdit that accepts an integer in a range and returns None when empty."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QIntValidator
from PySide6.QtWidgets import QLineEdit


class OptionalIntEdit(QLineEdit):
    """A QLineEdit accepting integers in [min_value, max_value]; returns None when empty."""

    def __init__(self, placeholder="", parent=None, *, min_value=0, max_value=9999, width=60):
        super().__init__(parent)
        self.setPlaceholderText(placeholder)
        self.setFixedWidth(width)
        self.setAlignment(Qt.AlignCenter)
        self.setValidator(QIntValidator(min_value, max_value, self))

    def get_value_or_none(self):
        text = self.text().strip()
        return int(text) if text else None

    def set_from_db(self, val):
        self.setText(str(int(val)) if val is not None else "")
