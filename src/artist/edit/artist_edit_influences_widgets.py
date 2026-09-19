# ══════════════════════════════════════════════════════════════════════════════
# Tab: Influences — UI helper widgets
# ══════════════════════════════════════════════════════════════════════════════
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.common.widgets.entity_completer_edit import EntityCompleterEdit

# Direction constants — stored in the table's UserRole+1 slot per row.
DIR_INFLUENCED = "influenced"  # this artist -> other artist (other was influenced by this one)
DIR_INFLUENCER = "influencer"  # other artist -> this artist (this one was influenced by other)

_DIRECTION_COLOR = {
    DIR_INFLUENCED: QColor("#2f7dd1"),  # blue: this artist influenced them
    DIR_INFLUENCER: QColor("#c17a2a"),  # amber: they influenced this artist
}


def _esc_amp(text):
    """Escape '&' so it doesn't act as a Qt mnemonic prefix in button/label text."""
    return (text or "").replace("&", "&&")


def _cap(text):
    """Capitalize just the first character — safe for names ('lady Gaga' stays intact)."""
    return text[0].upper() + text[1:] if text else text


def _relationship_sentence(direction, artist_name, other_name=None):
    """
    Always-active-voice sentence naming both artists — 'X influenced Y' — so
    the relationship can never be misread the way passive "Influenced by" or
    a bare arrow could be. When `other_name` isn't known yet (e.g. an empty
    add-bar field), falls back to a grammatically-correct pronoun.
    """
    name = artist_name or "this artist"
    if direction == DIR_INFLUENCED:
        # this artist -> other: this artist is the influencer (subject)
        other = other_name or "them"
        return _cap(f"{name} influenced {other}")
    # other -> this artist: other artist is the influencer (subject)
    other = other_name or "they"
    return _cap(f"{other} influenced {name}")


# ── filter chips ─────────────────────────────────────────────────────────────


class _FilterChips(QWidget):
    """
    Small exclusive chip row: All / "<artist> influenced them" / "They influenced <artist>".
    Always active voice and names the edited artist explicitly so the filter can't be misread.
    """

    def __init__(self, on_changed, parent=None):
        super().__init__(parent)
        self._on_changed = on_changed
        self._artist_name = "this artist"
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self.group = QButtonGroup(self)
        self.group.setExclusive(True)

        self._buttons = {}
        for key in (None, DIR_INFLUENCED, DIR_INFLUENCER):
            btn = QPushButton()
            btn.setCheckable(True)
            btn.setProperty("class", "filterChip")
            btn.setCursor(Qt.PointingHandCursor)
            self.group.addButton(btn)
            self._buttons[key] = btn
            layout.addWidget(btn)
        layout.addStretch()

        self._buttons[None].setChecked(True)
        self.group.buttonClicked.connect(self._emit_change)
        self._counts = {}
        self._relabel()

    def _emit_change(self, _btn):
        self._on_changed(self.current_filter())

    def current_filter(self):
        for key, btn in self._buttons.items():
            if btn.isChecked():
                return key
        return None

    def set_artist_name(self, name):
        self._artist_name = name or "this artist"
        self._relabel()

    def set_counts(self, counts: dict):
        """counts: {None: total, DIR_INFLUENCED: n, DIR_INFLUENCER: n}"""
        self._counts = counts
        self._relabel()

    def _relabel(self):
        labels = {
            None: "All",
            DIR_INFLUENCED: _relationship_sentence(DIR_INFLUENCED, self._artist_name),
            DIR_INFLUENCER: _relationship_sentence(DIR_INFLUENCER, self._artist_name),
        }
        for key, btn in self._buttons.items():
            n = self._counts.get(key, 0)
            btn.setText(_esc_amp(f"{labels[key]} ({n})"))


# ── add bar ──────────────────────────────────────────────────────────────────


