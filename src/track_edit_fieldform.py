# ---------------------------------------------------------------------------
# FieldFormTab — auto-builds a QFormLayout from TRACK_FIELDS for one category
# ---------------------------------------------------------------------------
from __future__ import annotations

from typing import Any, Dict

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QSpinBox,
    QTextEdit,
    QWidget,
)

from src.db_mapping_tracks import TRACK_FIELDS
from src.track_edit_basetab import _BaseTab

# ---------------------------------------------------------------------------
# Helpers shared by all tabs
# ---------------------------------------------------------------------------


def _make_widget_for_field(field_name: str, field_config, on_change_cb):
    """
    Create and return the right editable widget for a TrackField.
    Connects the widget's change signal to on_change_cb(field_name).
    """
    if field_config.type == bool:  # noqa: E721
        w = QCheckBox()
        w.toggled.connect(lambda _checked, fn=field_name: on_change_cb(fn))
    elif field_config.type == int:  # noqa: E721
        w = QSpinBox()
        w.setRange(
            int(field_config.min) if field_config.min is not None else -2_147_483_648,
            int(field_config.max) if field_config.max is not None else 2_147_483_647,
        )
        w.valueChanged.connect(lambda _v, fn=field_name: on_change_cb(fn))
    elif field_config.type == float:  # noqa: E721
        w = QDoubleSpinBox()
        w.setDecimals(4)
        w.setRange(
            field_config.min if field_config.min is not None else -1e9,
            field_config.max if field_config.max is not None else 1e9,
        )
        w.valueChanged.connect(lambda _v, fn=field_name: on_change_cb(fn))
    elif field_config.longtext:
        w = QTextEdit()
        w.textChanged.connect(lambda fn=field_name: on_change_cb(fn))
    else:
        w = QLineEdit()
        if field_config.placeholder:
            w.setPlaceholderText(field_config.placeholder)
        if field_config.length:
            w.setMaxLength(field_config.length)
        w.textChanged.connect(lambda _t, fn=field_name: on_change_cb(fn))
    return w


def _read_widget(widget) -> Any:
    """Return the current value from any supported widget type."""
    if isinstance(widget, QCheckBox):
        return widget.isChecked()
    if isinstance(widget, (QSpinBox, QDoubleSpinBox)):
        return widget.value()
    if isinstance(widget, QTextEdit):
        return widget.toPlainText()
    if isinstance(widget, QLineEdit):
        return widget.text()
    return None


def _write_widget(widget, value) -> None:
    """Write a value into any supported widget type without triggering signals."""
    if value is None:
        value_for_widget = None
    else:
        value_for_widget = value

    widget.blockSignals(True)
    try:
        if isinstance(widget, QCheckBox):
            widget.setChecked(
                bool(value_for_widget) if value_for_widget is not None else False
            )
        elif isinstance(widget, (QSpinBox, QDoubleSpinBox)):
            widget.setValue(value_for_widget if value_for_widget is not None else 0)
        elif isinstance(widget, QTextEdit):
            widget.setPlainText(
                str(value_for_widget) if value_for_widget is not None else ""
            )
        elif isinstance(widget, QLineEdit):
            widget.setText(
                str(value_for_widget) if value_for_widget is not None else ""
            )
    finally:
        widget.blockSignals(False)


def _coerce(value, field_config) -> Any:
    """Convert a raw widget value to the correct Python type."""
    if value in (None, ""):
        return None
    try:
        if field_config.type == int:  # noqa: E721
            return int(value)
        if field_config.type == float:  # noqa: E721
            return float(value)
        if field_config.type == bool:  # noqa: E721
            return bool(value)
    except (ValueError, TypeError):
        return None
    return value


def _format_readonly(value, field_config) -> str:
    """Format a value for display in a readonly QLabel."""
    if value is None or value == "":
        return "—"
    if field_config and field_config.type == bool:  # noqa: E721
        return "Yes" if value else "No"
    text = str(value)
    if len(text) > 80:
        return text[:77] + "..."
    return text


class FieldFormTab(_BaseTab):
    """
    Generic tab that renders all TRACK_FIELDS belonging to `category`.
    Editable fields → appropriate input widget.
    Read-only fields → styled QLabel.
    """

    def __init__(self, category: str, tracks: list, controller, parent=None):
        super().__init__(tracks, controller, parent)
        self.category = category
        self._widgets: Dict[str, QWidget] = {}  # editable widgets
        self._labels: Dict[str, QLabel] = {}  # readonly labels
        self._build_ui()

    def _build_ui(self):
        layout = QFormLayout(self)
        layout.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)

        fields = {
            name: cfg
            for name, cfg in TRACK_FIELDS.items()
            if cfg.category == self.category
        }

        if self.is_multi:
            note = QLabel("⚠  Changes will apply to all selected tracks.")
            note.setStyleSheet("color: #888; font-style: italic;")
            layout.addRow(note)

        for field_name, cfg in fields.items():
            # Build the label
            label_text = cfg.friendly or field_name
            lbl = QLabel(f"{label_text}:")
            if cfg.tooltip:
                lbl.setToolTip(cfg.tooltip)

            if not cfg.editable:
                # Read-only display label
                val_lbl = QLabel("—")
                val_lbl.setWordWrap(True)
                val_lbl.setStyleSheet("color: #666; font-style: italic;")
                val_lbl.setFocusPolicy(Qt.NoFocus)
                val_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
                self._labels[field_name] = val_lbl
                layout.addRow(lbl, val_lbl)
            else:
                # Skip fields marked multiple=False in multi-track mode
                if self.is_multi and not cfg.multiple:
                    continue
                w = _make_widget_for_field(field_name, cfg, self._mark_dirty)
                self._widgets[field_name] = w
                layout.addRow(lbl, w)

    # ── _BaseTab interface ───────────────────────────────────────────────

    def load(self, tracks: list) -> None:
        self.tracks = tracks
        self._dirty.clear()

        if self.is_multi:
            # Show value only when all tracks agree; blank otherwise
            for field_name, w in self._widgets.items():
                values = [getattr(t, field_name, None) for t in tracks]
                unique = set(str(v) for v in values)
                _write_widget(w, values[0] if len(unique) == 1 else None)
        else:
            for field_name, w in self._widgets.items():
                _write_widget(w, getattr(self.track, field_name, None))
            for field_name, lbl in self._labels.items():
                cfg = TRACK_FIELDS.get(field_name)
                lbl.setText(
                    _format_readonly(getattr(self.track, field_name, None), cfg)
                )

    def collect_changes(self) -> Dict[str, Any]:
        changes = {}
        for field_name in self._dirty:
            w = self._widgets.get(field_name)
            if w is None:
                continue
            cfg = TRACK_FIELDS.get(field_name)
            if cfg is None:
                continue
            raw = _read_widget(w)
            new_val = _coerce(raw, cfg)
            if self.is_multi or self._has_changed(field_name, new_val):
                changes[field_name] = new_val
        return changes
