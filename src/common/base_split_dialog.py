import re

from PySide6.QtCore import QStringListModel, Qt
from PySide6.QtWidgets import (
    QCompleter,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import object_mapper

from src.core.logger_config import logger
from src.core.status_utility import show_status_message
from src.db.db_helpers import SplitDB

# Common separators used when multiple artists/entities are combined into a
# single name, e.g. "Simon & Garfunkel", "Paul Simon / Art Garfunkel",
# "Artist1, Artist2", "Artist1 feat. Artist2", "Artist1 - Artist2".
# The dash requires surrounding whitespace so it only matches when used as a
# separator, not inside hyphenated names like "Jean-Paul" or "T-Pain".
_SPLIT_DELIMITER_PATTERN = re.compile(
    r"\s*(?:,|;|/|&|\bfeat\.?(?=\s|$)|\bfeaturing\b|\bft\.?(?=\s|$)|"
    r"\bvs\.?(?=\s|$)|\band\b|\bx\b)\s*|\s+-\s+",
    re.IGNORECASE,
)

# Cache of entity display names for autocomplete, keyed by model name, so
# reopening the dialog doesn't re-query the DB every time.
_entity_name_cache = {}


def _find_name_attr_for_entity(entity, model_name: str):
    """Find the name attribute for an entity of the given model type."""
    for attr in (f"{model_name.lower()}_name", "name", "title", f"{model_name.lower()}_title"):
        if hasattr(entity, attr):
            return attr
    return None


def _get_cached_entity_names(get_helper, model_name: str) -> list:
    """Return cached display names for a model type, fetching once via
    get_helper.get_all_entities() and reusing the result on subsequent calls."""
    if model_name in _entity_name_cache:
        return _entity_name_cache[model_name]

    names = set()
    if get_helper is not None:
        try:
            for entity in get_helper.get_all_entities(model_name) or []:
                attr = _find_name_attr_for_entity(entity, model_name)
                value = getattr(entity, attr, None) if attr else None
                if value:
                    names.add(value)
        except SQLAlchemyError as e:
            logger.warning(f"Could not fetch {model_name} names for completer: {e}")

    result = sorted(names)
    _entity_name_cache[model_name] = result
    return result


class SplitDBDialog(QDialog):
    """Dialog for splitting a combined ORM entity (e.g., 'Paul Simon / Art Garfunkel')."""

    def __init__(self, split_helper: SplitDB, model_name: str, entity_obj, parent=None, get_helper=None):
        super().__init__(parent)
        self.split_helper = split_helper
        self.model_name = model_name
        self.entity_obj = entity_obj
        self.get_helper = get_helper
        self.setWindowTitle(f"Split {model_name}")
        self.setMinimumWidth(400)
        self.setMinimumHeight(500)

        # Shared backing model for the row completers; built once from the
        # cached name list so every row's QCompleter reuses the same data.
        self._name_completer_model = QStringListModel(
            _get_cached_entity_names(self.get_helper, self.model_name), self
        )

        self._init_ui()
        self._load_relationship_info()

    def _init_ui(self):
        layout = QVBoxLayout(self)

        # Context Label
        layout.addWidget(QLabel(f"Splitting {self.model_name}:"))

        # Display the original entity name
        name_attr = self._find_name_attribute()
        original_name = getattr(self.entity_obj, name_attr, "Unknown")
        self.original_name = QLineEdit(original_name)
        self.original_name.setReadOnly(True)
        layout.addWidget(self.original_name)

        # Relationship info
        self.relationship_info = QTextEdit()
        self.relationship_info.setReadOnly(True)
        self.relationship_info.setMaximumHeight(100)
        layout.addWidget(QLabel("Current relationships that will be duplicated:"))
        layout.addWidget(self.relationship_info)

        # Instructions
        layout.addWidget(QLabel("Enter the new entities below:"))

        # List of splits
        self.split_list = QListWidget()
        self.split_list.setObjectName("splitList")
        layout.addWidget(self.split_list)

        # Prefill rows with suggested split names based on common delimiters
        # found in the original name (comma, semicolon, slash, ampersand,
        # "feat."/"ft."/"featuring", "vs.", "and", "x"). Fall back to two
        # empty rows if no delimiter-based split was found.
        suggestions = self._suggest_split_names(original_name)
        if len(suggestions) >= 2:
            for name in suggestions:
                self._add_split_row(name)
        else:
            for _ in range(2):
                self._add_split_row("")

        # Add/Remove buttons
        btn_row = QHBoxLayout()
        self.add_btn = QPushButton("Add")
        self.remove_btn = QPushButton("Remove")
        btn_row.addWidget(self.add_btn)
        btn_row.addWidget(self.remove_btn)
        layout.addLayout(btn_row)

        self.add_btn.clicked.connect(lambda: self._add_split_row(""))
        self.remove_btn.clicked.connect(self._remove_selected_row)

        # Warning label
        self.warning_label = QLabel("")
        self.warning_label.setWordWrap(True)
        layout.addWidget(self.warning_label)

        # Split/Cancel buttons
        btn_layout = QHBoxLayout()
        self.split_btn = QPushButton("Split")
        self.cancel_btn = QPushButton("Cancel")
        btn_layout.addStretch()
        btn_layout.addWidget(self.split_btn)
        btn_layout.addWidget(self.cancel_btn)
        layout.addLayout(btn_layout)

        self.split_btn.clicked.connect(self._on_split)
        self.cancel_btn.clicked.connect(self.reject)

        self._update_tab_order()

    def _find_name_attribute(self) -> str:
        """Find the name attribute for the current entity."""
        name_attrs_to_try = [
            f"{self.model_name.lower()}_name",
            "name",
            "title",
            f"{self.model_name.lower()}_title",
        ]
        for name_attr in name_attrs_to_try:
            if hasattr(self.entity_obj, name_attr):
                return name_attr
        return "name"  # fallback

    def _load_relationship_info(self):
        """Load and display information about current relationships."""
        try:
            mapper = object_mapper(self.entity_obj)
            relationship_info = []

            for rel in mapper.relationships:
                if rel.backref or rel.key.startswith("_"):
                    continue

                rel_key = rel.key
                related_value = getattr(self.entity_obj, rel_key)

                if rel.uselist:
                    # Collection relationship
                    count = len(related_value) if related_value else 0
                    if count > 0:
                        relationship_info.append(f"• {rel_key}: {count} items")
                else:
                    # Scalar relationship
                    if related_value is not None:
                        # Try to get a meaningful name for the related object
                        related_name = "Unknown"
                        try:
                            name_attr = self._find_name_attribute_for_obj(related_value)
                            if name_attr:
                                related_name = getattr(
                                    related_value, name_attr, "Unknown"
                                )
                        except (AttributeError, SQLAlchemyError):
                            logger.debug("Could not determine name for related object")
                        relationship_info.append(f"• {rel_key}: {related_name}")

            if relationship_info:
                self.relationship_info.setText("\n".join(relationship_info))
            else:
                self.relationship_info.setText("No relationships found")

        except (AttributeError, SQLAlchemyError) as e:
            logger.error(f"Failed to load relationship info for {self.model_name}: {e}")
            self.relationship_info.setText(f"Could not load relationship info: {e}")

    def _find_name_attribute_for_obj(self, obj) -> str:
        """Find name attribute for a related object."""
        name_attrs_to_try = ["name", "title"]
        for name_attr in name_attrs_to_try:
            if hasattr(obj, name_attr):
                return name_attr
        return None

    def _suggest_split_names(self, original_name: str) -> list:
        """Suggest split names by breaking the original name on common
        multi-entity delimiters (comma, semicolon, slash, ampersand,
        feat./ft./featuring, vs., and, x)."""
        if not original_name:
            return []

        parts = _SPLIT_DELIMITER_PATTERN.split(original_name)

        seen = set()
        suggestions = []
        for part in parts:
            name = part.strip()
            if name and name.lower() not in seen:
                seen.add(name.lower())
                suggestions.append(name)
        return suggestions

    def _add_split_row(self, default_text=""):
        """Add a new editable line for a split name."""
        item = QListWidgetItem()

        # Ensure default_text is a string, not a boolean
        if isinstance(default_text, bool):
            default_text = ""

        widget = QLineEdit(str(default_text))  # Convert to string to be safe
        widget.setPlaceholderText("Enter new name...")
        widget.setMinimumWidth(300)
        widget.textChanged.connect(self._validate_input)

        completer = QCompleter(widget)
        completer.setModel(self._name_completer_model)
        completer.setCaseSensitivity(Qt.CaseInsensitive)
        completer.setFilterMode(Qt.MatchContains)
        widget.setCompleter(completer)

        self.split_list.addItem(item)
        self.split_list.setItemWidget(item, widget)

        # Ensure the style (incl. QSS padding) is applied before measuring the
        # size hint, otherwise the row is sized too short and clips the text.
        widget.ensurePolished()
        size_hint = widget.sizeHint()
        item.setSizeHint(size_hint)

        self._update_tab_order()

    def _remove_selected_row(self):
        """Remove selected name row."""
        row = self.split_list.currentRow()
        if row >= 0:
            self.split_list.takeItem(row)
        self._validate_input()
        self._update_tab_order()

    def _update_tab_order(self):
        """Rebuild the Tab focus chain so it walks through every split-name
        row (including ones added/removed after dialog init) before reaching
        the action buttons."""
        if not hasattr(self, "add_btn"):
            # Buttons aren't built yet (called while pre-populating the
            # initial rows during __init__); the chain is finalized once
            # _init_ui() creates them.
            return
        prev = self.relationship_info
        for i in range(self.split_list.count()):
            item = self.split_list.item(i)
            widget = self.split_list.itemWidget(item)
            if widget is not None:
                self.setTabOrder(prev, widget)
                prev = widget
        self.setTabOrder(prev, self.add_btn)
        self.setTabOrder(self.add_btn, self.remove_btn)
        self.setTabOrder(self.remove_btn, self.split_btn)
        self.setTabOrder(self.split_btn, self.cancel_btn)

    def _validate_input(self):
        """Validate user input and update UI state."""
        names = self._collect_split_names()

        if len(names) < 1:
            self.warning_label.setText("Please enter at least one new entity name.")
            self.split_btn.setEnabled(False)
        elif len(names) == 1:
            self.warning_label.setText(
                "Warning: Only one entity specified. This will create a duplicate with a new name."
            )
            self.split_btn.setEnabled(True)
        else:
            # Updated message to reflect that ALL relationships go to ALL new entities
            self.warning_label.setText(
                f"Will create {len(names)} new entities. "
                f"ALL current relationships will be duplicated to EACH new entity."
            )
            self.split_btn.setEnabled(True)

    def _collect_split_names(self):
        """Return list of entered names."""
        names = []
        for i in range(self.split_list.count()):
            item = self.split_list.item(i)
            widget = self.split_list.itemWidget(item)
            name = widget.text().strip()
            if name:
                names.append(name)
        return names

    def _on_split(self):
        """Collect input and perform the split."""
        names = self._collect_split_names()
        if not names:
            show_status_message(self, "Please enter at least one new entity name.")
            return

        # Confirm the action
        reply = QMessageBox.question(
            self,
            "Confirm Split",
            "All relationships will be copied to the chosen names.\n\n"
            "Are you sure you want to proceed?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )

        if reply != QMessageBox.Yes:
            return

        # Prepare attributes for each new entity
        split_attributes = [{"name": n} for n in names]

        # Get the entity ID using consistent naming
        id_attr = f"{self.model_name.lower()}_id"
        entity_id = getattr(self.entity_obj, id_attr)

        try:
            # Call SplitDB using the split_helper
            self.split_helper.split_entity(
                self.model_name,
                entity_id,
                split_attributes,
            )

            logger.info(
                f"Split {self.model_name} (id={entity_id}) into {len(names)} new entities: {names}"
            )

            # Accept the dialog to close it
            self.accept()

        except SQLAlchemyError as e:
            logger.error(f"Failed to split {self.model_name} (id={entity_id}): {e}")
            QMessageBox.critical(
                self,
                "Split Failed",
                f"An error occurred during the split:\n{str(e)}",
            )
