from PySide6.QtWidgets import (
    QWidget,
    QHBoxLayout,
    QComboBox,
    QLineEdit,
    QSpinBox,
    QDoubleSpinBox,
    QDateTimeEdit,
    QPushButton,
    QApplication,
    QStyle,
)
from PySide6.QtCore import Qt, Signal


# Mapping for track object fields [field_name, data_type, display_name]
TRACK_MAPPINGS = [
    # --- Core Identification ---
    ["track_name", "String", "Title"],
    ["comment", "String", "Comment"],
    ["lyrics", "String", "Lyrics"],
    ["track_quality", "String", "Quality"],
    ["is_fixed", "Integer", "Metadata Complete"],  # 0 or 1
    # --- File Information ---
    ["file_size", "Integer", "File Size (bytes)"],
    ["file_extension", "String", "File Extension"],
    ["bit_rate", "Integer", "Bit Rate (kbps)"],
    ["sample_rate", "Integer", "Sample Rate (Hz)"],
    ["bit_depth", "Integer", "Bit Depth"],
    ["channels", "Integer", "Channels"],
    ["duration", "Float", "Duration (seconds)"],
    # --- Date Information ---
    ["recorded_year", "Integer", "Recorded Year"],
    ["recorded_month", "Integer", "Recorded Month"],
    ["recorded_day", "Integer", "Recorded Day"],
    ["composed_year", "Integer", "Composed Year"],
    ["composed_month", "Integer", "Composed Month"],
    ["composed_day", "Integer", "Composed Day"],
    ["first_performed_year", "Integer", "First Performed Year"],
    ["date_added", "Datetime", "Date Added"],
    ["last_listened_date", "Datetime", "Last Played"],
    # --- Musical Attributes ---
    ["bpm", "Integer", "Beats Per Minute"],
    ["key", "String", "Musical Key"],
    ["mode", "String", "Musical Mode"],
    ["is_classical", "Integer", "Is Classical"],  # 0 or 1
    ["work_type", "String", "Classical Work Type"],
    ["is_explicit", "Integer", "Explicit"],  # 0 or 1
    ["is_instrumental", "Integer", "Instrumental"],  # 0 or 1
    # --- Acoustic Analysis (Spotify-style fields) ---
    ["danceability", "Float", "Danceability"],  # 0.0–1.0
    ["energy", "Float", "Energy"],  # 0.0–1.0
    ["acousticness", "Float", "Acousticness"],  # 0.0–1.0
    ["liveness", "Float", "Liveness"],  # 0.0–1.0
    ["valence", "Float", "Valence"],  # 0.0–1.0
    ["instrumentalness", "Float", "Instrumentalness"],  # 0.0–1.0
    ["tempo_confidence", "Float", "Tempo Confidence"],
    ["key_confidence", "Float", "Key Confidence"],
    # --- User Interaction ---
    ["user_rating", "Float", "User Rating (0–10)"],
    ["play_count", "Integer", "Play Count"],
    ["track_description", "String", "Description"],
    # --- Property Shortcuts ---
    ["album_name", "String", "Album Name"],
    ["genre_names", "List", "Genre Names"],
    ["artist_names", "List", "Artist Names"],
    ["mood_name", "String", "Mood Name"],
    ["place_name", "String", "Place Name"],
]

# operator mapping for comparison (kwarg shortcut, text description, symbolic description)
# controller pattern: self.controller.get.get_all_entities("Track", **kwargs)
OPERATOR_MAPPINGS = [
    {"kwarg": "eq", "description": "equals", "symbol": "="},
    {"kwarg": "not", "description": "does not equal / not in", "symbol": "!="},
    {"kwarg": "in", "description": "is one of", "symbol": "∈"},
    {"kwarg": "not_in", "description": "is not one of", "symbol": "∉"},
    {"kwarg": "contains", "description": "contains substring", "symbol": "∋"},
    {"kwarg": "startswith", "description": "starts with", "symbol": "^"},
    {"kwarg": "endswith", "description": "ends with", "symbol": "$"},
    {"kwarg": "gt", "description": "greater than", "symbol": ">"},
    {"kwarg": "lt", "description": "less than", "symbol": "<"},
    {"kwarg": "gte", "description": "greater than or equal", "symbol": "≥"},
    {"kwarg": "lte", "description": "less than or equal", "symbol": "≤"},
    {"kwarg": "range", "description": "between (inclusive)", "symbol": "[a,b]"},
    {"kwarg": "isnull", "description": "is empty (null)", "symbol": "IS NULL"},
    {
        "kwarg": "notnull",
        "description": "has a value (not null)",
        "symbol": "IS NOT NULL",
    },
]


