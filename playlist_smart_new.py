"""
playlist_smart_new.py - Minimal dialog using CriteriaWidget
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
)

from playlist_smart_criteria_widget import CriteriaWidget


class SmartPlaylistCreateDialog(QDialog):
    """Minimal dialog for creating smart playlists."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Create Smart Playlist")
        self.setMinimumWidth(700)
        self.criteria_widgets = []
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)

        # Name and description
        form_layout = QFormLayout()

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("My Smart Playlist")
        form_layout.addRow("Playlist Name:", self.name_edit)

        self.desc_edit = QTextEdit()
        self.desc_edit.setMaximumHeight(60)
        self.desc_edit.setPlaceholderText("Optional description...")
        form_layout.addRow("Description:", self.desc_edit)

        layout.addLayout(form_layout)

        # Criteria section
        layout.addWidget(QLabel("<b>Criteria:</b>"))

        # Container for criteria widgets
        self.criteria_container = QVBoxLayout()
        layout.addLayout(self.criteria_container)

        # Add first criteria widget
        self.add_criteria_widget()

        # Add Criteria button
        self.add_btn = QPushButton("+ Add Another Criteria")
        self.add_btn.clicked.connect(self.add_criteria_widget)
        layout.addWidget(self.add_btn)

        # Dialog buttons
        button_layout = QHBoxLayout()
        button_layout.addStretch()

        self.ok_btn = QPushButton("Create")
        self.ok_btn.clicked.connect(self.accept)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self.reject)

        button_layout.addWidget(self.ok_btn)
        button_layout.addWidget(self.cancel_btn)

        layout.addLayout(button_layout)

    def add_criteria_widget(self):
        """Add a new criteria widget."""
        criteria_widget = CriteriaWidget()
        criteria_widget.delete_requested.connect(self.remove_criteria_widget)

        self.criteria_container.addWidget(criteria_widget)
        self.criteria_widgets.append(criteria_widget)

    def remove_criteria_widget(self, widget):
        """Remove a criteria widget."""
        if widget in self.criteria_widgets:
            self.criteria_widgets.remove(widget)
            widget.setParent(None)
            widget.deleteLater()

    def get_data(self):
        """Get the entered data."""
        name = self.name_edit.text().strip()
        description = self.desc_edit.toPlainText().strip()

        # Collect all criteria
        criteria_list = []
        for widget in self.criteria_widgets:
            criteria = widget.get_criteria()
            criteria_list.append(criteria)

        # Simple representation - we'll need to convert this to something
        # our controller can use to query tracks
        return name, description, criteria_list
