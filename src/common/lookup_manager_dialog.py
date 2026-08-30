"""
lookup_manager_dialog.py

Shared scaffold for "manage a global lookup vocabulary" dialogs -- a table
or tree of Name/Description/# Artists rows with inline rename/description
editing, add, and delete (currently used by ArtistType, a flat list, and
Religion, a drag-and-drop-reparentable hierarchy). The content widget itself
(QTableWidget vs. QTreeWidget) and how it's populated stay subclass-specific
-- that's genuinely different work for a flat list vs. a hierarchy -- but the
button row, add-new-entry flow, delete-selected flow, and name/description
edit validation are the same shape regardless of which widget holds the rows.
"""

from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QInputDialog,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)
from sqlalchemy.exc import SQLAlchemyError

from src.core.logger_config import logger

NAME_COL = 0
DESC_COL = 1
COUNT_COL = 2


class BaseLookupManagerDialog(QDialog):
    """Shared button row / add / delete / rename-validation scaffold.

    Subclasses must:
      - set `_ENTITY_TYPE` / `_ID_ATTR` / `_NAME_ATTR` / `_DESC_ATTR` class
        attributes (the DB entity name and its id/name/description fields)
      - set `_ENTITY_LABEL` (lowercase, e.g. "type"/"religion", used in
        "A {label} named '...' already exists.") and `_NAME_EMPTY_LABEL`
        (e.g. "Type name"/"Religion name", used in "{label} cannot be
        empty.")
      - set `_ADD_BUTTON_TEXT` / `_ADD_DIALOG_TITLE` / `_ADD_DIALOG_PROMPT`
      - set `_DELETE_SELECT_FIRST_MSG` / `_DELETE_DIALOG_TITLE` /
        `_DELETE_INTRO` (the sentence before the bullet list of entries to
        delete -- this is where hierarchy-specific wording, e.g. "child
        religions will lose their parent", differs)
      - implement `_build_content_widget()`, returning the fully configured
        QTableWidget/QTreeWidget (columns, selection mode, signals) to embed
        above the button row
      - implement `_load()`, populating the content widget from the DB
      - implement `_selected_entries()`, returning `[(entity_id, name,
        count_text), ...]` for whatever is currently selected
      - implement `_fetch_counts()` (the usage-count query differs too much
        structurally between a flat FK group-by and a hierarchy's to share;
        use `_safe_fetch_counts()` below to keep the error handling uniform)
    """

    _ENTITY_TYPE: str = ""
    _ID_ATTR: str = ""
    _NAME_ATTR: str = ""
    _DESC_ATTR: str = ""
    _ENTITY_LABEL: str = ""
    _NAME_EMPTY_LABEL: str = ""
    _ADD_BUTTON_TEXT: str = "Add"
    _ADD_DIALOG_TITLE: str = ""
    _ADD_DIALOG_PROMPT: str = ""
    _DELETE_SELECT_FIRST_MSG: str = ""
    _DELETE_DIALOG_TITLE: str = ""
    _DELETE_INTRO: str = ""

    def __init__(self, controller, title: str, size: tuple[int, int], parent=None):
        super().__init__(parent)
        self.controller = controller
        self.setWindowTitle(title)
        self.resize(*size)
        self._build_ui()
        self._load()

    # ── UI scaffold ──────────────────────────────────────────────────────

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.addWidget(self._build_content_widget())

        btn_row = QHBoxLayout()
        add_btn = QPushButton(self._ADD_BUTTON_TEXT)
        add_btn.clicked.connect(self._add)
        btn_row.addWidget(add_btn)

        delete_btn = QPushButton("Delete Selected")
        delete_btn.clicked.connect(self._delete_selected)
        btn_row.addWidget(delete_btn)

        btn_row.addStretch()

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)

        layout.addLayout(btn_row)

    def _build_content_widget(self):
        raise NotImplementedError

    def _load(self):
        raise NotImplementedError

    def _selected_entries(self) -> list[tuple[int, str, str]]:
        raise NotImplementedError

    def _fetch_counts(self) -> dict:
        raise NotImplementedError

    def _safe_fetch_counts(self, query) -> dict:
        """Run a usage-count query, logging and returning {} on failure."""
        try:
            rows = self.controller.get.session.execute(query).all()
            return dict(rows)
        except SQLAlchemyError as e:
            logger.warning(f"Failed to fetch {self._ENTITY_LABEL} usage counts: {e}")
            return {}

    # ── Add ──────────────────────────────────────────────────────────────

    def _add(self):
        name, ok = QInputDialog.getText(self, self._ADD_DIALOG_TITLE, self._ADD_DIALOG_PROMPT)
        name = name.strip()
        if not ok or not name:
            return

        existing = self.controller.get.get_entity_object(
            self._ENTITY_TYPE, **{self._NAME_ATTR: name}
        )
        if existing:
            QMessageBox.warning(
                self, "Duplicate Name", f"A {self._ENTITY_LABEL} named '{name}' already exists."
            )
            return

        self.controller.add.add_entity(self._ENTITY_TYPE, **{self._NAME_ATTR: name})
        self._load()

    # ── Rename / description edit validation ────────────────────────────

    def _validate_and_rename(self, entity_id, new_name: str) -> bool:
        """Validate a name edit and persist it. Returns True if applied,
        False if rejected (in which case `_load()` was already called to
        discard the bad edit)."""
        new_name = new_name.strip()
        if not new_name:
            QMessageBox.warning(self, "Invalid Name", f"{self._NAME_EMPTY_LABEL} cannot be empty.")
            self._load()
            return False

        existing = self.controller.get.get_entity_object(
            self._ENTITY_TYPE, **{self._NAME_ATTR: new_name}
        )
        if existing and getattr(existing, self._ID_ATTR) != entity_id:
            QMessageBox.warning(
                self, "Duplicate Name", f"A {self._ENTITY_LABEL} named '{new_name}' already exists."
            )
            self._load()
            return False

        self.controller.update.update_entity(
            self._ENTITY_TYPE, entity_id, **{self._NAME_ATTR: new_name}
        )
        return True

    def _save_description(self, entity_id, new_desc: str) -> None:
        self.controller.update.update_entity(
            self._ENTITY_TYPE, entity_id, **{self._DESC_ATTR: new_desc.strip() or None}
        )

    # ── Delete ───────────────────────────────────────────────────────────

    def _delete_selected(self):
        entries = self._selected_entries()
        if not entries:
            QMessageBox.information(self, self._DELETE_DIALOG_TITLE, self._DELETE_SELECT_FIRST_MSG)
            return

        lines = [f"• {name} ({count} artist(s))" for _id, name, count in entries]
        reply = QMessageBox.question(
            self,
            self._DELETE_DIALOG_TITLE,
            f"{self._DELETE_INTRO}\n\n" + "\n".join(lines),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        for entity_id, _name, _count in entries:
            self.controller.delete.delete_entity(self._ENTITY_TYPE, **{self._ID_ATTR: entity_id})
        self._load()
