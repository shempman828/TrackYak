"""FieldFormTab: titled field cards built from TRACK_FIELDS for one category."""

from __future__ import annotations

from typing import Any, ClassVar

from PySide6.QtCore import Qt
from PySide6.QtGui import QDoubleValidator, QIntValidator
from PySide6.QtWidgets import QCheckBox, QDoubleSpinBox, QFormLayout, QFrame, QHBoxLayout, QLabel, QLineEdit, QScrollArea, QSizePolicy, QSpinBox, QTextEdit, QVBoxLayout, QWidget

from src.common.widgets.detail_card import DetailCard
from src.common.widgets.nullable_numeric_field import create_nullable_float_field, create_nullable_int_field, nullable_field_value, set_nullable_field_value
from src.db.db_mapping_tracks import TRACK_FIELDS
from src.foundation.logger_config import logger
from src.track.edit.track_edit_basetab import _BaseTab

# ---------------------------------------------------------------------------
# Helpers shared by all tabs
# ---------------------------------------------------------------------------


# Numbers are short; a full-width field for one would read as a text box.
_NUMBER_FIELD_WIDTH = 160


def _make_widget_for_field(field_name: str, field_config, on_change_cb):
    """Create the editable widget for a FieldSpec and connect it to on_change_cb(field_name)."""
    if field_config.type is bool:
        w = QCheckBox()
        w.toggled.connect(lambda _checked, fn=field_name: on_change_cb(fn))
        # A multi-track "Mixed values" box starts partially checked (see
        # _show_mixed); the first click resolves it to a plain two-state box.
        w.checkStateChanged.connect(lambda state, cb=w: cb.setTristate(False) if cb.isTristate() and state != Qt.PartiallyChecked else None)
    elif field_config.type is int:
        # Every int field on Track is nullable in the DB, so we always give
        # the user a way to clear it back to NULL rather than being stuck
        # with whatever number is left in a plain QSpinBox.
        w = create_nullable_int_field(
            min_val=(int(field_config.min) if field_config.min is not None else -2_147_483_648), max_val=(int(field_config.max) if field_config.max is not None else 2_147_483_647)
        )
        w.setMaximumWidth(_NUMBER_FIELD_WIDTH)
        w.textChanged.connect(lambda _t, fn=field_name: on_change_cb(fn))
    elif field_config.type is float:
        w = create_nullable_float_field(
            min_val=field_config.min if field_config.min is not None else -1e9,
            max_val=field_config.max if field_config.max is not None else 1e9,
            decimals=field_config.decimals if field_config.decimals is not None else 4,
        )
        w.setMaximumWidth(_NUMBER_FIELD_WIDTH)
        w.textChanged.connect(lambda _t, fn=field_name: on_change_cb(fn))
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
                w.setMaximumWidth(w.fontMetrics().horizontalAdvance("W" * field_config.length) + 24)
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
        validator = widget.validator()
        if isinstance(validator, QDoubleValidator):
            return nullable_field_value(widget, is_float=True)
        if isinstance(validator, QIntValidator):
            return nullable_field_value(widget)
        return widget.text()
    return None


def _write_widget(widget, value) -> None:
    """Write a value into any supported widget type without triggering signals."""
    widget.blockSignals(True)
    try:
        if isinstance(widget, QCheckBox):
            widget.setChecked(bool(value) if value is not None else False)
        elif isinstance(widget, (QSpinBox, QDoubleSpinBox)):
            widget.setValue(value if value is not None else 0)
        elif isinstance(widget, QTextEdit):
            widget.setPlainText(str(value) if value is not None else "")
        elif isinstance(widget, QLineEdit):
            validator = widget.validator()
            if isinstance(validator, QDoubleValidator):
                set_nullable_field_value(widget, value, is_float=True)
            elif isinstance(validator, QIntValidator):
                set_nullable_field_value(widget, value)
            else:
                widget.setText(str(value) if value is not None else "")
    finally:
        widget.blockSignals(False)


def _coerce(value, field_config) -> Any:
    """Convert a raw widget value to the correct Python type."""
    if value in (None, ""):
        return None
    try:
        if field_config.type is int:
            return int(value)
        if field_config.type is float:
            return float(value)
        if field_config.type is bool:
            return bool(value)
    except (ValueError, TypeError) as e:
        logger.warning(f"Failed to coerce value {value!r} for field '{field_config.friendly}': {e}")
        return None
    return value


