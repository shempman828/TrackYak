from typing import Any, Callable, Dict, Tuple

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from logger_config import logger

# ──────────────────────────────────────────────────────────────
# Entity configuration
# ──────────────────────────────────────────────────────────────

# For each entity type:
#   key: entity type name as used by controller
#   value: (name_attr, id_attr)
ENTITY_FIELDS: Dict[str, Tuple[str, str]] = {
    "Artist": ("artist_name", "artist_id"),
    "Album": ("album_name", "album_id"),
    "Track": ("track_name", "track_id"),
    "Publisher": ("publisher_name", "publisher_id"),
    "Place": ("place_name", "place_id"),
}

# Relationship type options
RELATIONSHIP_TYPES = [
    "Recipient",
    "Nominee",
    "Presenter",
    "Judge",
    "Host",
    "Sponsor",
    "Organizer",
]


def default_display(entity, name_attr: str, entity_type: str) -> str:
    """Default function to compute display name."""
    return getattr(entity, name_attr)


def album_display(entity, name_attr: str, entity_type: str) -> str:
    """Special display function for albums with year."""
    base = getattr(entity, name_attr)
    return (
        f"{base} ({entity.release_year})"
        if getattr(entity, "release_year", None)
        else base
    )


# Optional custom display logic
DISPLAY_OVERRIDE: Dict[str, Callable] = {"Album": album_display}


# ──────────────────────────────────────────────────────────────
# Dialog class
# ──────────────────────────────────────────────────────────────


