from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLineEdit,
    QMessageBox,
)

from src.logger_config import logger


def get_valid_parents(controller, genre):
    """
    Return a list of valid parent Genre objects for a given genre,
    excluding itself, its parent (if any), and its children.
    """
    # Get all genres first
    all_genres = controller.get.get_all_entities("Genre")

    # Get all children of the current genre (to exclude from valid parents)
    children_ids = set()
    if genre.genre_id:
        # Get direct children
        direct_children = controller.get.get_all_entities(
            "Genre", parent_id=genre.genre_id
        )
        children_ids.update(child.genre_id for child in direct_children)

        # For a more thorough exclusion, you could recursively get all descendants
        # but direct children should be sufficient for most cases

    # Filter out invalid parents
    valid_parents = []
    for g in all_genres:
        # Exclude the genre itself
        if g.genre_id == genre.genre_id:
            continue

        # Exclude any children of the current genre
        if g.genre_id in children_ids:
            continue

        valid_parents.append(g)

    valid_parents.sort(key=lambda g: g.genre_name.lower())

    logger.debug(
        f"Manual filtering: Found {len(valid_parents)} valid parents for genre {genre.genre_name}"
    )
    for parent in valid_parents:
        logger.debug(f"  - {parent.genre_name} (ID: {parent.genre_id})")

    return valid_parents


class GenreEditDialog(QDialog):
    def __init__(self, controller, genre=None, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.genre = genre
        self.parent_id = genre.parent_id if genre else None
        self.setup_ui()
        self.load_data()

    def setup_ui(self):
        self.setWindowTitle("Edit Genre" if self.genre else "New Genre")

        layout = QFormLayout(self)
        self.name_input = QLineEdit()
        layout.addRow("Genre Name:", self.name_input)

        self.desc_input = QLineEdit()
        layout.addRow("Description:", self.desc_input)

        self.parent_combo = QComboBox()
        self.parent_combo.addItem("(No parent)", None)
        layout.addRow("Parent Genre:", self.parent_combo)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.validate)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def load_data(self):
        if self.genre:
            # Pre-fill fields
            self.name_input.setText(self.genre.genre_name)
            if self.genre.description:
                self.desc_input.setText(self.genre.description)

            # Clear existing items except the first "(No parent)" option

            for i in range(self.parent_combo.count() - 1, 0, -1):
                self.parent_combo.removeItem(i)

            # Populate parent combo using helper
            try:
                logger.debug(
                    f"Loading valid parents for genre: {self.genre.genre_name} (ID: {self.genre.genre_id})"
                )
                valid_parents = get_valid_parents(self.controller, self.genre)
                logger.debug(f"Adding {len(valid_parents)} items to combo box")

                for g in valid_parents:
                    self.parent_combo.addItem(g.genre_name, g.genre_id)
                    logger.debug(f"Added to combo: {g.genre_name} (ID: {g.genre_id})")

            except Exception as e:
                logger.error(f"Error loading valid parents: {str(e)}")
                QMessageBox.warning(self, "Error", "Could not load parent options")

            # Pre-select current parent
            current_idx = 0
            if self.genre.parent_id:
                idx = self.parent_combo.findData(self.genre.parent_id)
                if idx >= 0:
                    current_idx = idx
                    logger.debug(f"Found current parent at index: {idx}")
                else:
                    logger.debug(
                        f"Current parent ID {self.genre.parent_id} not found in valid parents"
                    )

            self.parent_combo.setCurrentIndex(current_idx)
            logger.debug(f"Final combo box count: {self.parent_combo.count()}")

    def validate(self):
        name = self.name_input.text().strip()
        if not name:
            QMessageBox.warning(self, "Validation", "Genre name is required")
            return

        parent_id = self.parent_combo.currentData()
        try:
            if self.genre:  # Editing
                self.controller.update.update_entity(
                    "Genre",
                    self.genre.genre_id,
                    genre_name=name,
                    description=self.desc_input.text().strip() or None,
                    parent_id=parent_id,
                )
            else:  # Creating
                self.controller.add.add_entity(
                    "Genre",
                    genre_name=name,
                    description=self.desc_input.text().strip() or None,
                    parent_id=parent_id,
                )
            self.accept()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to save genre: {str(e)}")
