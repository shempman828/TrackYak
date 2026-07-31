"""Regression test for #263: NullableSpinBox showed a raw sentinel value like
-2147483648 instead of blanking itself when a field had no configured `min`
(e.g. Classical tab fields such as Classical Catalog Number / Movement Number).

Root cause: Qt's QAbstractSpinBox.setSpecialValueText("") is a no-op — an
empty string means "no special text", so the box falls back to displaying
the raw numeric sentinel. Fixed by using a single space instead, which Qt
does treat as valid (and visually blank) special-value text.
"""

from src.common.nullable_spinbox import NullableSpinBox


def test_empty_int_spinbox_does_not_show_raw_sentinel(qapp):
    widget = NullableSpinBox(
        min_val=-2_147_483_648, max_val=2_147_483_647, current_value=None
    )
    assert widget.value() is None
    assert "2147483648" not in widget._spin.text()


def test_empty_float_spinbox_does_not_show_raw_sentinel(qapp):
    widget = NullableSpinBox(min_val=0, max_val=9999, is_float=True, current_value=None)
    assert widget.value() is None
    assert widget._spin.text().strip() == ""


def test_set_value_then_clear_blanks_display(qapp):
    widget = NullableSpinBox(
        min_val=-2_147_483_648, max_val=2_147_483_647, current_value=67
    )
    assert widget.value() == 67
    assert widget._spin.text() == "67"

    widget.setValue(None)
    assert widget.value() is None
    assert "2147483648" not in widget._spin.text()
