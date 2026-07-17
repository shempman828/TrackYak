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
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QSpinBox,
    QTextEdit,
    QWidget,
)

from src.core.logger_config import logger
from src.db.db_mapping_tracks import TRACK_FIELDS
from src.track.track_edit_basetab import _BaseTab

# ---------------------------------------------------------------------------
# Helpers shared by all tabs
# ---------------------------------------------------------------------------


class _NoScrollSpinBox(QSpinBox):
    """QSpinBox that ignores wheel events unless it already has focus.

    Qt delivers wheel events to whatever widget is under the cursor,
    regardless of focus, so merely scrolling the form past a spin box
    silently bumps its value and fires valueChanged.
    """

    def wheelEvent(self, event):
        if not self.hasFocus():
            event.ignore()
        else:
            super().wheelEvent(event)


class _NoScrollDoubleSpinBox(QDoubleSpinBox):
    """QDoubleSpinBox variant of _NoScrollSpinBox — see its docstring."""

    def wheelEvent(self, event):
        if not self.hasFocus():
            event.ignore()
        else:
            super().wheelEvent(event)


def _make_widget_for_field(field_name: str, field_config, on_change_cb):
    """
    Create and return the right editable widget for a FieldSpec.
    Connects the widget's change signal to on_change_cb(field_name).
    """
    if field_config.type == bool:  # noqa: E721
        w = QCheckBox()
        w.toggled.connect(lambda _checked, fn=field_name: on_change_cb(fn))
    elif field_config.type == int:  # noqa: E721
        w = _NoScrollSpinBox()
        w.setRange(
            int(field_config.min) if field_config.min is not None else -2_147_483_648,
            int(field_config.max) if field_config.max is not None else 2_147_483_647,
        )
        w.valueChanged.connect(lambda _v, fn=field_name: on_change_cb(fn))
    elif field_config.type == float:  # noqa: E721
        w = _NoScrollDoubleSpinBox()
        w.setDecimals(
            field_config.decimals if field_config.decimals is not None else 4
        )
        w.setRange(
            field_config.min if field_config.min is not None else -1e9,
            field_config.max if field_config.max is not None else 1e9,
        )
        if field_config.step is not None:
            w.setSingleStep(field_config.step)
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
            if field_config.length <= 4:
                w.setMaximumWidth(
                    w.fontMetrics().horizontalAdvance("W" * field_config.length) + 24
                )
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
    except (ValueError, TypeError) as e:
        logger.warning(
            f"Failed to coerce value {value!r} for field '{field_config.friendly}': {e}"
        )
        return None
    return value


