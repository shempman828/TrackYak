"""
playlist_smart_base_dialog.py

Shared UI scaffold for the smart-playlist create/edit dialogs: name/description
fields, the AND/OR logic combo, and the scrollable criteria-row section. The
create and edit dialogs differ in their persistence contract (create returns
raw form data for the caller to save; edit saves directly to the database), so
that part stays in each subclass — only the widget construction and
criteria-row bookkeeping are shared here.
"""

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from src.core.logger_config import logger
from src.playlist.playlist_smart_criteria_widget import CriteriaWidget


class BaseSmartPlaylistDialog(QDialog):
    """Shared name/description/logic/criteria scaffold for smart playlist dialogs."""

    def __init__(self, title: str, ok_button_text: str, parent=None):
        super().__init__(parent)
        self.criteria_widgets = []
        self.setWindowTitle(title)
        self.setMinimumWidth(750)
        self.setMinimumHeight(400)
        self._ok_button_text = ok_button_text
        self.init_ui()

    # ------------------------------------------------------------------
    # UI setup
    # ------------------------------------------------------------------

    def name_placeholder(self) -> str:
        """Subclasses override for a dialog-specific placeholder."""
        return "Playlist name"

    def init_ui(self):
        layout = QVBoxLayout(self)

        # --- Name and description ---
        form_layout = QFormLayout()

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText(self.name_placeholder())
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

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMaximumHeight(300)

        self.criteria_container_widget = QWidget()
        self.criteria_container = QVBoxLayout(self.criteria_container_widget)
        self.criteria_container.setSpacing(4)
        self.criteria_container.addStretch()  # keeps rows pinned to the top

        scroll.setWidget(self.criteria_container_widget)
        layout.addWidget(scroll)

        # Add Criteria button
        self.add_btn = QPushButton("+ Add Another Criteria")
        self.add_btn.clicked.connect(self.add_criteria_widget)
        layout.addWidget(self.add_btn)

        # --- Dialog buttons ---
        button_layout = QHBoxLayout()
        button_layout.addStretch()

        self.ok_btn = QPushButton(self._ok_button_text)
        self.ok_btn.setDefault(True)
        self.ok_btn.clicked.connect(self._on_ok_clicked)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self.reject)

        button_layout.addWidget(self.ok_btn)
        button_layout.addWidget(self.cancel_btn)
        layout.addLayout(button_layout)

    # ------------------------------------------------------------------
    # Criteria row management
    # ------------------------------------------------------------------

    def add_criteria_widget(self, criteria_dict: dict = None):
        """
        Add a criteria row to the dialog.

        If criteria_dict is given, the row is pre-filled with those values.
        Otherwise a blank row is added.
        """
        widget = CriteriaWidget()
        widget.delete_requested.connect(self.remove_criteria_widget)

        # Insert before the stretch (last item in the layout)
        count = self.criteria_container.count()
        self.criteria_container.insertWidget(count - 1, widget)
        self.criteria_widgets.append(widget)

        if criteria_dict:
            widget.set_criteria(criteria_dict)

        logger.debug(f"Added smart playlist criteria row (total={len(self.criteria_widgets)})")

    def remove_criteria_widget(self, widget):
        """Remove a criteria row, but always keep at least one."""
        if len(self.criteria_widgets) <= 1:
            return
        if widget in self.criteria_widgets:
            self.criteria_widgets.remove(widget)
            widget.setParent(None)
            widget.deleteLater()
            logger.debug(
                f"Removed smart playlist criteria row (total={len(self.criteria_widgets)})"
            )

    # ------------------------------------------------------------------
    # Form data
    # ------------------------------------------------------------------

    def _collect_form_data(self):
        """Return (name, description, logic, criteria_list) from the current form."""
        name = self.name_edit.text().strip()
        description = self.desc_edit.toPlainText().strip()
        logic = self.logic_combo.currentData()  # "AND" or "OR"
        criteria_list = [w.get_criteria() for w in self.criteria_widgets]
        return name, description, logic, criteria_list

    def _on_ok_clicked(self):
        """Subclasses override to validate/save/accept."""
        raise NotImplementedError
