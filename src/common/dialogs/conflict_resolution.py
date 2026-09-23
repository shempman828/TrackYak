"""Shared field-conflict detection and resolution UI for entity merges.

Used by both the manual one-pair merge dialog (`MergeDBDialog` in
base_merge_dialog.py) and the batch fuzzy-duplicate-scan merge flow
(`BaseFuzzyMatchDialog` in fuzzy_match_dialog.py), so a reconciled field
choice behaves identically wherever a merge happens.
"""

import html

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from src.common.widgets.qt_text import esc_amp as _esc_amp


def _is_skippable_field(attr, value):
    """Return True for fields that should never be shown as merge choices.

    Skipped categories:
    - Relationship fields: lists or ORM-mapped objects (not plain Python types)
    - Auto-generated IDs: any attribute ending in '_id'
    - Timestamps: any attribute ending in '_at' or named 'created_*' / 'updated_*'
    """
    if attr.endswith("_id"):
        return True

    if attr.endswith("_at") or attr.startswith("created_") or attr.startswith("updated_"):
        return True

    if isinstance(value, list):
        return True
    plain_types = (str, int, float, bool, type(None))
    return not isinstance(value, plain_types)


def _format_value_for_display(value):
    """Format a value for display in the conflict resolution UI."""
    if value is None:
        return "[Empty]"
    if value == "":
        return "[Blank]"
    return str(value)


def get_entity_conflicts(source_entity, target_entity, id_attr):
    """Detect differing scalar fields between two mapped ORM entities of the
    same class, excluding IDs, timestamps, and relationship/list fields.

    Returns {field_name: (source_value, target_value)}. Uses the mapper's
    column list rather than vars(entity): the instance __dict__ is emptied
    by SQLAlchemy whenever the shared session commits (expire_on_commit),
    which would otherwise report zero conflicts after the first merge in a
    batch.
    """
    conflicts = {}
    attr_names = type(source_entity).__mapper__.column_attrs.keys()
    for attr in attr_names:
        if attr.startswith("_") or attr in ("metadata", id_attr):
            continue

        s_val = getattr(source_entity, attr)
        t_val = getattr(target_entity, attr)

        if _is_skippable_field(attr, s_val) or _is_skippable_field(attr, t_val):
            continue

        if s_val != t_val:
            conflicts[attr] = (s_val, t_val)
    return conflicts


class _ConflictValueCell(QWidget):
    """One clickable value in the conflict-resolution grid.

    Behaves like a radio option shaped as a table cell: clicking it selects
    this side's value for the row and notifies the dialog to deselect the
    sibling cell in the same row.
    """

    def __init__(self, display_text, on_select, parent=None):
        super().__init__(parent)
        self.setProperty("mergeCell", True)
        self.setProperty("chosen", False)
        self.setCursor(Qt.PointingHandCursor)
        self._on_select = on_select

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(6)

        self._check = QLabel("✓")
        self._check.setProperty("mergeCellCheck", True)
        self._check.setFixedWidth(14)
        layout.addWidget(self._check, 0, Qt.AlignTop)

        value_label = QLabel(display_text)
        value_label.setWordWrap(True)
        layout.addWidget(value_label, 1)

        self._check.setVisible(False)

    def set_chosen(self, chosen):
        self._check.setVisible(chosen)
        self.setProperty("chosen", chosen)
        self.style().unpolish(self)
        self.style().polish(self)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._on_select()
        super().mousePressEvent(event)