class CriteriaWidget(QWidget):
    """Widget for editing a single criteria condition"""

    delete_requested = Signal(QWidget)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.field_mapping = {}
        self.operator_mapping = {}
        self.init_ui()

    def init_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 2)
        layout.setSpacing(5)

        # Field selector
        self.field_combo = QComboBox()
        for mapping in TRACK_MAPPINGS:
            field_name, data_type, display_name = mapping
            self.field_combo.addItem(display_name, field_name)
            self.field_mapping[field_name] = {
                "type": data_type,
                "display": display_name,
            }
        self.field_combo.currentIndexChanged.connect(self.on_field_changed)

        # Operator selector
        self.operator_combo = QComboBox()
        self.update_operator_list()

        # Value widget placeholder - will be created based on field type
        self.value_widget = QLineEdit()
        self.value_widget.setPlaceholderText("Enter value...")

        # Delete button
        delete_btn = QPushButton()
        delete_btn.setIcon(
            QApplication.style().standardIcon(QStyle.SP_DialogCloseButton)
        )
        delete_btn.setFixedSize(24, 24)
        delete_btn.setToolTip("Remove this criteria")
        delete_btn.clicked.connect(lambda: self.delete_requested.emit(self))

        layout.addWidget(self.field_combo, 2)
        layout.addWidget(self.operator_combo, 2)
        layout.addWidget(self.value_widget, 3)
        layout.addWidget(delete_btn)

        # Set initial value widget based on first field
        self.on_field_changed(0)

    def update_operator_list(self):
        """Update the operator list based on current field type."""
        current_op = (
            self.operator_combo.currentData()
            if self.operator_combo.count() > 0
            else None
        )

        self.operator_combo.clear()

        # Get current field type
        if self.field_combo.count() > 0:
            field_name = self.field_combo.currentData()
            field_info = self.field_mapping.get(field_name, {})
            data_type = field_info.get("type", "String")

            # Add appropriate operators based on data type
            for op in OPERATOR_MAPPINGS:
                op_type = op["kwarg"]

                # Skip inappropriate operators for certain types
                if data_type in ["String", "Text"]:
                    if op_type in ["gt", "lt", "gte", "lte", "range"]:
                        continue
                elif data_type in ["Integer", "Float", "Datetime"]:
                    if op_type in [
                        "contains",
                        "startswith",
                        "endswith",
                        "in",
                        "not_in",
                    ]:
                        continue

                # For List type, only show list operators
                elif data_type == "List":
                    if op_type not in ["in", "not_in", "contains", "isnull", "notnull"]:
                        continue

                self.operator_combo.addItem(f"{op['description']}", op_type)
                self.operator_mapping[op_type] = op

            # Restore previous operator if still available
            if current_op and current_op in self.operator_mapping:
                for i in range(self.operator_combo.count()):
                    if self.operator_combo.itemData(i) == current_op:
                        self.operator_combo.setCurrentIndex(i)
                        break

    def on_field_changed(self, index):
        """Update value widget based on selected field type."""
        if index < 0:
            return

        field_name = self.field_combo.currentData()
        field_info = self.field_mapping.get(field_name, {})
        data_type = field_info.get("type", "String")

        # Remove old widget
        old_widget = self.value_widget
        if old_widget:
            old_widget.setParent(None)
            old_widget.deleteLater()

        # Create appropriate widget based on data type
        if data_type in ["String", "Text"]:
            widget = QLineEdit()
            widget.setPlaceholderText("Enter text...")

        elif data_type == "Integer":
            widget = QSpinBox()
            widget.setRange(-999999, 999999)

            # Set sensible defaults for specific fields
            if field_name in ["user_rating"]:
                widget.setRange(0, 10)
            elif field_name in ["play_count", "file_size", "bit_rate", "sample_rate"]:
                widget.setMinimum(0)
            elif field_name in ["bit_depth", "channels"]:
                widget.setMinimum(1)
                widget.setMaximum(32)  # Reasonable max

        elif data_type == "Float":
            widget = QDoubleSpinBox()
            widget.setRange(-999999.0, 999999.0)
            widget.setDecimals(3)

            # Spotify-style fields (0-1 range)
            if field_name in [
                "danceability",
                "energy",
                "acousticness",
                "liveness",
                "valence",
                "instrumentalness",
                "tempo_confidence",
                "key_confidence",
            ]:
                widget.setRange(0.0, 1.0)
                widget.setDecimals(4)
                widget.setSingleStep(0.01)

        elif data_type == "Datetime":
            widget = QDateTimeEdit()
            widget.setCalendarPopup(True)
            widget.setDisplayFormat("yyyy-MM-dd HH:mm:ss")

        elif data_type == "List":
            widget = QLineEdit()
            widget.setPlaceholderText("Comma-separated values...")
            widget.setToolTip("Enter values separated by commas")

        else:
            # Default to QLineEdit for unknown types
            widget = QLineEdit()

        self.value_widget = widget

        # Add to layout
        layout = self.layout()
        layout.insertWidget(2, widget, 3)

        # Update operator list for new field type
        self.update_operator_list()

    def get_criteria(self):
        """Get the criteria as a dictionary."""
        field_name = self.field_combo.currentData()
        operator = self.operator_combo.currentData()
        field_info = self.field_mapping.get(field_name, {})
        data_type = field_info.get("type", "String")

        # Get value based on widget type
        value = None

        if isinstance(self.value_widget, QLineEdit):
            text = self.value_widget.text().strip()
            if data_type == "List":
                # Parse comma-separated list
                if text:
                    value = [v.strip() for v in text.split(",") if v.strip()]
                else:
                    value = []
            else:
                value = text if text else None

        elif isinstance(self.value_widget, QSpinBox):
            value = self.value_widget.value()

        elif isinstance(self.value_widget, QDoubleSpinBox):
            value = self.value_widget.value()

        elif isinstance(self.value_widget, QDateTimeEdit):
            value = self.value_widget.dateTime().toString(Qt.ISODate)

        # Handle special operators that don't need values
        if operator in ["isnull", "notnull"]:
            value = None

        return {
            "field": field_name,
            "comparison": operator,
            "value": value,
            "type": data_type,
        }

    def set_criteria(self, criteria_dict):
        """Set the widget values from a criteria dictionary."""
        # Set field
        field = criteria_dict.get("field")
        if field:
            for i in range(self.field_combo.count()):
                if self.field_combo.itemData(i) == field:
                    self.field_combo.setCurrentIndex(i)
                    break

        # Set operator
        operator = criteria_dict.get("operator")
        if operator:
            for i in range(self.operator_combo.count()):
                if self.operator_combo.itemData(i) == operator:
                    self.operator_combo.setCurrentIndex(i)
                    break

        # Set value
        value = criteria_dict.get("value")
        if value is not None:
            data_type = criteria_dict.get("type", "String")

            if isinstance(self.value_widget, QLineEdit):
                if data_type == "List" and isinstance(value, list):
                    self.value_widget.setText(", ".join(str(v) for v in value))
                else:
                    self.value_widget.setText(str(value))

            elif isinstance(self.value_widget, (QSpinBox, QDoubleSpinBox)):
                try:
                    self.value_widget.setValue(float(value))
                except (ValueError, TypeError):
                    pass

            elif isinstance(self.value_widget, QDateTimeEdit):
                # Would need date parsing here
                pass