def _format_readonly(value, field_config, field_name: str = "") -> str:
    """Format a value for display in a readonly QLabel."""
    if value is None or value == "":
        return "—"
    if field_config and field_config.type is bool:
        return "Yes" if value else "No"
    if field_name == "duration" and isinstance(value, (int, float)):
        total_s = int(value)
        m, s = divmod(total_s, 60)
        return f"{m}:{s:02d}"
    if field_name == "file_size" and isinstance(value, (int, float)):
        return f"{value / (1024 * 1024):.1f} MB"
    if field_config and field_config.type is float and isinstance(value, (int, float)):
        # 0-1 typed features (danceability, energy, etc.) read better as percentages
        if field_config.min == 0.0 and field_config.max == 1.0:
            return f"{value * 100:.1f}%"
        return f"{value:,.2f}"
    text = str(value)
    # File paths carry their identifying part (artist/album/title) at the end,
    # so front-truncating them leaves every track showing the same useless
    # common prefix. The display label word-wraps, so show the whole path.
    if field_name == "track_file_path":
        return text
    if len(text) > 80:
        return text[:77] + "..."
    return text


_MIXED_PLACEHOLDER = "Mixed values"


def _show_mixed(widget, mixed: bool) -> None:
    """Mark a multi-track field whose tracks have different values."""
    # Line and text edits show a placeholder; checkboxes show the partial state.
    if isinstance(widget, (QLineEdit, QTextEdit)):
        base = widget.property("basePlaceholder")
        if base is None:
            base = widget.placeholderText()
            widget.setProperty("basePlaceholder", base)
        widget.setPlaceholderText(_MIXED_PLACEHOLDER if mixed else base)
    elif isinstance(widget, QCheckBox) and mixed:
        widget.blockSignals(True)
        widget.setTristate(True)
        widget.setCheckState(Qt.PartiallyChecked)
        widget.blockSignals(False)


