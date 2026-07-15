from PySide6.QtGui import QDoubleValidator
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QSpinBox,
    QTextEdit,
    QWidget,
)


class AlbumUIComponents:
    """Helper class for creating reusable UI components"""

    @staticmethod
    def create_section_header(text):
        """Create a standardized section header"""
        label = QLabel(f"<h3>{text}</h3>")
        label.setContentsMargins(0, 10, 0, 5)
        return label

    @staticmethod
    def create_form_row(label_text, widget):
        """Create a standardized form row"""
        widget_row = QWidget()
        layout = QHBoxLayout(widget_row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(QLabel(label_text))
        layout.addWidget(widget, 1)
        return widget_row

    @staticmethod
    def create_editable_field(field_config, current_value=None):
        """Create appropriate widget for a field based on its configuration"""
        if field_config.type == str:  # noqa: E721
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

        elif field_config.type == int:  # noqa: E721
            widget = QSpinBox()
            # Set range — use 0 as minimum unless field_config specifies otherwise
            min_val = field_config.min if field_config.min is not None else 0
            max_val = field_config.max if field_config.max is not None else 9999
            widget.setRange(int(min_val), int(max_val))
            # BUG FIX: Only call setValue if we actually have a value.
            # Without this check, QSpinBox defaults to its minimum (often
            # showing 99 or some garbage value) when current_value is None.
            if current_value is not None:
                widget.setValue(int(current_value))
            else:
                widget.setValue(int(min_val))  # default to min, not QSpinBox internal

        elif field_config.type == float:  # noqa: E721
            widget = QLineEdit()
            if current_value is not None:
                widget.setText(str(current_value))
            widget.setValidator(QDoubleValidator())

        elif field_config.type == bool:  # noqa: E721
            label = field_config.friendly or field_config.short or ""
            widget = QCheckBox(label)
            if current_value is not None:
                widget.setChecked(bool(current_value))

        else:
            # Fallback: plain text input
            widget = QLineEdit()
            if current_value is not None:
                widget.setText(str(current_value))

        return widget

    @staticmethod
    def get_field_value(widget, field_type):
        """Extract the current value from a widget based on its expected type"""
        if isinstance(widget, QLineEdit):
            text = widget.text().strip()
            if not text:
                return None
            if field_type == int:
                try:
                    return int(text)
                except ValueError:
                    return None
            if field_type == float:
                try:
                    return float(text)
                except ValueError:
                    return None
            return text

        elif isinstance(widget, QTextEdit):
            text = widget.toPlainText().strip()
            return text if text else None

        elif isinstance(widget, QSpinBox):
            value = widget.value()
            # Treat 0 as "not set" for optional int fields
            return value if value != 0 else None

        elif isinstance(widget, QCheckBox):
            return int(widget.isChecked())

        return None
