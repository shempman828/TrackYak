"""
playlist_smart_new.py

Dialog for creating a new smart playlist.
Lets the user enter a name, description, AND/OR logic, and one or more criteria rows.
"""

from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QFormLayout,
    QLineEdit,
    QTextEdit,
    QPushButton,
    QLabel,
    QHBoxLayout,
    QComboBox,
    QScrollArea,
    QWidget,
)

from src.playlist_smart_criteria_widget import CriteriaWidget


class SmartPlaylistCreateDialog(QDialog):
    """Dialog for creating a new smart playlist."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Create Smart Playlist")
        self.setMinimumWidth(750)
        self.setMinimumHeight(400)
        self.criteria_widgets = []
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)

        # --- Name and description ---
        form_layout = QFormLayout()

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("My Smart Playlist")
        form_layout.addRow("Playlist Name:", self.name_edit)

        self.desc_edit = QTextEdit()
        self.desc_edit.setMaximumHeight(60)
        self.desc_edit.setPlaceholderText("Optional description...")
        form_layout.addRow("Description:", self.desc_edit)

        layout.addLayout(form_layout)

        # --- AND / OR logic toggle ---
        logic_layout = QHBoxLayout()
        logic_label = QLabel("<b>Match</b>")
        self.logic_combo = QComboBox()
        self.logic_combo.addItem("ALL of the following conditions (AND)", "AND")
        self.logic_combo.addItem("ANY of the following conditions (OR)", "OR")
        logic_layout.addWidget(logic_label)
        logic_layout.addWidget(self.logic_combo)
        logic_layout.addStretch()
        layout.addLayout(logic_layout)

        # --- Criteria section ---
        layout.addWidget(QLabel("<b>Criteria:</b>"))

        # Scrollable area in case there are many criteria rows
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMaximumHeight(300)

        self.criteria_container_widget = QWidget()
        self.criteria_container = QVBoxLayout(self.criteria_container_widget)
        self.criteria_container.setSpacing(4)
        self.criteria_container.addStretch()  # pushes rows to the top

        scroll.setWidget(self.criteria_container_widget)
        layout.addWidget(scroll)

        # Add first criteria row
        self.add_criteria_widget()

        # Add Criteria button
        self.add_btn = QPushButton("+ Add Another Criteria")
        self.add_btn.clicked.connect(self.add_criteria_widget)
        layout.addWidget(self.add_btn)

        # --- Dialog buttons ---
        button_layout = QHBoxLayout()
        button_layout.addStretch()

        self.ok_btn = QPushButton("Create")
        self.ok_btn.setDefault(True)
        self.ok_btn.clicked.connect(self.accept)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self.reject)

        button_layout.addWidget(self.ok_btn)
        button_layout.addWidget(self.cancel_btn)
        layout.addLayout(button_layout)

    def add_criteria_widget(self):
        """Add a new blank criteria row."""
        widget = CriteriaWidget()
        widget.delete_requested.connect(self.remove_criteria_widget)

        # Insert before the stretch (last item)
        count = self.criteria_container.count()
        self.criteria_container.insertWidget(count - 1, widget)
        self.criteria_widgets.append(widget)

    def remove_criteria_widget(self, widget):
        """Remove a criteria row (but always keep at least one)."""
        if len(self.criteria_widgets) <= 1:
            return  # don't remove the last row
        if widget in self.criteria_widgets:
            self.criteria_widgets.remove(widget)
            widget.setParent(None)
            widget.deleteLater()

    def get_data(self):
        """
        Return (name, description, logic, criteria_list).

        criteria_list is a list of dicts like:
            [{"field": "user_rating", "comparison": "gt", "value": 5.5, "type": "Float"}, ...]
        logic is "AND" or "OR".
        """
        name = self.name_edit.text().strip()
        description = self.desc_edit.toPlainText().strip()
        logic = self.logic_combo.currentData()
        criteria_list = [w.get_criteria() for w in self.criteria_widgets]
        return name, description, logic, criteria_list