def _format_readonly(value, field_config, field_name: str = "") -> str:
    """Format a value for display in a readonly QLabel."""
    if value is None or value == "":
        return "—"
    if field_config and field_config.type == bool:  # noqa: E721
        return "Yes" if value else "No"
    if field_name == "duration" and isinstance(value, (int, float)):
        total_s = int(value)
        m, s = divmod(total_s, 60)
        return f"{m}:{s:02d}"
    if field_name == "file_size" and isinstance(value, (int, float)):
        return f"{value / (1024 * 1024):.1f} MB"
    if field_config and field_config.type == float and isinstance(value, (int, float)):  # noqa: E721
        # 0–1 typed features (danceability, energy, etc.) read better as percentages
        if field_config.min == 0.0 and field_config.max == 1.0:
            return f"{value * 100:.1f}%"
        return f"{value:,.2f}"
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

    # Fields sharing one form row instead of getting their own — keeps
    # short, closely-related fields (e.g. a Year/Month/Day trio) from
    # wasting vertical space. Keyed by the first field in the group.
    _ROW_GROUPS = {
        "track_number": ["absolute_track_number"],
        "recorded_year": ["recorded_month", "recorded_day"],
        "release_year": ["release_month", "release_day"],
        "composed_year": ["composed_month", "composed_day"],
        "file_extension": ["file_size", "duration"],
        "bit_rate": ["sample_rate", "channels", "bit_depth"],
        "bpm": ["tempo_confidence"],
        "key": ["mode", "key_confidence"],
        "track_gain": ["track_peak"],
        "date_added": ["last_listened_date", "play_count"],
    }

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
            note.setProperty("textRole", "note")
            layout.addRow(note)

        skip = set()
        current_section = None
        for field_name, cfg in fields.items():
            if field_name in skip:
                continue

            if cfg.section and cfg.section != current_section:
                current_section = cfg.section
                layout.addRow(self._make_section_header(current_section))

            partner_names = [
                n for n in self._ROW_GROUPS.get(field_name, []) if n in fields
            ]
            if partner_names:
                skip.update(partner_names)
                rows = [
                    row
                    for row in (
                        self._make_row_field(field_name, cfg),
                        *(
                            self._make_row_field(name, fields[name])
                            for name in partner_names
                        ),
                    )
                    if row is not None
                ]
                if not rows:
                    continue
                if len(rows) == 1:
                    layout.addRow(*rows[0])
                    continue
                first_label, first_widget = rows[0]
                container = QWidget()
                hbox = QHBoxLayout(container)
                hbox.setContentsMargins(0, 0, 0, 0)
                hbox.addWidget(first_widget)
                for lbl, w in rows[1:]:
                    hbox.addWidget(lbl)
                    hbox.addWidget(w)
                hbox.addStretch()
                layout.addRow(first_label, container)
                continue

            row = self._make_row_field(field_name, cfg)
            if row is not None:
                layout.addRow(*row)

    @staticmethod
    def _make_section_header(text: str) -> QLabel:
        """A bold sub-heading row that splits a tab's fields into groups
        (e.g. Properties' "File Info" vs "Musical Properties")."""
        header = QLabel(text)
        font = header.font()
        font.setBold(True)
        font.setPointSize(font.pointSize() + 1)
        header.setFont(font)
        header.setStyleSheet("margin-top: 10px;")
        return header

    def _make_row_field(self, field_name: str, cfg):
        """Build the (label, value_widget) pair for one field, or return
        None if the field should be omitted (e.g. non-multiple in multi-track
        edit mode)."""
        label_text = cfg.friendly or field_name
        lbl = QLabel(f"{label_text}:")
        if cfg.tooltip:
            lbl.setToolTip(cfg.tooltip)

        if not cfg.editable:
            # Read-only display label
            val_lbl = QLabel("—")
            val_lbl.setWordWrap(True)
            val_lbl.setProperty("textRole", "note")
            val_lbl.setFocusPolicy(Qt.NoFocus)
            val_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
            self._labels[field_name] = val_lbl
            return lbl, val_lbl

        # Skip fields marked multiple=False in multi-track mode
        if self.is_multi and not cfg.multiple:
            return None
        w = _make_widget_for_field(field_name, cfg, self._mark_dirty)
        self._widgets[field_name] = w
        return lbl, w

    # ── _BaseTab interface ───────────────────────────────────────────────

    def load(self, tracks: list) -> None:
        self.tracks = tracks
        self._dirty.clear()
        self._populate(tracks, skip_dirty=False)

    def refresh_values(self, tracks: list) -> None:
        """Like load(), but leaves any field the user has already started
        editing untouched — safe to call mid-session (e.g. after a
        background audio analysis run updates the underlying track(s))."""
        self.tracks = tracks
        self._populate(tracks, skip_dirty=True)

    def _populate(self, tracks: list, skip_dirty: bool) -> None:
        if self.is_multi:
            # Show value only when all tracks agree; blank otherwise
            for field_name, w in self._widgets.items():
                if skip_dirty and field_name in self._dirty:
                    continue
                values = [getattr(t, field_name, None) for t in tracks]
                unique = set(str(v) for v in values)
                _write_widget(w, values[0] if len(unique) == 1 else None)
        else:
            for field_name, w in self._widgets.items():
                if skip_dirty and field_name in self._dirty:
                    continue
                _write_widget(w, getattr(self.track, field_name, None))
            for field_name, lbl in self._labels.items():
                cfg = TRACK_FIELDS.get(field_name)
                lbl.setText(
                    _format_readonly(
                        getattr(self.track, field_name, None), cfg, field_name
                    )
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
        if changes:
            logger.debug(
                f"Collected {len(changes)} field change(s) in '{self.category}' tab: "
                f"{list(changes.keys())}"
            )
        return changes