class AwardRelationshipDialog(QDialog):
    """Dialog for managing award relationships with entity search and creation."""

    relationship_added = Signal(
        str, int, str
    )  # entity_type, entity_id, relationship_type

    def __init__(self, award: Any, controller: Any, parent=None):
        super().__init__(parent)
        self.award = award
        self.controller = controller

        self.setWindowTitle("Add Award Relationship")
        self.setMinimumWidth(500)

        # Timer for debouncing searches
        self.search_timer = QTimer(self)
        self.search_timer.setInterval(250)
        self.search_timer.setSingleShot(True)
        self.search_timer.timeout.connect(self._perform_search)

        self.init_ui()

    # ─────────────────────────────────────────────── UI setup
    def init_ui(self):
        layout = QVBoxLayout(self)

        # Relationship type
        rel_type_layout = QHBoxLayout()
        rel_type_layout.addWidget(QLabel("Relationship Type:"))

        self.relationship_combo = QComboBox()
        self.relationship_combo.addItems(RELATIONSHIP_TYPES)
        self.relationship_combo.setCurrentText("recipient")
        self.relationship_combo.setEditable(True)  # Allow custom types
        self.relationship_combo.setInsertPolicy(QComboBox.InsertAtTop)

        rel_type_layout.addWidget(self.relationship_combo)
        rel_type_layout.addStretch()
        layout.addLayout(rel_type_layout)

        # Entity type
        type_layout = QHBoxLayout()
        type_layout.addWidget(QLabel("Entity Type:"))

        self.entity_type_combo = QComboBox()
        self.entity_type_combo.addItems(list(ENTITY_FIELDS.keys()))
        self.entity_type_combo.currentTextChanged.connect(self._on_entity_type_changed)

        type_layout.addWidget(self.entity_type_combo)
        type_layout.addStretch()
        layout.addLayout(type_layout)

        # ─────────────────────────────────────────────
        # TAB WIDGET
        # ─────────────────────────────────────────────
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)

        # ─────────────────────────────── Search Tab
        search_tab = QVBoxLayout()
        search_root = QGroupBox("Search Existing")
        search_root.setLayout(search_tab)

        # Search input
        search_input_layout = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Search for existing...")
        self.search_edit.textChanged.connect(self._on_search_changed)

        self.search_btn = QPushButton("Search")
        self.search_btn.clicked.connect(self._perform_search)

        search_input_layout.addWidget(self.search_edit)
        search_input_layout.addWidget(self.search_btn)
        search_tab.addLayout(search_input_layout)

        # Results list
        self.results_list = QTreeWidget()
        self.results_list.setHeaderLabels(["Name", "Type", "ID"])
        self.results_list.setColumnHidden(2, True)
        self.results_list.setMinimumHeight(200)
        self.results_list.itemDoubleClicked.connect(self._on_entity_selected)

        search_tab.addWidget(self.results_list)

        # Select button
        select_layout = QHBoxLayout()
        self.select_btn = QPushButton("Select Entity")
        self.select_btn.setEnabled(False)
        self.select_btn.clicked.connect(self._on_select_clicked)

        select_layout.addWidget(self.select_btn)
        select_layout.addStretch()
        search_tab.addLayout(select_layout)

        search_widget = QGroupBox()
        search_widget.setLayout(search_tab)
        self.tabs.addTab(search_widget, "Search Existing")

        # ─────────────────────────────── Create Tab
        create_tab = QVBoxLayout()
        create_root = QGroupBox("Create New")
        create_root.setLayout(create_tab)

        self.new_entity_name = QLineEdit()
        self.new_entity_name.setPlaceholderText("Enter name...")
        create_tab.addWidget(self.new_entity_name)

        create_btn_layout = QHBoxLayout()
        self.create_btn = QPushButton("Create New")
        self.create_btn.clicked.connect(self._create_new_entity)

        create_btn_layout.addWidget(self.create_btn)
        create_btn_layout.addStretch()
        create_tab.addLayout(create_btn_layout)

        create_widget = QGroupBox()
        create_widget.setLayout(create_tab)
        self.tabs.addTab(create_widget, "Create New")

        # ─────────────────────────────── Action buttons
        btn_layout = QHBoxLayout()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)

        btn_layout.addWidget(cancel_btn)
        btn_layout.addStretch()

        layout.addLayout(btn_layout)

        self.results_list.itemSelectionChanged.connect(self._on_selection_changed)

        self._perform_search()
        self.search_edit.setFocus()

    # ─────────────────────────────────────────────── Search logic
    def _on_entity_type_changed(self, _):
        self._perform_search()

    def _on_search_changed(self, _):
        self.search_timer.start()

    def _perform_search(self):
        entity_type = self.entity_type_combo.currentText()
        search_text = self.search_edit.text().strip().lower()
        self.results_list.clear()

        try:
            name_attr, id_attr = ENTITY_FIELDS[entity_type]

            # Get all entities
            entities = self.controller.get.get_all_entities(entity_type)

            # Filter
            if search_text:
                entities = [
                    e for e in entities if search_text in getattr(e, name_attr).lower()
                ]

            # Determine display logic
            display_fn = DISPLAY_OVERRIDE.get(entity_type, default_display)

            # Populate list
            for entity in sorted(entities, key=lambda e: getattr(e, name_attr).lower()):
                display_name = display_fn(entity, name_attr, entity_type)
                entity_id = getattr(entity, id_attr)

                item = QTreeWidgetItem([display_name, entity_type, str(entity_id)])
                self.results_list.addTopLevelItem(item)

        except Exception as e:
            logger.error(f"Error searching {entity_type}: {e}")

    # ─────────────────────────────────────────────── Selection logic
    def _on_entity_selected(self, item: QTreeWidgetItem, _):
        entity_type = item.text(1)
        entity_id = int(item.text(2))
        relationship_type = self.relationship_combo.currentText().strip().lower()

        if not relationship_type:
            relationship_type = "recipient"  # Default if empty

        self.relationship_added.emit(entity_type, entity_id, relationship_type)
        self.accept()

    def _on_select_clicked(self):
        items = self.results_list.selectedItems()
        if items:
            self._on_entity_selected(items[0], 0)

    def _on_selection_changed(self):
        self.select_btn.setEnabled(bool(self.results_list.selectedItems()))

    # ─────────────────────────────────────────────── Create new entity
    def _create_new_entity(self):
        entity_name = self.new_entity_name.text().strip()
        if not entity_name:
            QMessageBox.warning(self, "Missing Name", "Please enter a name.")
            return

        entity_type = self.entity_type_combo.currentText()
        relationship_type = self.relationship_combo.currentText().strip().lower()

        if not relationship_type:
            relationship_type = "recipient"  # Default if empty

        try:
            name_attr, id_attr = ENTITY_FIELDS[entity_type]
            new_entity = self.controller.add.add_entity(
                entity_type, **{name_attr: entity_name}
            )
            entity_id = getattr(new_entity, id_attr)

            self.relationship_added.emit(entity_type, entity_id, relationship_type)
            self.accept()

        except Exception as e:
            logger.error(f"Error creating {entity_type}: {e}")
            QMessageBox.critical(self, "Error", f"Failed to create new entity:\n{e}")
