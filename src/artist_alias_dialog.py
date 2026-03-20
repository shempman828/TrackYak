"""
artist_alias_dialog.py

Alias utilities shared across the app:
  - AliasEditDialog  — add/edit a single alias (type field uses autocomplete)
  - AliasRowWidget   — compact row-level Edit / Swap / Delete buttons
  - ArtistAliasDialog — kept for backwards compatibility; wraps AliasesTab
                        in a standalone QDialog if ever needed.
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCompleter,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.logger_config import logger

# ── Well-known alias types offered as autocomplete suggestions ─────────────
SUGGESTED_ALIAS_TYPES = [
    "Legal Name",
    "Stylized Name",
    "Project Name",
    "Persona",
    "Birth Name",
    "Former Name",
    "Localized Name",
    "Romanized Name",
    "Phonetic Name",
    "Nickname",
    "Other",
]

# Column indices (shared constant so both files agree)
COL_NAME = 0
COL_TYPE = 1
COL_ACTIONS = 2


# ── Row-level action buttons ───────────────────────────────────────────────


class AliasRowWidget(QWidget):
    """
    Compact Edit / ↕ Use as Name / ✕ buttons rendered inside a table cell.

    The buttons are always visible (no hover-only magic needed — Qt tables
    paint cell widgets on top of the row background anyway).
    """

    def __init__(self, edit_cb, delete_cb, swap_cb, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(4)

        btn_edit = QPushButton("Edit")
        btn_edit.setFixedWidth(46)
        btn_edit.setFlat(True)
        btn_edit.setStyleSheet(
            "QPushButton { border: 1px solid palette(mid); border-radius: 3px; padding: 1px 4px; }"
            "QPushButton:hover { background: palette(highlight); color: palette(highlighted-text); }"
        )
        btn_edit.clicked.connect(edit_cb)

        btn_swap = QPushButton("↕ Use as Name")
        btn_swap.setFlat(True)
        btn_swap.setToolTip("Promote this alias to the artist's primary name")
        btn_swap.setStyleSheet(
            "QPushButton { border: 1px solid palette(mid); border-radius: 3px; padding: 1px 6px; }"
            "QPushButton:hover { background: palette(highlight); color: palette(highlighted-text); }"
        )
        btn_swap.clicked.connect(swap_cb)

        btn_delete = QPushButton("✕")
        btn_delete.setFixedWidth(26)
        btn_delete.setFlat(True)
        btn_delete.setToolTip("Delete this alias")
        btn_delete.setStyleSheet(
            "QPushButton { border: 1px solid transparent; border-radius: 3px; color: #cc4444; }"
            "QPushButton:hover { border-color: #cc4444; background: #fff0f0; }"
        )
        btn_delete.clicked.connect(delete_cb)

        layout.addWidget(btn_edit)
        layout.addWidget(btn_swap)
        layout.addStretch()
        layout.addWidget(btn_delete)


# ── Add / Edit sub-dialog ──────────────────────────────────────────────────


class AliasEditDialog(QDialog):
    """
    Small dialog for entering or editing a single alias.

    The type field is a free-text QLineEdit with a QCompleter that suggests
    both the built-in SUGGESTED_ALIAS_TYPES and any extra types already
    present in the caller's alias list, so users can define their own
    custom types while still benefiting from quick suggestions.

    Parameters
    ----------
    alias_name : str
        Pre-filled name (empty when adding).
    alias_type : str
        Pre-filled type (empty when adding).
    extra_types : list[str]
        Additional type strings to include in autocomplete (e.g. types
        already used by other aliases for this artist).
    """

    def __init__(
        self,
        alias_name: str = "",
        alias_type: str = "",
        extra_types: list[str] | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Add Alias" if not alias_name else "Edit Alias")
        self.setMinimumWidth(360)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        # ── Name field ──
        self.name_edit = QLineEdit(alias_name)
        self.name_edit.setPlaceholderText("e.g. Marshall Mathers")
        form.addRow("Alias Name:", self.name_edit)

        # ── Type field with autocomplete ──
        self.type_edit = QLineEdit(alias_type)
        self.type_edit.setPlaceholderText("e.g. Birth Name  (or type your own)")
        self.type_edit.setClearButtonEnabled(True)

        all_types = list(dict.fromkeys(SUGGESTED_ALIAS_TYPES + (extra_types or [])))
        completer = QCompleter(all_types, self)
        completer.setCaseSensitivity(Qt.CaseInsensitive)
        completer.setFilterMode(Qt.MatchContains)
        completer.setCompletionMode(QCompleter.PopupCompletion)
        self.type_edit.setCompleter(completer)

        type_hint = QLabel(
            "<small><i>Choose a suggestion or type a custom type — "
            "leave blank if unspecified.</i></small>"
        )
        type_hint.setWordWrap(True)

        form.addRow("Alias Type:", self.type_edit)
        layout.addLayout(form)
        layout.addWidget(type_hint)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_accept(self):
        if not self.name_edit.text().strip():
            QMessageBox.warning(self, "Validation", "Alias name cannot be empty.")
            return
        self.accept()

    @property
    def alias_name(self) -> str:
        return self.name_edit.text().strip()

    @property
    def alias_type(self) -> str:
        return self.type_edit.text().strip()


# ── Standalone dialog wrapper (backwards-compatible) ──────────────────────


class ArtistAliasDialog(QDialog):
    """
    Wraps the embedded AliasesTab in a standalone QDialog.

    Kept for any call-sites that still open a separate window.  New code
    should embed AliasesTab directly (see artist_edit_alias.py).
    """

    def __init__(self, controller, artist, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Manage Aliases — {artist.artist_name}")
        self.setMinimumSize(640, 460)
        self.setModal(True)

        # Import here to avoid circular imports at module load time
        from src.artist_edit_alias import AliasesTab

        layout = QVBoxLayout(self)
        self.tab = AliasesTab(controller, artist, parent=self)
        self.tab.load(artist)
        layout.addWidget(self.tab)

        btn_box = QDialogButtonBox(QDialogButtonBox.Close)
        btn_box.rejected.connect(self.accept)
        layout.addWidget(btn_box)
