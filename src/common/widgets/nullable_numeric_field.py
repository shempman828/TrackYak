"""Helpers for QLineEdit-based numeric fields that can be cleared to NULL.

An empty QLineEdit already means "nothing typed" for free, so an optional
int/float field just needs a validator (plus a group-separator variant for
large numbers) and consistent formatting on blur -- no dedicated widget
subclass required.
"""

from PySide6.QtGui import QDoubleValidator, QIntValidator
from PySide6.QtWidgets import QLineEdit


class _GroupedIntValidator(QIntValidator):
    """QIntValidator that also accepts ',' thousands separators while typing.

    estimated_sales displays with grouping (e.g. "1,234,567"); QIntValidator
    itself only understands plain digits, so a stripped copy is validated
    and the group-separated text is passed through unchanged.
    """

    def validate(self, input: str, pos: int):
        state, _, _ = super().validate(input.replace(",", ""), pos)
        return state, input, pos


def create_nullable_int_field(
    min_val: int, max_val: int, current_value=None, group_separator: bool = False
) -> QLineEdit:
    """A QLineEdit for an optional int field -- empty text means NULL."""
    edit = QLineEdit()
    validator = (
        _GroupedIntValidator(int(min_val), int(max_val), edit)
        if group_separator
        else QIntValidator(int(min_val), int(max_val), edit)
    )
    edit.setValidator(validator)
    edit.editingFinished.connect(
        lambda: set_nullable_field_value(
            edit, nullable_field_value(edit), group_separator=group_separator
        )
    )
    set_nullable_field_value(edit, current_value, group_separator=group_separator)
    return edit


def create_nullable_float_field(
    min_val: float, max_val: float, current_value=None, decimals: int = 4
) -> QLineEdit:
    """A QLineEdit for an optional float field -- empty text means NULL."""
    edit = QLineEdit()
    validator = QDoubleValidator(min_val, max_val, decimals, edit)
    validator.setNotation(QDoubleValidator.Notation.StandardNotation)
    edit.setValidator(validator)
    edit.editingFinished.connect(
        lambda: set_nullable_field_value(
            edit, nullable_field_value(edit, is_float=True), is_float=True, decimals=decimals
        )
    )
    set_nullable_field_value(edit, current_value, is_float=True, decimals=decimals)
    return edit


def nullable_field_value(edit: QLineEdit, is_float: bool = False):
    """Return the numeric value of a nullable field, or None if it's empty."""
    text = edit.text().strip().replace(",", "")
    if not text:
        return None
    try:
        return float(text) if is_float else int(text)
    except ValueError:
        return None


def set_nullable_field_value(
    edit: QLineEdit, value, is_float: bool = False, decimals: int = 4, group_separator: bool = False
) -> None:
    """Write a value (or None) into a nullable field without emitting signals."""
    if value is None:
        text = ""
    elif is_float:
        text = f"{value:.{decimals}f}"
    elif group_separator:
        text = f"{int(value):,}"
    else:
        text = str(int(value))
    edit.blockSignals(True)
    try:
        edit.setText(text)
    finally:
        edit.blockSignals(False)
