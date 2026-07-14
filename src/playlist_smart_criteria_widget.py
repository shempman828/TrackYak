"""
playlist_smart_criteria_widget.py

Widget for a single smart playlist criteria row.
"""

from datetime import datetime

from PySide6.QtCore import QDate, QDateTime, Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDateEdit,
    QDateTimeEdit,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QStyle,
    QWidget,
)

from src.db_mapping_tracks import TRACK_FIELDS, TrackField

# Storage/display format for exact-moment datetime values. Must use a space
# separator (not Qt.ISODate's "T") to match the "yyyy-MM-dd HH:MM:SS[.ffffff]"
# strings SQLAlchemy/SQLite actually store for DATETIME columns — comparing
# a "T"-separated string against those sorts incorrectly for same-day values.
DATETIME_DISPLAY_FORMAT = "yyyy-MM-dd HH:mm:ss"


def _parse_datetime(text: str) -> QDateTime:
    """Parse a stored datetime string, tolerating the legacy Qt.ISODate ('T') format."""
    dt = QDateTime.fromString(text, DATETIME_DISPLAY_FORMAT)
    if not dt.isValid():
        dt = QDateTime.fromString(text, Qt.ISODate)
    return dt

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
        ("eq", "on this day"),
        ("range", "between (inclusive)"),
        ("last_n_days", "in the last N days"),
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
# _DateRangeEdit — compound value widget for the Datetime "between" operator.
#
# Uses two date-only pickers (no time-of-day input) so users can just pick
# calendar days. The range is expanded internally to cover the full first
# day through the full last day (00:00:00 .. 23:59:59), inclusive.
# ---------------------------------------------------------------------------
class _DateRangeEdit(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self.start_edit = QDateEdit()
        self.start_edit.setCalendarPopup(True)
        self.start_edit.setDisplayFormat("yyyy-MM-dd")
        self.start_edit.setDate(QDate.currentDate().addDays(-7))

        self.end_edit = QDateEdit()
        self.end_edit.setCalendarPopup(True)
        self.end_edit.setDisplayFormat("yyyy-MM-dd")
        self.end_edit.setDate(QDate.currentDate())

        layout.addWidget(self.start_edit)
        layout.addWidget(QLabel("to"))
        layout.addWidget(self.end_edit)

    def get_value(self) -> str:
        """Return 'start|end' with the range widened to whole-day bounds."""
        start = self.start_edit.date().toString("yyyy-MM-dd") + " 00:00:00"
        end = self.end_edit.date().toString("yyyy-MM-dd") + " 23:59:59"
        return f"{start}|{end}"

    def set_value(self, value):
        if not value:
            return
        parts = str(value).split("|", 1)
        if len(parts) != 2:
            return
        start_day = parts[0].strip().split(" ")[0].split("T")[0]
        end_day = parts[1].strip().split(" ")[0].split("T")[0]
        start_date = QDate.fromString(start_day, "yyyy-MM-dd")
        end_date = QDate.fromString(end_day, "yyyy-MM-dd")
        if start_date.isValid():
            self.start_edit.setDate(start_date)
        if end_date.isValid():
            self.end_edit.setDate(end_date)


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
            operator = self.operator_combo.currentData()
            if operator == "range":
                widget = _DateRangeEdit()
            elif operator == "eq":
                # "On this day" — date only, matched as a whole-day range
                widget = QDateEdit()
                widget.setCalendarPopup(True)
                widget.setDisplayFormat("yyyy-MM-dd")
                widget.setDate(QDate.currentDate())
            elif operator == "last_n_days":
                widget = QSpinBox()
                widget.setRange(1, 3650)
                widget.setValue(7)
                widget.setSuffix(" day(s) ago")
            else:
                widget = QDateTimeEdit()
                widget.setCalendarPopup(True)
                widget.setDisplayFormat(DATETIME_DISPLAY_FORMAT)
                widget.setDateTime(QDateTime.currentDateTime())

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
        self._apply_value_widget_visibility()

    def _apply_value_widget_visibility(self):
        """Hide value input for operators that don't need a value."""
        op = self.operator_combo.currentData()
        self.value_widget.setVisible(op not in NO_VALUE_OPERATORS)

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    def _on_field_changed(self, index):
        if index < 0:
            return
        self._rebuild_operator_combo()
        self._rebuild_value_widget()

    def _on_operator_changed(self, index=None):
        # Datetime operators each need a differently-shaped value widget
        # (a moment picker, a date-only picker, two date pickers, or a
        # day-count spinner), so rebuild rather than just toggling visibility.
        if self._current_meta()[0] == "Datetime":
            self._rebuild_value_widget()
        else:
            self._apply_value_widget_visibility()

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
        elif isinstance(self.value_widget, _DateRangeEdit):
            value = self.value_widget.get_value()
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
        elif isinstance(self.value_widget, QDateEdit):
            # "On this day" — date only, no time component
            value = self.value_widget.date().toString("yyyy-MM-dd")
        elif isinstance(self.value_widget, QDateTimeEdit):
            value = self.value_widget.dateTime().toString(DATETIME_DISPLAY_FORMAT)
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

        if isinstance(self.value_widget, _DateRangeEdit):
            self.value_widget.set_value(value)
        elif isinstance(self.value_widget, QComboBox):
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
        elif isinstance(self.value_widget, QDateEdit):
            # "On this day" — date only, tolerate a full datetime string too
            day_text = str(value).strip().split(" ")[0].split("T")[0]
            d = QDate.fromString(day_text, "yyyy-MM-dd")
            if d.isValid():
                self.value_widget.setDate(d)
        elif isinstance(self.value_widget, QDateTimeEdit):
            dt = _parse_datetime(str(value))
            if dt.isValid():
                self.value_widget.setDateTime(dt)
