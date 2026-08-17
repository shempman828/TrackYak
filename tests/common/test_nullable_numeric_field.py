"""Regression tests for the nullable QLineEdit numeric field helpers.

#263: with the earlier QSpinBox-based NullableSpinBox implementation, an
empty field showed a raw sentinel value like -2147483648 instead of
blanking itself when a field had no configured `min` (e.g. Classical tab
fields such as Classical Catalog Number / Movement Number).

#340: clearing the field's text (select-all + Delete, or Backspace to
empty) and tabbing away silently reverted to the old value instead of
committing NULL -- an inherent limitation of QAbstractSpinBox's blur-time
CorrectToPreviousValue correction. Fixed by dropping the QSpinBox-based
widget class entirely in favor of a plain QLineEdit + validator, where an
empty box natively means None.
"""

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from src.common.nullable_numeric_field import (
    create_nullable_float_field,
    create_nullable_int_field,
    nullable_field_value,
    set_nullable_field_value,
)


def test_empty_int_field_has_no_value(qapp):
    edit = create_nullable_int_field(
        min_val=-2_147_483_648, max_val=2_147_483_647, current_value=None
    )
    assert nullable_field_value(edit) is None
    assert edit.text() == ""


def test_empty_float_field_has_no_value(qapp):
    edit = create_nullable_float_field(min_val=0, max_val=9999, current_value=None)
    assert nullable_field_value(edit, is_float=True) is None
    assert edit.text() == ""


def test_set_value_then_clear_blanks_display(qapp):
    edit = create_nullable_int_field(
        min_val=-2_147_483_648, max_val=2_147_483_647, current_value=67
    )
    assert nullable_field_value(edit) == 67
    assert edit.text() == "67"

    set_nullable_field_value(edit, None)
    assert nullable_field_value(edit) is None
    assert edit.text() == ""


def test_clearing_typed_text_commits_null(qapp):
    """#340: select-all + Delete + blur must leave the field at None, not
    silently revert to the previous value."""
    edit = create_nullable_int_field(min_val=1900, max_val=2100, current_value=2020)
    assert nullable_field_value(edit) == 2020

    edit.setFocus()
    edit.selectAll()
    QTest.keyClick(edit, Qt.Key.Key_Delete)
    edit.clearFocus()

    assert nullable_field_value(edit) is None
    assert edit.text() == ""


def test_group_separator_formats_and_parses_correctly(qapp):
    edit = create_nullable_int_field(
        min_val=0, max_val=9_999_999, current_value=1234567, group_separator=True
    )
    assert nullable_field_value(edit) == 1234567
    assert edit.text() == "1,234,567"

    set_nullable_field_value(edit, None, group_separator=True)
    assert nullable_field_value(edit) is None
    assert edit.text() == ""
