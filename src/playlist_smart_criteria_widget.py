"""
playlist_smart_criteria_widget.py

Widget for a single smart playlist criteria row.

Key improvement: instead of maintaining a separate TRACK_MAPPINGS list
that duplicated (and often disagreed with) TRACK_FIELDS in db_mapping_tracks.py,
we now import TRACK_FIELDS directly and derive everything from it — display names,
tooltips, types, min/max ranges. Any future changes to field definitions in
db_mapping_tracks.py automatically flow through to the smart playlist UI for free.
"""

from datetime import datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDateTimeEdit,
    QDoubleSpinBox,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QStyle,
    QWidget,
)

from src.db_mapping_tracks import TRACK_FIELDS, TrackField

# ---------------------------------------------------------------------------
# Fields to exclude from smart playlist filtering (internal / not filterable)
# ---------------------------------------------------------------------------
_EXCLUDED_FIELDS = {
    "track_id",
    "track_file_path",
    "MBID",
    "track_barcode",
    "track_wikipedia_link",
    "lyrics",  # too long to filter meaningfully
    "track_gain",  # internal audio normalization value
    "track_peak",  # internal audio normalization value
}

# ---------------------------------------------------------------------------
# Extra "List" fields that are association proxies on the Track model.
# These aren't in TRACK_FIELDS (they're relationships, not scalar columns)
# but are very useful for smart playlist filtering.
# ---------------------------------------------------------------------------
_LIST_FIELDS = [
    ("genre_names", "Genre Names", "Filter by genre, e.g.: Rock, Jazz"),
    ("artist_names", "Artist Names", "Filter by any associated artist name"),
    ("primary_artist_names", "Primary Artist", "Filter by primary credited artist"),
    ("place_names", "Place Names", "Filter by associated place"),
    ("mood_name", "Mood", "Filter by mood"),
]


# ---------------------------------------------------------------------------
# Map a TrackField's Python type → our operator-group key
# ---------------------------------------------------------------------------
def _field_to_group(field: TrackField) -> str:
    t = field.type
    if t is int:
        return "Integer"
    if t is float:
        return "Float"
    if t is bool:
        return "Bool"
    if t is datetime:
        return "Datetime"
    return "String"


# ---------------------------------------------------------------------------
# Build the ordered field list from TRACK_FIELDS + _LIST_FIELDS.
# Each entry: (field_name, op_group, display_name, tooltip, min, max)
# ---------------------------------------------------------------------------
def _build_criteria_fields():
    entries = []

    category_order = [
        "Basic",
        "Properties",
        "Date",
        "User",
        "Advanced",
        "Classical",
        "Identification",
    ]
    buckets = {cat: [] for cat in category_order}
    buckets["Other"] = []

    for field_name, field in TRACK_FIELDS.items():
        if field_name in _EXCLUDED_FIELDS:
            continue
        cat = field.category or "Other"
        buckets.setdefault(cat, []).append((field_name, field))

    for cat in category_order + ["Other"]:
        for field_name, field in buckets.get(cat, []):
            entries.append(
                (
                    field_name,
                    _field_to_group(field),
                    field.friendly or field_name,
                    field.tooltip or "",
                    field.min,
                    field.max,
                )
            )

    # Append relationship / list fields
    for field_name, display, tooltip in _LIST_FIELDS:
        entries.append((field_name, "List", display, tooltip, None, None))

    return entries


CRITERIA_FIELDS = _build_criteria_fields()

