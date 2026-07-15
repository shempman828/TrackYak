from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QVBoxLayout,
)


class SearchResultsDialog(QDialog):
    """Dialog to display multiple search results and let the user choose one."""

    def __init__(self, results, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Select Location")
        self.results = results
        self.selected_result = None
        self.init_ui()
        self.adjust_size_to_contents()

    def init_ui(self):
        layout = QVBoxLayout(self)

        # List widget to display results
        self.tree_widget = QListWidget()
        for result in self.results:
            item = QListWidgetItem(
                f"{result.address} (Lat: {result.latitude}, Lon: {result.longitude})"
            )
            item.setData(Qt.UserRole, result)  # Store the full result object
            self.tree_widget.addItem(item)
        layout.addWidget(self.tree_widget)

        # Buttons
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept_selection)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def accept_selection(self):
        selected_item = self.tree_widget.currentItem()
        if selected_item:
            self.selected_result = selected_item.data(Qt.UserRole)
            self.accept()
        else:
            QMessageBox.warning(self, "No Selection", "Please select a location.")

    def get_selected_result(self):
        return self.selected_result

    def adjust_size_to_contents(self):
        """Resize the dialog and list to fit the contents."""
        # Resize the list widget height to fit all items
        total_height = (
            self.tree_widget.sizeHintForRow(0) * self.tree_widget.count()
            + 2 * self.tree_widget.frameWidth()
        )
        self.tree_widget.setMinimumHeight(total_height)

        # Optionally, resize width to fit longest item
        max_width = max(
            self.tree_widget.sizeHintForColumn(0), 300
        )  # 300 as a minimum width
        self.tree_widget.setMinimumWidth(max_width)

        # Let the dialog adjust
        self.adjustSize()
