"""Widget factory and value reader for the album editor's ALBUM_FIELDS inputs."""

from PySide6.QtCore import QLocale
from PySide6.QtGui import QDoubleValidator
from PySide6.QtWidgets import QCheckBox, QComboBox, QLineEdit, QSpinBox, QTextEdit

from src.foundation.logger_config import logger


class AlbumUIComponents:
    """Create field widgets from a FieldSpec and read typed values back out of them."""

    @staticmethod
    def create_editable_field(field_config, current_value=None):
        """Return an input widget suited to field_config.type, pre-filled with current_value."""
        if field_config.type is str:
            if field_config.longtext:
                widget = QTextEdit()
                if current_value is not None:
                    widget.setPlainText(str(current_value))
            else:
                widget = QLineEdit()
                if current_value is not None:
                    widget.setText(str(current_value))
            if field_config.placeholder:
                widget.setPlaceholderText(field_config.placeholder)

        elif field_config.type is int:
            widget = QSpinBox()
            min_val = field_config.min if field_config.min is not None else 0
            max_val = field_config.max if field_config.max is not None else 9999
            widget.setRange(int(min_val), int(max_val))
            widget.setValue(int(current_value) if current_value is not None else int(min_val))

        elif field_config.type is float:
            widget = QLineEdit()
            if current_value is not None:
                widget.setText(str(current_value))
            validator = QDoubleValidator()
            # Force the C locale so the validator accepts "." as the decimal
            # separator, matching get_field_value's float(text) parsing --
            # otherwise a comma-decimal system locale can accept input like
            # "1,5" that float() then silently rejects (returns None).
            validator.setLocale(QLocale(QLocale.C))
            if field_config.min is not None:
                validator.setBottom(field_config.min)
            if field_config.max is not None:
                validator.setTop(field_config.max)
            widget.setValidator(validator)

        elif field_config.type is bool:
            label = field_config.friendly or field_config.short or ""
            widget = QCheckBox(label)
            if current_value is not None:
                widget.setChecked(bool(current_value))

        else:
            # Gotcha: get_field_value cannot parse a list back, so an editable list field would corrupt.
            logger.debug(f"No dedicated widget for field type {field_config.type!r}; falling back to plain text input")
            widget = QLineEdit()
            if current_value is not None:
                widget.setText(str(current_value))

        return widget

    @staticmethod
    def get_field_value(widget, field_type):
        """Return the widget's current value as field_type, or None when blank or unparsable."""
        if isinstance(widget, QLineEdit):
            text = widget.text().strip()
            if not text:
                return None
            if field_type is int:
                try:
                    # Strip thousands separators (e.g. "1,234,567" on the
                    # estimated_sales field) before parsing.
                    return int(text.replace(",", ""))
                except ValueError:
                    return None
            if field_type is float:
                try:
                    return float(text)
                except ValueError:
                    return None
            return text

        if isinstance(widget, QTextEdit):
            text = widget.toPlainText().strip()
            return text if text else None

        if isinstance(widget, QSpinBox):
            value = widget.value()
            # Treat 0 as "not set" for optional int fields
            return value if value != 0 else None

        if isinstance(widget, QCheckBox):
            return int(widget.isChecked())

        if isinstance(widget, QComboBox):
            text = widget.currentText().strip()
            return text if text else None

        return None
