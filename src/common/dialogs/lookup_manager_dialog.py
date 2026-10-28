"""Shared add/delete/rename scaffold for "manage a global lookup vocabulary" dialogs."""

from PySide6.QtWidgets import QDialog, QHBoxLayout, QInputDialog, QMessageBox, QPushButton, QVBoxLayout
from sqlalchemy.exc import SQLAlchemyError

from src.foundation.logger_config import logger

NAME_COL = 0
DESC_COL = 1
COUNT_COL = 2


class BaseLookupManagerDialog(QDialog):
    """Shared button row / add / delete / rename-validation scaffold for a lookup table."""

    # Subclasses set the class attributes below and implement
    # _build_content_widget(), _load(), _selected_entries() -> [(id, name, count_text)]
    # and _fetch_counts() (use _safe_fetch_counts() for uniform error handling).

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
    _COUNT_NOUN: str = "artist"  # what the usage count in the delete prompt counts

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

        self._build_extra_buttons(btn_row)

        btn_row.addStretch()

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)

        layout.addLayout(btn_row)

    def _build_content_widget(self):
        raise NotImplementedError

    def _build_extra_buttons(self, btn_row) -> None:
        """Optional hook to add buttons between Delete Selected and the stretch; no-op by default."""

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

    def _name_taken(self, name: str, exclude_id=None) -> bool:
        """True if another row already has `name`, ignoring case."""
        lowered = name.lower()
        try:
            rows = self.controller.get.get_all_entities(self._ENTITY_TYPE) or []
        except SQLAlchemyError as e:
            logger.warning(f"Failed to check {self._ENTITY_LABEL} names: {e}")
            return False
        return any((getattr(row, self._NAME_ATTR, "") or "").strip().lower() == lowered and getattr(row, self._ID_ATTR) != exclude_id for row in rows)

    def _warn_save_failed(self, action: str) -> None:
        QMessageBox.critical(self, "Error", f"Could not {action} the {self._ENTITY_LABEL}. Please check the logs.")

    # ── Add ──────────────────────────────────────────────────────────────

    def _add(self):
        name, ok = QInputDialog.getText(self, self._ADD_DIALOG_TITLE, self._ADD_DIALOG_PROMPT)
        name = name.strip()
        if not ok or not name:
            return

        if self._name_taken(name):
            QMessageBox.warning(self, "Duplicate Name", f"A {self._ENTITY_LABEL} named '{name}' already exists.")
            return

        if self.controller.add.add_entity(self._ENTITY_TYPE, **{self._NAME_ATTR: name}) is None:
            self._warn_save_failed("add")
        self._load()

    # ── Rename / description edit validation ────────────────────────────

    def _validate_and_rename(self, entity_id, new_name: str) -> bool:
        """Validate and save a name edit; on rejection `_load()` discards it and False is returned."""
        new_name = new_name.strip()
        if not new_name:
            QMessageBox.warning(self, "Invalid Name", f"{self._NAME_EMPTY_LABEL} cannot be empty.")
            self._load()
            return False

        if self._name_taken(new_name, exclude_id=entity_id):
            QMessageBox.warning(self, "Duplicate Name", f"A {self._ENTITY_LABEL} named '{new_name}' already exists.")
            self._load()
            return False

        if not self.controller.update.update_entity(self._ENTITY_TYPE, entity_id, **{self._NAME_ATTR: new_name}):
            self._warn_save_failed("rename")
            self._load()
            return False
        return True

    def _save_description(self, entity_id, new_desc: str) -> None:
        """Save a description edit; blank text stores NULL."""
        if not self.controller.update.update_entity(self._ENTITY_TYPE, entity_id, **{self._DESC_ATTR: new_desc.strip() or None}):
            self._warn_save_failed("update")
            self._load()

    # ── Delete ───────────────────────────────────────────────────────────

    def _delete_selected(self):
        entries = self._selected_entries()
        if not entries:
            QMessageBox.information(self, self._DELETE_DIALOG_TITLE, self._DELETE_SELECT_FIRST_MSG)
            return

        lines = [f"• {name} ({count} {self._COUNT_NOUN}(s))" for _id, name, count in entries]
        reply = QMessageBox.question(self, self._DELETE_DIALOG_TITLE, f"{self._DELETE_INTRO}\n\n" + "\n".join(lines), QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply != QMessageBox.Yes:
            return

        failed = [name for entity_id, name, _count in entries if not self.controller.delete.delete_entity(self._ENTITY_TYPE, **{self._ID_ATTR: entity_id})]
        if failed:
            QMessageBox.critical(self, "Error", "Could not delete:\n" + "\n".join(failed))
        self._load()
