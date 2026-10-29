"""Dialog that picks or creates an entity to link to an award."""

from typing import Any

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import QComboBox, QDialog, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QTabWidget, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget

from src.award.award_common import ASSOCIATION_TYPES, ENTITY_FIELDS, RECIPIENT, entity_display_name

# Bare tracks/albums made from just a name have no file or artist, so only these can be created here.
CREATABLE_TYPES = {"Artist", "Publisher"}
SEARCH_LIMIT = 200

_SEARCH_TAB, _CREATE_TAB = 0, 1


class AwardRelationshipDialog(QDialog):
    """Search for (or create) an entity and emit it with the chosen relationship type."""

    relationship_added = Signal(str, int, str)  # entity_type, entity_id, relationship_type

    def __init__(self, award: Any, controller: Any, parent=None, default_relationship: str = RECIPIENT):
        super().__init__(parent)
        self.award = award
        self.controller = controller
        self.default_relationship = default_relationship

        self.setWindowTitle("Add Award Relationship")
        self.setMinimumWidth(500)

        self.search_timer = QTimer(self)
        self.search_timer.setInterval(250)
        self.search_timer.setSingleShot(True)
        self.search_timer.timeout.connect(self._perform_search)

        self.init_ui()

    def init_ui(self):
        """Build the type pickers, the Search/Create tabs and the buttons."""
        layout = QVBoxLayout(self)

        rel_type_layout = QHBoxLayout()
        rel_label = QLabel("Relationship Type:")
        rel_type_layout.addWidget(rel_label)
        self.relationship_combo = QComboBox()
        self.relationship_combo.setEditable(True)  # custom types are allowed
        self.relationship_combo.setInsertPolicy(QComboBox.InsertAtTop)
        self.relationship_combo.addItems(ASSOCIATION_TYPES)
        index = self.relationship_combo.findText(self.default_relationship)
        if index >= 0:
            self.relationship_combo.setCurrentIndex(index)
        else:
            self.relationship_combo.setEditText(self.default_relationship)
        rel_label.setBuddy(self.relationship_combo)
        rel_type_layout.addWidget(self.relationship_combo)
        rel_type_layout.addStretch()
        layout.addLayout(rel_type_layout)

        type_layout = QHBoxLayout()
        type_label = QLabel("Entity Type:")
        type_layout.addWidget(type_label)
        self.entity_type_combo = QComboBox()
        self.entity_type_combo.addItems(list(ENTITY_FIELDS))
        self.entity_type_combo.currentTextChanged.connect(self._on_entity_type_changed)
        type_label.setBuddy(self.entity_type_combo)
        type_layout.addWidget(self.entity_type_combo)
        type_layout.addStretch()
        layout.addLayout(type_layout)

        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)

        search_page = QWidget()
        search_layout = QVBoxLayout(search_page)
        search_input_layout = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Search for existing...")
        self.search_edit.textChanged.connect(self.search_timer.start)
        self.search_btn = QPushButton("Search")
        self.search_btn.clicked.connect(self._perform_search)
        search_input_layout.addWidget(self.search_edit)
        search_input_layout.addWidget(self.search_btn)
        search_layout.addLayout(search_input_layout)

        self.results_list = QTreeWidget()
        self.results_list.setHeaderLabels(["Name"])
        self.results_list.setRootIsDecorated(False)
        self.results_list.setMinimumHeight(200)
        self.results_list.itemDoubleClicked.connect(self._on_entity_selected)
        self.results_list.itemSelectionChanged.connect(self._on_selection_changed)
        search_layout.addWidget(self.results_list)

        self.results_note = QLabel()
        self.results_note.setProperty("textRole", "note")
        search_layout.addWidget(self.results_note)

        select_layout = QHBoxLayout()
        self.select_btn = QPushButton("Select Entity")
        self.select_btn.setEnabled(False)
        self.select_btn.clicked.connect(self._on_select_clicked)
        select_layout.addWidget(self.select_btn)
        select_layout.addStretch()
        search_layout.addLayout(select_layout)
        self.tabs.addTab(search_page, "Search Existing")

        create_page = QWidget()
        create_layout = QVBoxLayout(create_page)
        self.new_entity_name = QLineEdit()
        self.new_entity_name.setPlaceholderText("Enter name...")
        create_layout.addWidget(self.new_entity_name)
        create_btn_layout = QHBoxLayout()
        self.create_btn = QPushButton("Create New")
        self.create_btn.clicked.connect(self._create_new_entity)
        create_btn_layout.addWidget(self.create_btn)
        create_btn_layout.addStretch()
        create_layout.addLayout(create_btn_layout)
        create_layout.addStretch()
        self.tabs.addTab(create_page, "Create New")

        btn_layout = QHBoxLayout()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(cancel_btn)
        btn_layout.addStretch()
        layout.addLayout(btn_layout)

        self._on_entity_type_changed(self.entity_type_combo.currentText())
        self.search_edit.setFocus()

    def _relationship_type(self) -> str:
        """Chosen relationship type, lowercased; empty falls back to recipient."""
        return self.relationship_combo.currentText().strip().lower() or RECIPIENT

    def _on_entity_type_changed(self, entity_type: str):
        """Refresh the results and allow Create only for creatable types."""
        creatable = entity_type in CREATABLE_TYPES
        self.tabs.setTabEnabled(_CREATE_TAB, creatable)
        self.tabs.setTabToolTip(_CREATE_TAB, "" if creatable else f"A {entity_type} cannot be created from a name alone")
        if not creatable:
            self.tabs.setCurrentIndex(_SEARCH_TAB)
        self._perform_search()

    def _perform_search(self):
        """Show up to SEARCH_LIMIT entities of the chosen type whose name contains the search text."""
        entity_type = self.entity_type_combo.currentText()
        name_attr, id_attr = ENTITY_FIELDS[entity_type]
        search_text = self.search_edit.text().strip()
        self.results_list.clear()

        # Filter and cap in SQL: a full Track table is far too large to load per keystroke.
        filters = {f"{name_attr}__contains": search_text} if search_text else {}
        entities = self.controller.get.get_all_entities(entity_type, limit=SEARCH_LIMIT + 1, **filters)
        truncated = len(entities) > SEARCH_LIMIT
        entities = entities[:SEARCH_LIMIT]

        for entity in sorted(entities, key=lambda e: (getattr(e, name_attr) or "").casefold()):
            item = QTreeWidgetItem([entity_display_name(entity_type, entity)])
            item.setData(0, Qt.UserRole, (entity_type, getattr(entity, id_attr)))
            self.results_list.addTopLevelItem(item)

        if truncated:
            self.results_note.setText(f"Showing the first {SEARCH_LIMIT} matches. Type more to narrow the search.")
        elif not entities:
            self.results_note.setText("No matches.")
        else:
            self.results_note.setText("")

    def _on_entity_selected(self, item: QTreeWidgetItem, _column: int = 0):
        """Emit the picked entity and close."""
        entity_type, entity_id = item.data(0, Qt.UserRole)
        self.relationship_added.emit(entity_type, entity_id, self._relationship_type())
        self.accept()

    def _on_select_clicked(self):
        """Pick the selected result."""
        items = self.results_list.selectedItems()
        if items:
            self._on_entity_selected(items[0])

    def _on_selection_changed(self):
        """Enable Select only while a result is selected."""
        self.select_btn.setEnabled(bool(self.results_list.selectedItems()))

    def _create_new_entity(self):
        """Create an entity from the typed name, then emit it and close."""
        entity_name = self.new_entity_name.text().strip()
        if not entity_name:
            QMessageBox.warning(self, "Missing Name", "Please enter a name.")
            return

        entity_type = self.entity_type_combo.currentText()
        if entity_type not in CREATABLE_TYPES:
            return
        name_attr, id_attr = ENTITY_FIELDS[entity_type]
        new_entity = self.controller.add.add_entity(entity_type, **{name_attr: entity_name})
        if not new_entity:
            QMessageBox.critical(self, "Error", f"Failed to create the new {entity_type}. See the log for details.")
            return

        self.relationship_added.emit(entity_type, getattr(new_entity, id_attr), self._relationship_type())
        self.accept()
