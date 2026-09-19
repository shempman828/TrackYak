"""
playlist_smart_criteria_widget.py

Widget for a single smart playlist criteria row.
"""

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
    QMenu,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QStyle,
    QToolButton,
    QWidget,
)

from src.foundation.logger_config import logger
from src.playlist.playlist_smart_criteria_fields import CRITERIA_FIELDS, OPERATORS_BY_GROUP

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
        start_date = self.start_edit.date()
        end_date = self.end_edit.date()
        if start_date > end_date:
            # An inverted range would otherwise silently match nothing --
            # swap rather than leave the user to guess why.
            start_date, end_date = end_date, start_date
        start = start_date.toString("yyyy-MM-dd") + " 00:00:00"
        end = end_date.toString("yyyy-MM-dd") + " 23:59:59"
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
    """A single criteria row: field selector, operator selector, value input, delete button."""

    delete_requested = Signal(QWidget)

    def __init__(self, parent=None):
        super().__init__(parent)
        # Lookup: field_name → (op_group, display, tooltip, min, max)
        self._field_meta = {
            name: (grp, disp, tip, mn, mx) for name, grp, disp, tip, mn, mx, cat in CRITERIA_FIELDS
        }
        self.init_ui()

    def init_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 2)
        layout.setSpacing(5)

        # Field selector — a combo-like button whose popup menu groups fields
        # into a submenu per category, so picking a field is a two-click
        # "category -> field" drill-down instead of scrolling one long list.
        self.field_button = QToolButton()
        self.field_button.setPopupMode(QToolButton.InstantPopup)
        self.field_button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._field_name = None
        self.field_menu = QMenu(self.field_button)

        current_category = None
        submenu = None
        first_field_name = first_display = None
        for field_name, _op_group, display, tooltip, _mn, _mx, category in CRITERIA_FIELDS:
            if category != current_category:
                submenu = self.field_menu.addMenu(category)
                current_category = category
            action = submenu.addAction(display)
            if tooltip:
                action.setToolTip(tooltip)
            action.triggered.connect(
                lambda checked=False, fn=field_name, disp=display: self._on_field_selected(fn, disp)
            )
            if first_field_name is None:
                first_field_name, first_display = field_name, display

        self.field_button.setMenu(self.field_menu)

        # Operator selector
        self.operator_combo = QComboBox()
        self.operator_combo.currentIndexChanged.connect(self._on_operator_changed)

        # Value widget (replaced dynamically based on field type)
        self.value_widget = QLineEdit()
        self.value_widget.setPlaceholderText("Enter value...")

        # Delete button
        delete_btn = QPushButton()
        delete_btn.setIcon(QApplication.style().standardIcon(QStyle.SP_DialogCloseButton))
        delete_btn.setFixedSize(24, 24)
        delete_btn.setToolTip("Remove this criteria")
        delete_btn.clicked.connect(lambda: self.delete_requested.emit(self))

        layout.addWidget(self.field_button, 2)
        layout.addWidget(self.operator_combo, 2)
        layout.addWidget(self.value_widget, 3)
        layout.addWidget(delete_btn)

        # Populate initial state
        self._on_field_selected(first_field_name, first_display)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _current_field_name(self) -> str:
        return self._field_name or ""

    def _current_meta(self):
        """Return (op_group, display, tooltip, min, max) for selected field."""
        return self._field_meta.get(self._current_field_name(), ("String", "", "", None, None))

    def _rebuild_operator_combo(self):
        """Refill operators to only those valid for the current field's type."""
        previous_op = self.operator_combo.currentData()

        self.operator_combo.currentIndexChanged.disconnect(self._on_operator_changed)
        self.operator_combo.clear()

        op_group = self._current_meta()[0]
        for kwarg, description in OPERATORS_BY_GROUP.get(op_group, OPERATORS_BY_GROUP["String"]):
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
            # Finer steps for 0-1 range fields (audio analysis); coarser for ratings
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

    def _on_field_selected(self, field_name, display):
        # Rebuilding here keeps the operator list and value widget always
        # matched to the selected field's type, so an invalid combination
        # (e.g. a text operator against a date field) can never be picked.
        self._field_name = field_name
        self.field_button.setText(display)
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
        """Return this row as a field/comparison/value/type dict."""
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
                value = [v.strip() for v in text.split(",") if v.strip()] if text else []
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

        return {"field": field_name, "comparison": operator, "value": value, "type": op_group}

    def set_criteria(self, criteria_dict: dict):
        """Pre-fill this row from a saved criteria dict (used when editing a playlist)."""
        # Set field first — this triggers operator + value widget rebuild
        field = criteria_dict.get("field")
        if field and field in self._field_meta:
            display = self._field_meta[field][1]
            self._on_field_selected(field, display)

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
            except (ValueError, TypeError) as e:
                logger.warning(f"Could not restore numeric criteria value {value!r}: {e}")
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
