# ---------------------------------------------------------------------------
# ClassicalTab — FieldFormTab("Classical") plus a "parse the title" action
# ---------------------------------------------------------------------------
"""Classical metadata tab.

Identical to the generic ``FieldFormTab("Classical")`` form, with one extra
button: **Parse Title for Classical Data**. It runs
``classical_title_parser.parse_classical_title`` over the live track title
(read from the Basic tab), shows a preview of what it would set, and on
confirm fills the *blank* Classical fields and rewrites the title down to the
bare movement name. See docs/specs/classical_metadata_from_title.md.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from src.db.db_mapping_tracks import TRACK_FIELDS
from src.track.classical_title_parser import ClassicalTitleParse, parse_classical_title
from src.track.track_edit_fieldform import FieldFormTab

# Fields the preview lists, in display order. is_classical is handled
# separately (always set to checked on a successful parse).
_PREVIEW_FIELDS = (
    "work_type",
    "work_name",
    "classical_catalog_prefix",
    "classical_catalog_number",
    "classical_tempo",
    "movement_number",
    "movement_name",
)


class ClassicalTab(FieldFormTab):
    def __init__(self, tracks: list, controller, parent=None, dialog=None):
        super().__init__("Classical", tracks, controller, parent)
        self._dialog = dialog
        # Titles differ per track and set_if_empty is single-track only, so
        # the parse action is offered only when editing exactly one track.
        if not self.is_multi:
            self._add_parse_button()

    def _add_parse_button(self):
        self.parse_button = QPushButton("🎼 Parse Title for Classical Data")
        self.parse_button.setToolTip(
            "Read work, key, catalogue (Op./BWV/…), movement number and tempo "
            "out of the track title, fill in any blank Classical fields, and "
            "strip that text from the title. Shows a preview first; never "
            "overwrites fields you've already filled in."
        )
        self.parse_button.clicked.connect(self._parse_title)
        self.layout().addRow(self.parse_button)

    # ── action ───────────────────────────────────────────────────────────

    def _live_title(self) -> str:
        if self._dialog is not None:
            return self._dialog.get_live_track_name() or ""
        return self.track.track_name or ""

    def _parse_title(self):
        title = self._live_title().strip()
        if not title:
            QMessageBox.warning(self, "Parse Title", "This track has no title to parse.")
            return

        parse = parse_classical_title(title)
        if not parse.matched:
            QMessageBox.information(
                self, "Parse Title", "Couldn't find any classical metadata in this title."
            )
            return

        if not _ParsePreviewDialog(self, parse, title, self._field_is_filled).exec():
            return

        # is_classical: set_if_empty treats an unchecked box as blank and
        # fills it; a checked box is left alone.
        self.set_if_empty({**parse.to_field_dict(), "is_classical": True})

        new_title = parse.cleaned_title.strip()
        if new_title and new_title != title and self._dialog is not None:
            self._dialog.set_live_track_name(new_title)

    def _field_is_filled(self, field_name: str) -> bool:
        """True if this tab's widget for `field_name` already holds a value
        (so set_if_empty would skip it)."""
        current = self.get_field_value(field_name)
        return current not in (None, "", 0, 0.0, False)


class _ParsePreviewDialog(QDialog):
    """Read-only 'here's what I'll change' confirmation for the parse action."""

    def __init__(self, parent, parse: ClassicalTitleParse, old_title: str, is_filled):
        super().__init__(parent)
        self.setWindowTitle("Parse Title for Classical Data")
        self.setMinimumWidth(460)

        root = QVBoxLayout(self)
        intro = QLabel(
            "These blank fields will be filled and the title trimmed to the "
            "movement name. Fields you've already filled in are kept as-is."
        )
        intro.setWordWrap(True)
        root.addWidget(intro)

        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
        proposed = parse.to_field_dict()
        for name in _PREVIEW_FIELDS:
            if name not in proposed:
                continue
            cfg = TRACK_FIELDS.get(name)
            friendly = (cfg.friendly if cfg else None) or name
            value_lbl = QLabel(str(proposed[name]))
            value_lbl.setWordWrap(True)
            if is_filled(name):
                value_lbl.setText(f"{proposed[name]}   —  kept (already set)")
            form.addRow(f"{friendly}:", value_lbl)

        if not is_filled("is_classical"):
            form.addRow("Classical:", QLabel("Yes"))

        new_title = parse.cleaned_title.strip()
        if new_title and new_title != old_title:
            title_lbl = QLabel(f"{old_title}\n→  {new_title}")
            title_lbl.setWordWrap(True)
            form.addRow("New title:", title_lbl)
        root.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Apply")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
