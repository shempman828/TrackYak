"""Tests for AlbumUIComponents.create_editable_field's float branch.

Regression coverage: QDoubleValidator() with no explicit locale follows the
application's default locale, so under a comma-decimal locale (e.g. de_DE)
it would accept input like "1,5" -- but get_field_value's float(text) always
parses with "." as the decimal separator, so that input silently came back
as None. The validator is now pinned to QLocale.C so it only accepts "."
decimals, matching the parser.
"""

from PySide6.QtCore import QLocale
from PySide6.QtGui import QValidator
from PySide6.QtWidgets import QLineEdit

from src.album.album_components import AlbumUIComponents
from src.db.field_spec import FieldSpec


def test_float_field_validator_uses_c_locale_under_comma_decimal_default(qapp):
    previous_default = QLocale()
    QLocale.setDefault(QLocale("de_DE"))
    try:
        field_config = FieldSpec(type=float)
        widget = AlbumUIComponents.create_editable_field(field_config)

        validator = widget.validator()
        assert validator.locale().decimalPoint() == "."

        # Comma-decimal input is rejected by the validator...
        state, _, _ = validator.validate("1,5", 4)
        assert state != QValidator.Acceptable

        # ...while dot-decimal input round-trips through get_field_value.
        widget.setText("1.5")
        assert AlbumUIComponents.get_field_value(widget, float) == 1.5
    finally:
        QLocale.setDefault(previous_default)


def test_float_field_applies_min_max_bounds(qapp):
    field_config = FieldSpec(type=float, min=0.0, max=10.0)
    widget = AlbumUIComponents.create_editable_field(field_config)

    validator = widget.validator()
    assert validator.bottom() == 0.0
    assert validator.top() == 10.0


def test_float_field_widget_is_plain_lineedit_when_no_bounds_set(qapp):
    field_config = FieldSpec(type=float)
    widget = AlbumUIComponents.create_editable_field(field_config)

    assert isinstance(widget, QLineEdit)
    assert widget.validator() is not None
