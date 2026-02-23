from PySide6.QtWidgets import (
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
from sqlalchemy.orm import object_mapper

from db_helpers import SplitDB


class SplitDBDialog(QDialog):
    """Dialog for splitting a combined ORM entity (e.g., 'Paul Simon / Art Garfunkel')."""

    def __init__(self, split_helper: SplitDB, model_name: str, entity_obj, parent=None):
        super().__init__(parent)
        self.split_helper = split_helper
        self.model_name = model_name
        self.entity_obj = entity_obj
        self.setWindowTitle(f"Split {model_name}")
        self.setMinimumWidth(400)
        self.setMinimumHeight(500)

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
        layout.addWidget(self.split_list)

        # Add first two empty rows by default
        for _ in range(2):
            self._add_split_row("")

        # Add/Remove buttons
        btn_row = QHBoxLayout()
        add_btn = QPushButton("Add")
        remove_btn = QPushButton("Remove")
        btn_row.addWidget(add_btn)
        btn_row.addWidget(remove_btn)
        layout.addLayout(btn_row)

        add_btn.clicked.connect(lambda: self._add_split_row(""))
        remove_btn.clicked.connect(self._remove_selected_row)

        # Warning label
        self.warning_label = QLabel("")
        self.warning_label.setWordWrap(True)
        layout.addWidget(self.warning_label)

        # Split/Cancel buttons
        btn_layout = QHBoxLayout()
        self.split_btn = QPushButton("Split")
        cancel_btn = QPushButton("Cancel")
        btn_layout.addStretch()
        btn_layout.addWidget(self.split_btn)
        btn_layout.addWidget(cancel_btn)
        layout.addLayout(btn_layout)

        self.split_btn.clicked.connect(self._on_split)
        cancel_btn.clicked.connect(self.reject)

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
                        except:  # noqa: E722
                            pass
                        relationship_info.append(f"• {rel_key}: {related_name}")

            if relationship_info:
                self.relationship_info.setText("\n".join(relationship_info))
            else:
                self.relationship_info.setText("No relationships found")

        except Exception as e:
            self.relationship_info.setText(f"Could not load relationship info: {e}")

    def _find_name_attribute_for_obj(self, obj) -> str:
        """Find name attribute for a related object."""
        name_attrs_to_try = ["name", "title"]
        for name_attr in name_attrs_to_try:
            if hasattr(obj, name_attr):
                return name_attr
        return None

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
        self.split_list.addItem(item)
        self.split_list.setItemWidget(item, widget)
        item.setSizeHint(widget.sizeHint())

    def _remove_selected_row(self):
        """Remove selected name row."""
        row = self.split_list.currentRow()
        if row >= 0:
            self.split_list.takeItem(row)
        self._validate_input()

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
            QMessageBox.warning(
                self, "No Names", "Please enter at least one new entity name."
            )
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

            # Show success message
            QMessageBox.information(
                self,
                "Split Successful",
                f"Successfully split into {len(names)} new entities.",
            )

            # Accept the dialog to close it
            self.accept()

        except Exception as e:
            QMessageBox.critical(
                self,
                "Split Failed",
                f"An error occurred during the split:\n{str(e)}",
            )
