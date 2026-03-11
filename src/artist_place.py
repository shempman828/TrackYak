from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QVBoxLayout,
)

from src.logger_config import logger


# -------------------------
# Place Selection Dialog
# -------------------------
class PlaceSelectionDialog(QDialog):
    """Dialog for selecting a place to associate with an artist."""

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.selected_place_id = None
        self.selected_association_type = "associated"
        self._init_ui()

    def _init_ui(self):
        self.setWindowTitle("Add Place Association")
        self.setMinimumWidth(400)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Select a place:"))

        self.place_list = QListWidget()
        try:
            places = self.controller.get.get_all_entities("Place")
            for place in sorted(places, key=lambda p: p.place_name.lower()):
                item = QListWidgetItem(place.place_name)
                item.setData(Qt.UserRole, place.place_id)
                self.place_list.addItem(item)
        except Exception as e:
            logger.error(f"Error loading places: {e}")

        layout.addWidget(self.place_list)

        layout.addWidget(QLabel("Association type:"))
        self.type_combo = QComboBox()
        self.type_combo.addItems(["associated", "birth", "death", "residence"])
        layout.addWidget(self.type_combo)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_accept(self):
        item = self.place_list.currentItem()
        if not item:
            QMessageBox.warning(self, "No Selection", "Please select a place.")
            return
        self.selected_place_id = item.data(Qt.UserRole)
        self.selected_association_type = self.type_combo.currentText()
        self.accept()