# ---------------------------------------------------------------------------
# Operators available per group — only logically valid operators are shown
# ---------------------------------------------------------------------------
OPERATORS_BY_GROUP = {
    "String": [
        ("eq", "equals"),
        ("not", "does not equal"),
        ("contains", "contains"),
        ("startswith", "starts with"),
        ("endswith", "ends with"),
        ("isnull", "is empty"),
        ("notnull", "has a value"),
    ],
    "Integer": [
        ("eq", "equals"),
        ("not", "does not equal"),
        ("gt", "greater than"),
        ("lt", "less than"),
        ("gte", "greater than or equal"),
        ("lte", "less than or equal"),
        ("range", "between (inclusive)"),
        ("isnull", "is empty"),
        ("notnull", "has a value"),
    ],
    "Float": [
        ("eq", "equals"),
        ("not", "does not equal"),
        ("gt", "greater than"),
        ("lt", "less than"),
        ("gte", "greater than or equal"),
        ("lte", "less than or equal"),
        ("range", "between (inclusive)"),
        ("isnull", "is empty"),
        ("notnull", "has a value"),
    ],
    "Bool": [
        ("eq", "is"),
        ("not", "is not"),
        ("isnull", "is empty"),
        ("notnull", "has a value"),
    ],
    "Datetime": [
        ("gt", "after"),
        ("lt", "before"),
        ("gte", "on or after"),
        ("lte", "on or before"),
        ("eq", "exactly"),
        ("range", "between (inclusive)"),
        ("isnull", "is empty"),
        ("notnull", "has a value"),
    ],
    "List": [
        ("in", "is one of"),
        ("not_in", "is not one of"),
        ("contains", "contains"),
        ("isnull", "is empty"),
        ("notnull", "has a value"),
    ],
}

# Operators that require no value input from the user
NO_VALUE_OPERATORS = {"isnull", "notnull"}


# ---------------------------------------------------------------------------
# CriteriaWidget
# ---------------------------------------------------------------------------


