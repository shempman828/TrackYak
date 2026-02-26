from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QMessageBox,
    QVBoxLayout,
)

from logger_config import logger


def get_valid_parent_roles(controller, role):
    """
    Return a list of valid parent Role objects for a given role,
    excluding itself, its parent (if any), and its children.
    """
    # Get all roles first
    all_roles = controller.get.get_all_entities("Role")

    # Get all children of the current role (to exclude from valid parents)
    children_ids = set()
    if role.role_id:
        # Get direct children
        direct_children = controller.get.get_all_entities(
            "Role", parent_id=role.role_id
        )
        children_ids.update(child.role_id for child in direct_children)

    # Filter out invalid parents
    valid_parents = []
    for r in all_roles:
        # Exclude the role itself
        if r.role_id == role.role_id:
            continue

        # Exclude the current parent (if any)
        if role.parent_id and r.role_id == role.parent_id:
            continue

        # Exclude any children of the current role
        if r.role_id in children_ids:
            continue

        valid_parents.append(r)

    logger.debug(
        f"Manual filtering: Found {len(valid_parents)} valid parents for role {role.role_name}"
    )
    for parent in valid_parents:
        logger.debug(f"  - {parent.role_name} (ID: {parent.role_id})")

    return valid_parents


class RoleParentSelectDialog(QDialog):
    def __init__(self, controller, role, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.role = role
        self.selected_parent_id = None
        self.setup_ui()

    def setup_ui(self):
        self.setWindowTitle("Select Parent Role")
        layout = QVBoxLayout(self)

        self.parent_combo = QComboBox()
        self.parent_combo.addItem("(No parent)", None)

        # Populate using helper
        try:
            valid_parents = get_valid_parent_roles(self.controller, self.role)
            for r in valid_parents:
                self.parent_combo.addItem(r.role_name, r.role_id)
                logger.debug(f"Added combo item: {r.role_name} with data: {r.role_id}")
        except Exception as e:
            logger.error(f"Error loading valid parents: {str(e)}")
            QMessageBox.warning(self, "Error", "Could not load parent options")

        # Pre-select current parent
        if self.role.parent_id:
            idx = self.parent_combo.findData(self.role.parent_id)
            if idx >= 0:
                self.parent_combo.setCurrentIndex(idx)
            else:
                # If current parent is not valid, select "No parent"
                self.parent_combo.setCurrentIndex(0)
        else:
            self.parent_combo.setCurrentIndex(0)

        layout.addWidget(self.parent_combo)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def accept(self):
        # Get the current data from the selected item
        index = self.parent_combo.currentIndex()
        self.selected_parent_id = self.parent_combo.itemData(index)

        logger.debug(f"Selected parent ID: {self.selected_parent_id} (index: {index})")
        logger.debug(f"Current text: {self.parent_combo.currentText()}")

        super().accept()