class FieldFormTab(_BaseTab):
    """Tab that shows all TRACK_FIELDS of one category in titled DetailCards."""

    # One card per FieldSpec.section, or one editable and one read-only card.

    # Fields sharing one form row instead of getting their own — keeps
    # short, closely-related fields from wasting vertical space. Keyed by
    # the first field in the group.
    _ROW_GROUPS: ClassVar[dict[str, list[str]]] = {
        "track_number": ["absolute_track_number"],
        "recorded_year": ["recorded_month", "recorded_day"],
        "release_year": ["release_month", "release_day"],
        "composed_year": ["composed_month", "composed_day"],
        "file_extension": ["file_size", "duration"],
        "bit_rate": ["sample_rate", "channels", "bit_depth"],
        "bpm": ["tempo_confidence"],
        "key": ["mode", "key_confidence"],
        "primary_time_signature": ["time_signature_confidence"],
        "track_gain": ["track_peak"],
        "date_added": ["last_listened_date", "play_count"],
    }

    # Year/Month/Day groups, drawn as one framed date chip (the same
    # DateChipGroup control the album editor uses) instead of a plain row.
    _DATE_GROUPS: ClassVar[frozenset[str]] = frozenset({"recorded_year", "release_year", "composed_year"})

    # Card titles for categories whose fields carry no FieldSpec.section.
    _EDITABLE_CARD_TITLES: ClassVar[dict[str, str]] = {
        "Basic": "Title & Numbering",
        "Date": "Dates",
        "Alias": "Alternate Titles",
        "Classical": "Work & Movement",
        "Identification": "Identifiers",
        "Lyrics": "Lyrics",
    }
    _READONLY_CARD_TITLES: ClassVar[dict[str, str]] = {"Basic": "Album & Artist", "Date": "Release (from album)", "Identification": "AcoustID", "Advanced": "Audio Analysis"}

    def __init__(self, category: str, tracks: list, controller, parent=None):
        super().__init__(tracks, controller, parent)
        self.category = category
        self._widgets: dict[str, QWidget] = {}  # editable widgets
        self._labels: dict[str, QLabel] = {}  # readonly labels
        self._build_ui()

    def _build_ui(self):
        """Build the scroll area and one DetailCard per field group."""
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setObjectName("FieldFormScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        body = QWidget()
        body.setProperty("bgTransparent", True)
        self._body_layout = QVBoxLayout(body)
        self._body_layout.setContentsMargins(0, 0, 6, 0)
        self._body_layout.setSpacing(12)
        scroll.setWidget(body)
        outer.addWidget(scroll)

        fields = {name: cfg for name, cfg in TRACK_FIELDS.items() if cfg.category == self.category}

        # Card key -> (title, form). Cards appear in first-field order.
        cards: dict[str, tuple[DetailCard, QFormLayout]] = {}

        def form_for(cfg) -> QFormLayout:
            if cfg.section:
                key, title = cfg.section, cfg.section
            elif cfg.editable:
                key, title = "__editable__", self._EDITABLE_CARD_TITLES.get(self.category, self.category)
            else:
                key, title = "__readonly__", self._READONLY_CARD_TITLES.get(self.category, "Details")
            if key not in cards:
                card = DetailCard(title)
                form = QFormLayout()
                form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
                form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
                form.setHorizontalSpacing(14)
                form.setVerticalSpacing(8)
                card.body.addLayout(form)
                cards[key] = (card, form)
            return cards[key][1]

        skip = set()
        for field_name, cfg in fields.items():
            if field_name in skip:
                continue

            partner_names = [n for n in self._ROW_GROUPS.get(field_name, []) if n in fields]
            if partner_names:
                skip.update(partner_names)
                rows = [row for row in (self._make_row_field(field_name, cfg), *(self._make_row_field(name, fields[name]) for name in partner_names)) if row is not None]
                if not rows:
                    continue
                form = form_for(cfg)
                if field_name in self._DATE_GROUPS:
                    form.addRow(self._make_field_label(cfg.friendly.removesuffix(" Year") or field_name, cfg.tooltip), self._make_date_chip(rows))
                    continue
                if len(rows) == 1:
                    form.addRow(*rows[0])
                    continue
                first_label, first_widget = rows[0]
                container = QWidget()
                container.setProperty("bgTransparent", True)
                hbox = QHBoxLayout(container)
                hbox.setContentsMargins(0, 0, 0, 0)
                hbox.setSpacing(10)
                # Values sharing a row keep their natural width.
                for _lbl, w in rows:
                    if isinstance(w, QLabel):
                        w.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
                hbox.addWidget(first_widget)
                for lbl, w in rows[1:]:
                    hbox.addSpacing(6)
                    hbox.addWidget(lbl)
                    hbox.addWidget(w)
                hbox.addStretch()
                form.addRow(first_label, container)
                continue

            row = self._make_row_field(field_name, cfg)
            if row is not None:
                form_for(cfg).addRow(*row)

        # Editable cards first, read-only details after them.
        ordered = sorted(cards.items(), key=lambda kv: kv[0] == "__readonly__")
        for _key, (card, form) in ordered:
            if form.rowCount():
                self._body_layout.addWidget(card)
            else:
                card.deleteLater()

        # Tab-specific action buttons (see add_action_widget) sit under the cards.
        self._actions_row = QHBoxLayout()
        self._actions_row.setSpacing(8)
        self._actions_row.addStretch()
        self._body_layout.addLayout(self._actions_row)
        self._body_layout.addStretch()

    def add_action_widget(self, widget: QWidget) -> None:
        """Add a tab-specific action button to the row under the field cards."""
        self._actions_row.insertWidget(self._actions_row.count() - 1, widget)

    @staticmethod
    def _make_field_label(text: str, tooltip: str | None = None) -> QLabel:
        """Return a form label with an optional tooltip."""
        lbl = QLabel(text)
        lbl.setProperty("textRole", "fieldLabel")
        if tooltip:
            lbl.setToolTip(tooltip)
        return lbl

    @staticmethod
    def _make_date_chip(rows: list) -> QWidget:
        """Return the Year/Month/Day widgets as one framed date chip."""
        holder = QWidget()
        holder.setProperty("bgTransparent", True)
        holder_row = QHBoxLayout(holder)
        holder_row.setContentsMargins(0, 0, 0, 0)

        chip = QFrame()
        chip.setObjectName("DateChipGroup")
        chip_row = QHBoxLayout(chip)
        chip_row.setContentsMargins(10, 6, 10, 6)
        chip_row.setSpacing(12)
        for lbl, w in rows:
            col = QVBoxLayout()
            col.setSpacing(2)
            caption = QLabel(lbl.text().split()[-1] if lbl.text() else "")
            caption.setProperty("textRole", "muted")
            col.addWidget(caption)
            if isinstance(w, QLineEdit):
                w.setFixedWidth(80)
            col.addWidget(w)
            chip_row.addLayout(col)
            lbl.deleteLater()
        holder_row.addWidget(chip)
        holder_row.addStretch()
        return holder

    def _make_row_field(self, field_name: str, cfg):
        """Return the (label, value_widget) pair for one field, or None to omit it."""
        lbl = self._make_field_label(cfg.friendly or field_name, cfg.tooltip)

        if not cfg.editable:
            # Read-only display label
            val_lbl = QLabel("—")
            val_lbl.setWordWrap(True)
            # Expanding, so ExpandingFieldsGrow gives long values (file
            # paths) the full row to wrap in instead of their narrow hint.
            val_lbl.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
            val_lbl.setProperty("textRole", "value")
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
        """Fill every field from `tracks` and clear the unsaved-edit set."""
        self.tracks = tracks
        self._dirty.clear()
        self._populate(tracks, skip_dirty=False)

    def refresh_values(self, tracks: list) -> None:
        """Like load(), but leave the fields the user already edited untouched."""
        self.tracks = tracks
        self._populate(tracks, skip_dirty=True)

    def _populate(self, tracks: list, skip_dirty: bool) -> None:
        """Write track values into the widgets and labels."""
        if self.is_multi:
            # Show the value only when all tracks agree; otherwise leave the
            # field blank and flag it "Mixed values".
            for field_name, w in self._widgets.items():
                if skip_dirty and field_name in self._dirty:
                    continue
                values = [getattr(t, field_name, None) for t in tracks]
                unique = {str(v) for v in values}
                _write_widget(w, values[0] if len(unique) == 1 else None)
                _show_mixed(w, len(unique) > 1)
            for field_name, lbl in self._labels.items():
                values = [getattr(t, field_name, None) for t in tracks]
                if len({str(v) for v in values}) == 1:
                    lbl.setText(_format_readonly(values[0], TRACK_FIELDS.get(field_name), field_name))
                else:
                    lbl.setText(_MIXED_PLACEHOLDER)
        else:
            for field_name, w in self._widgets.items():
                if skip_dirty and field_name in self._dirty:
                    continue
                _write_widget(w, getattr(self.track, field_name, None))
            for field_name, lbl in self._labels.items():
                cfg = TRACK_FIELDS.get(field_name)
                lbl.setText(_format_readonly(getattr(self.track, field_name, None), cfg, field_name))

    def set_if_empty(self, values: dict[str, Any]) -> None:
        """Fill only the blank fields from `values` and mark them unsaved (single track only)."""
        if self.is_multi:
            return
        for field_name, value in values.items():
            widget = self._widgets.get(field_name)
            if widget is None:
                continue
            current = _read_widget(widget)
            if current not in (None, "", 0, 0.0, False):
                continue
            _write_widget(widget, value)
            self._mark_dirty(field_name)

    def get_field_value(self, field_name: str) -> Any:
        """Return the currently typed value of one editable field, or None."""
        widget = self._widgets.get(field_name)
        return _read_widget(widget) if widget is not None else None

    def set_field_value(self, field_name: str, value: Any) -> bool:
        """Write `value` into one editable field and mark it unsaved; False if absent."""
        widget = self._widgets.get(field_name)
        if widget is None:
            return False
        _write_widget(widget, value)
        self._mark_dirty(field_name)
        return True

    def collect_all_values(self) -> dict[str, Any]:
        """Return every displayed field that has a value, editable or read-only."""
        values: dict[str, Any] = {}
        for field_name, w in self._widgets.items():
            cfg = TRACK_FIELDS.get(field_name)
            if cfg is None:
                continue
            val = _coerce(_read_widget(w), cfg)
            if val is not None:
                values[field_name] = val
        for field_name, lbl in self._labels.items():
            text = lbl.text()
            if text and text not in ("—", _MIXED_PLACEHOLDER):
                values[field_name] = text
        return values

    def pending_changes(self) -> set[str]:
        """Return the fields whose edited value differs from the saved value."""
        return set(self._gather_changes())

    def collect_changes(self) -> dict[str, Any]:
        """Return the changed field values to save."""
        changes = self._gather_changes()
        if changes:
            logger.debug(f"Collected {len(changes)} field change(s) in '{self.category}' tab: {list(changes.keys())}")
        return changes

    def _gather_changes(self) -> dict[str, Any]:
        """Return dirty fields whose value really differs (all dirty fields in multi mode)."""
        # No logging: pending_changes() calls this on every keystroke.
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