class CriteriaWidget(QWidget):
    """
    A single criteria row: [Field ▾] [Operator ▾] [Value input] [✕]

    The operator list and value widget update automatically when the field
    changes, ensuring only valid combinations are ever possible.
    """

    delete_requested = Signal(QWidget)

    def __init__(self, parent=None):
        super().__init__(parent)
        # Lookup: field_name → (op_group, display, tooltip, min, max)
        self._field_meta = {
            name: (grp, disp, tip, mn, mx)
            for name, grp, disp, tip, mn, mx in CRITERIA_FIELDS
        }
        self.init_ui()

    def init_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 2)
        layout.setSpacing(5)

        # Field selector
        self.field_combo = QComboBox()
        for field_name, op_group, display, tooltip, mn, mx in CRITERIA_FIELDS:
            self.field_combo.addItem(display, field_name)
            if tooltip:
                idx = self.field_combo.count() - 1
                self.field_combo.setItemData(idx, tooltip, Qt.ToolTipRole)
        self.field_combo.currentIndexChanged.connect(self._on_field_changed)

        # Operator selector
        self.operator_combo = QComboBox()
        self.operator_combo.currentIndexChanged.connect(self._on_operator_changed)

        # Value widget (replaced dynamically based on field type)
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

        # Populate initial state
        self._on_field_changed(0)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _current_field_name(self) -> str:
        return self.field_combo.currentData() or ""

    def _current_meta(self):
        """Return (op_group, display, tooltip, min, max) for selected field."""
        return self._field_meta.get(
            self._current_field_name(), ("String", "", "", None, None)
        )

    def _rebuild_operator_combo(self):
        """Refill operators to only those valid for the current field's type."""
        previous_op = self.operator_combo.currentData()

        self.operator_combo.currentIndexChanged.disconnect(self._on_operator_changed)
        self.operator_combo.clear()

        op_group = self._current_meta()[0]
        for kwarg, description in OPERATORS_BY_GROUP.get(
            op_group, OPERATORS_BY_GROUP["String"]
        ):
            self.operator_combo.addItem(description, kwarg)

        # Restore previous operator if it still exists in the new list
        if previous_op:
            for i in range(self.operator_combo.count()):
                if self.operator_combo.itemData(i) == previous_op:
                    self.operator_combo.setCurrentIndex(i)
                    break

        self.operator_combo.currentIndexChanged.connect(self._on_operator_changed)

    def _rebuild_value_widget(self):
        """Replace value widget with one appropriate for the current field's type."""
        old = self.value_widget
        self.layout().removeWidget(old)
        old.setParent(None)
        old.deleteLater()

        op_group, _, tooltip, field_min, field_max = self._current_meta()

        if op_group == "Integer":
            widget = QSpinBox()
            lo = int(field_min) if field_min is not None else -999_999_999
            hi = int(field_max) if field_max is not None else 999_999_999
            widget.setRange(lo, hi)

        elif op_group == "Float":
            widget = QDoubleSpinBox()
            lo = float(field_min) if field_min is not None else -999_999.0
            hi = float(field_max) if field_max is not None else 999_999.0
            widget.setRange(lo, hi)
            # Finer steps for 0–1 range fields (audio analysis); coarser for ratings
            if hi <= 1.0:
                widget.setDecimals(4)
                widget.setSingleStep(0.01)
            else:
                widget.setDecimals(1)
                widget.setSingleStep(0.5)

        elif op_group == "Bool":
            widget = QComboBox()
            widget.addItem("Yes", True)
            widget.addItem("No", False)

        elif op_group == "Datetime":
            widget = QDateTimeEdit()
            widget.setCalendarPopup(True)
            widget.setDisplayFormat("yyyy-MM-dd HH:mm:ss")

        elif op_group == "List":
            widget = QLineEdit()
            widget.setPlaceholderText("Comma-separated values, e.g.: Rock, Pop")
            widget.setToolTip("Enter values separated by commas")

        else:  # String / fallback
            widget = QLineEdit()
            widget.setPlaceholderText("Enter text...")
            if tooltip:
                widget.setToolTip(tooltip)

        self.value_widget = widget
        # Position 2 = after field combo and operator combo, before delete btn
        self.layout().insertWidget(2, widget, 3)
        self._on_operator_changed()

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    def _on_field_changed(self, index):
        if index < 0:
            return
        self._rebuild_operator_combo()
        self._rebuild_value_widget()

    def _on_operator_changed(self, index=None):
        """Hide value input for operators that don't need a value."""
        op = self.operator_combo.currentData()
        self.value_widget.setVisible(op not in NO_VALUE_OPERATORS)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_criteria(self) -> dict:
        """
        Return this row as a dict, e.g.:
            {"field": "user_rating", "comparison": "gt", "value": 5.5, "type": "Float"}
        """
        field_name = self._current_field_name()
        op_group = self._current_meta()[0]
        operator = self.operator_combo.currentData()

        if operator in NO_VALUE_OPERATORS:
            value = None
        elif isinstance(self.value_widget, QComboBox):
            value = self.value_widget.currentData()  # Bool: True/False
        elif isinstance(self.value_widget, QLineEdit):
            text = self.value_widget.text().strip()
            if op_group == "List":
                value = (
                    [v.strip() for v in text.split(",") if v.strip()] if text else []
                )
            else:
                value = text if text else None
        elif isinstance(self.value_widget, (QSpinBox, QDoubleSpinBox)):
            value = self.value_widget.value()
        elif isinstance(self.value_widget, QDateTimeEdit):
            value = self.value_widget.dateTime().toString(Qt.ISODate)
        else:
            value = None

        return {
            "field": field_name,
            "comparison": operator,
            "value": value,
            "type": op_group,
        }

    def set_criteria(self, criteria_dict: dict):
        """
        Pre-fill this row from a saved criteria dict (used when editing a playlist).
        """
        # Set field first — this triggers operator + value widget rebuild
        field = criteria_dict.get("field")
        if field:
            for i in range(self.field_combo.count()):
                if self.field_combo.itemData(i) == field:
                    self.field_combo.setCurrentIndex(i)
                    break

        # Set operator (key is "comparison" in our saved format)
        operator = criteria_dict.get("comparison") or criteria_dict.get("operator")
        if operator:
            for i in range(self.operator_combo.count()):
                if self.operator_combo.itemData(i) == operator:
                    self.operator_combo.setCurrentIndex(i)
                    break

        # Set value
        value = criteria_dict.get("value")
        if value is None:
            return

        op_group = self._current_meta()[0]

        if isinstance(self.value_widget, QComboBox):
            for i in range(self.value_widget.count()):
                if str(self.value_widget.itemData(i)) == str(value):
                    self.value_widget.setCurrentIndex(i)
                    break
        elif isinstance(self.value_widget, QLineEdit):
            if op_group == "List" and isinstance(value, list):
                self.value_widget.setText(", ".join(str(v) for v in value))
            else:
                self.value_widget.setText(str(value))
        elif isinstance(self.value_widget, (QSpinBox, QDoubleSpinBox)):
            try:
                self.value_widget.setValue(float(value))
            except (ValueError, TypeError):
                pass
        elif isinstance(self.value_widget, QDateTimeEdit):
            from PySide6.QtCore import QDateTime

            dt = QDateTime.fromString(str(value), Qt.ISODate)
            if dt.isValid():
                self.value_widget.setDateTime(dt)