class ConflictGridWidget(QWidget):
    """Field / source-entity / target-entity conflict grid for one pair.

    Renders one row per differing scalar field, each a click-to-choose pair
    of `_ConflictValueCell`s, and tracks which side the user picked.
    Defaults to the source side when it's non-blank, otherwise the target
    side -- matching the default `MergeDBDialog` has always used, kept
    as-is here for consistency between the manual and batch merge paths.
    """

    def __init__(self, source_entity, target_entity, id_attr, source_label, target_label, parent=None):
        super().__init__(parent)
        self.source_entity = source_entity
        self.target_entity = target_entity
        self.conflicts = get_entity_conflicts(source_entity, target_entity, id_attr)
        self._choices = {}
        self._cells = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        if not self.conflicts:
            layout.addWidget(QLabel("No conflicting fields."))
            return

        layout.addWidget(QLabel("Click a value to keep it for that field:"))
        layout.addLayout(self._build_grid(source_label, target_label))

    def _build_grid(self, source_label, target_label):
        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(8)
        grid.setColumnStretch(0, 0)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(2, 1)

        field_header = QLabel("Field")
        field_header.setProperty("mergeColHeader", True)
        grid.addWidget(field_header, 0, 0)

        source_header = QLabel(html.escape(str(source_label)))
        source_header.setProperty("mergeColHeader", True)
        grid.addWidget(source_header, 0, 1)

        target_header = QLabel(html.escape(str(target_label)))
        target_header.setProperty("mergeColHeader", True)
        grid.addWidget(target_header, 0, 2)

        divider = QFrame()
        divider.setFrameShape(QFrame.HLine)
        grid.addWidget(divider, 1, 0, 1, 3)

        row = 2
        for field, (s_val, t_val) in self.conflicts.items():
            field_label = QLabel(field)
            field_label.setProperty("mergeFieldLabel", True)
            field_label.setAlignment(Qt.AlignTop | Qt.AlignLeft)
            grid.addWidget(field_label, row, 0)

            s_display = html.escape(_format_value_for_display(s_val))
            t_display = html.escape(_format_value_for_display(t_val))

            s_cell = _ConflictValueCell(s_display, lambda f=field: self._choose(f, 0))
            t_cell = _ConflictValueCell(t_display, lambda f=field: self._choose(f, 1))
            grid.addWidget(s_cell, row, 1)
            grid.addWidget(t_cell, row, 2)
            self._cells[field] = (s_cell, t_cell)

            default_side = 1 if (s_val is None or s_val == "") else 0
            self._choices[field] = default_side
            (t_cell if default_side == 1 else s_cell).set_chosen(True)

            row += 1

        return grid

    def _choose(self, field, side):
        self._choices[field] = side
        s_cell, t_cell = self._cells[field]
        s_cell.set_chosen(side == 0)
        t_cell.set_chosen(side == 1)

    def has_conflicts(self):
        return bool(self.conflicts)

    def get_resolved_fields(self):
        """Return {field: value} for every conflicting field, using
        whichever side the user chose (or the default choice, if
        untouched)."""
        resolved = {}
        for field, side in self._choices.items():
            entity = self.source_entity if side == 0 else self.target_entity
            resolved[field] = getattr(entity, field)
        return resolved


class PairConflictDialog(QDialog):
    """One-pair conflict-resolution step shown mid-batch-merge, for a
    checked pair that has at least one differing scalar field.

    Distinct from `MergeDBDialog`'s conflict page, which is the terminal
    step of a one-pair search-driven merge; this is one step inside a
    larger batch, so it offers "skip this pair" and "skip the rest of the
    batch" in addition to applying the chosen values, and never runs the
    merge itself -- the caller collects `resolved_fields()` and dispatches
    the actual merge afterwards.
    """

    USE_SELECTED = "use_selected"
    SKIP_PAIR = "skip_pair"
    SKIP_ALL = "skip_all"
    CANCEL = "cancel"

    def __init__(self, source_entity, target_entity, id_attr, source_label, target_label, pair_index, pair_total, parent=None):
        super().__init__(parent)
        self.outcome = self.CANCEL

        self.setWindowTitle(f"Resolve Conflicts ({pair_index} of {pair_total})")
        self.resize(700, 500)

        layout = QVBoxLayout(self)

        header = QLabel(f"<b>{html.escape(str(source_label))}</b> will be merged into <b>{html.escape(str(target_label))}</b>. These fields differ:")
        header.setWordWrap(True)
        layout.addWidget(header)

        self.grid_widget = ConflictGridWidget(source_entity, target_entity, id_attr, source_label, target_label)
        layout.addWidget(self.grid_widget)

        footer = QHBoxLayout()

        cancel_btn = QPushButton("Cancel Merge")
        cancel_btn.setToolTip("Abort the whole batch -- nothing checked so far has been merged")
        cancel_btn.clicked.connect(lambda: self._finish(self.CANCEL))
        footer.addWidget(cancel_btn)

        footer.addStretch()

        skip_all_btn = QPushButton("Skip All Remaining")
        skip_all_btn.setToolTip("Keep the canonical entry's fields for this pair and every other pair still awaiting review")
        skip_all_btn.clicked.connect(lambda: self._finish(self.SKIP_ALL))
        footer.addWidget(skip_all_btn)

        skip_btn = QPushButton(_esc_amp("Skip This Pair (Keep Canonical)"))
        skip_btn.clicked.connect(lambda: self._finish(self.SKIP_PAIR))
        footer.addWidget(skip_btn)

        use_btn = QPushButton("Use Selected Values")
        use_btn.clicked.connect(lambda: self._finish(self.USE_SELECTED))
        footer.addWidget(use_btn)

        layout.addLayout(footer)

    def _finish(self, outcome):
        self.outcome = outcome
        self.accept()

    def resolved_fields(self):
        """Field overrides to apply, or {} if the user didn't choose to use
        the selected values (Skip This Pair / Skip All Remaining both keep
        the canonical entity untouched)."""
        return self.grid_widget.get_resolved_fields() if self.outcome == self.USE_SELECTED else {}
