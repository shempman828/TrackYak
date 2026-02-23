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
            else:
                widget = QLineEdit()
            if current_value is not None:
                widget.setText(str(current_value))
            if field_config.placeholder:
                widget.setPlaceholderText(field_config.placeholder)

        elif field_config.type == int:  # noqa: E721
            widget = QSpinBox()
            if field_config.min is not None:
                widget.setMinimum(field_config.min)
            if field_config.max is not None:
                widget.setMaximum(field_config.max)
            if current_value is not None:
                widget.setValue(int(current_value))

        elif field_config.type == float:  # noqa: E721
            widget = QLineEdit()
            if current_value is not None:
                widget.setText(str(current_value))
            widget.setValidator(QDoubleValidator())

        elif field_config.type == bool:  # noqa: E721
            label = field_config.friendly or field_config.short or "Checkbox"
            widget = QCheckBox(label)
            if current_value is not None:
                widget.setChecked(bool(current_value))

        else:
            widget = QLineEdit()
            if current_value is not None:
                widget.setText(str(current_value))

        if field_config.tooltip and hasattr(widget, "setToolTip"):
            widget.setToolTip(field_config.tooltip)

        return widget

    @staticmethod
    def get_field_value(widget, field_type):
        """Extract value from widget based on field type"""
        if isinstance(widget, (QLineEdit, QTextEdit)):
            if isinstance(widget, QTextEdit):
                value = widget.toPlainText().strip()
            else:
                value = widget.text().strip()
            return value or None

        elif isinstance(widget, QSpinBox):
            value = widget.value()
            return value if value != 0 else None

        elif isinstance(widget, QCheckBox):
            return int(widget.isChecked())

        return None