class _AddInfluenceBar(QWidget):
    """
    Single add row for either direction: a direction toggle, artist name
    (with completer), description, and Add. Replaces the old per-panel
    duplicated toolbar.
    """

    def __init__(self, on_add, artist_name=None, parent=None):
        super().__init__(parent)
        self._on_add = on_add
        self._artist_name = artist_name or "this artist"

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 0)
        layout.setSpacing(6)

        # Direction toggle. Labels stay short and fixed-width — "Influenced"
        # vs. "Influenced by" — regardless of how long the typed artist name
        # gets, so the bar can't be crammed. The full "X influenced Y"
        # sentence (with real names) lives in the tooltip instead, and
        # live-updates as the name field is typed into.
        self.dir_group = QButtonGroup(self)
        self.dir_group.setExclusive(True)
        self.btn_influenced = QPushButton(_esc_amp("Influenced"))
        self.btn_influenced.setObjectName("dirLeft")
        self.btn_influencer = QPushButton(_esc_amp("Influenced by"))
        self.btn_influencer.setObjectName("dirRight")
        for b in (self.btn_influenced, self.btn_influencer):
            b.setCheckable(True)
            b.setProperty("class", "dirToggle")
            b.setCursor(Qt.PointingHandCursor)
            self.dir_group.addButton(b)
        self.btn_influenced.setChecked(True)

        dir_box = QHBoxLayout()
        dir_box.setSpacing(0)
        dir_box.addWidget(self.btn_influenced)
        dir_box.addWidget(self.btn_influencer)

        self.name_edit = EntityCompleterEdit("Artist name…")
        self.name_edit.textChanged.connect(self._relabel_toggle)
        self.desc_edit = QLineEdit()
        self.desc_edit.setPlaceholderText("Description (optional)")

        self._relabel_toggle()

        add_btn = QPushButton("Add")
        add_btn.setFixedWidth(60)
        add_btn.clicked.connect(self._handle_add)
        self.name_edit.returnPressed.connect(self._handle_add)
        self.desc_edit.returnPressed.connect(self._handle_add)

        layout.addLayout(dir_box)
        layout.addWidget(self.name_edit, 3)
        layout.addWidget(self.desc_edit, 3)
        layout.addWidget(add_btn)

    def current_direction(self):
        return DIR_INFLUENCED if self.btn_influenced.isChecked() else DIR_INFLUENCER

    def set_artist_name(self, name):
        self._artist_name = name or "this artist"
        self._relabel_toggle()

    def _relabel_toggle(self, *_args):
        # Button text stays short and fixed ("Influenced" / "Influenced by");
        # only the tooltip spells out the full sentence with real names.
        name = self._artist_name
        other = self.name_edit.text().strip() or None

        influenced_sentence = _relationship_sentence(DIR_INFLUENCED, name, other)
        influencer_sentence = _relationship_sentence(DIR_INFLUENCER, name, other)

        self.btn_influenced.setToolTip(influenced_sentence + ".")
        self.btn_influencer.setToolTip(influencer_sentence + ".")

    def _handle_add(self):
        names = self.name_edit.split_names()
        if not names:
            return
        self._on_add(
            direction=self.current_direction(),
            names=names,
            description=self.desc_edit.text().strip() or None,
            # matched_id only names a single typed artist -- with several
            # typed at once (e.g. "A;B;C") each is resolved by name instead
            # of relying on that one-shot completer pick.
            matched_artist_id=self.name_edit.matched_id() if len(names) == 1 else None,
        )

    def clear_inputs(self):
        self.name_edit.reset()
        self.desc_edit.clear()

    def set_completer_index(self, index, context_by_id=None):
        self.name_edit.set_index(index, context_by_id)

    def register_new_artist(self, display, artist_id):
        # Deferred: this can run nested inside EntityCompleterEdit's own
        # keyPressEvent (Enter -> returnPressed fires *during* that native
        # call, via _handle_add -> self._on_add -> here). add_to_index()
        # replaces the QCompleter object in place, and doing that while Qt's
        # own key handling is still mid-execution on that same completer
        # corrupts its internals and crashes the process. See
        # artist_edit_types.py _flush_new_chip_paint() for the same hazard.
        name_edit = self.name_edit
        QTimer.singleShot(0, lambda: name_edit.add_to_index(display, artist_id))


class _EditDescriptionDialog(QDialog):
    """Small modal for editing an existing influence relation's description."""

    def __init__(self, description, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit Description")

        layout = QVBoxLayout(self)
        self.desc_edit = QLineEdit(description or "")
        self.desc_edit.setPlaceholderText("Description (optional)")
        layout.addWidget(self.desc_edit)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.desc_edit.setFocus()
        self.desc_edit.selectAll()

    def value(self):
        return self.desc_edit.text().strip() or None
