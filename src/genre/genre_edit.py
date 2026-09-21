from PySide6.QtWidgets import QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit, QMessageBox
from sqlalchemy.exc import SQLAlchemyError

from src.common.alias.entity_alias_tab import EntityAliasesTab
from src.common.widgets.hierarchy_tree_style import is_hierarchy_descendant
from src.foundation.logger_config import logger


def get_valid_parents(controller, genre=None):
    """Return valid parent Genre objects for `genre` (or all genres, if creating a new one)."""
    all_genres = controller.get.get_all_entities("Genre") or []

    if genre is None or not genre.genre_id:
        return sorted(all_genres, key=lambda g: g.genre_name.lower())

    # Exclude the genre itself and every descendant at any depth (not just
    # direct children) -- picking a descendant as the new parent would
    # create a cycle that nothing downstream guards against.
    invalid_ids = {genre.genre_id}
    for g in all_genres:
        if g.genre_id in invalid_ids:
            continue
        if is_hierarchy_descendant(genre.genre_id, g.genre_id, all_genres, id_attr="genre_id"):
            invalid_ids.add(g.genre_id)

    valid_parents = [g for g in all_genres if g.genre_id not in invalid_ids]
    valid_parents.sort(key=lambda g: g.genre_name.lower())
    return valid_parents


def find_duplicate_genre_name(controller, name: str, exclude_id: int | None = None):
    """Return the existing Genre with the same name (case-insensitive), if any."""
    target = name.strip().lower()
    for g in controller.get.get_all_entities("Genre") or []:
        if g.genre_id != exclude_id and g.genre_name.lower() == target:
            return g
    return None


class GenreEditDialog(QDialog):
    """Dialog to create a new genre or edit an existing one, including its parent and aliases."""

    def __init__(self, controller, genre=None, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.genre = genre
        self.parent_id = genre.parent_id if genre else None
        # Populated after a successful save so callers (e.g. the "New Parent"/
        # "New Child" context menu actions) can link the resulting genre
        # without re-querying the database.
        self.result_genre = None
        self.tab_aliases = None
        self.setup_ui()
        self.load_data()

    def setup_ui(self):
        """Build the name/description/parent form, plus an aliases tab when editing."""
        self.setWindowTitle("Edit Genre" if self.genre else "New Genre")

        layout = QFormLayout(self)
        self.name_input = QLineEdit()
        layout.addRow("Genre Name:", self.name_input)

        self.desc_input = QLineEdit()
        layout.addRow("Description:", self.desc_input)

        self.parent_combo = QComboBox()
        self.parent_combo.addItem("(No parent)", None)
        layout.addRow("Parent Genre:", self.parent_combo)

        # Aliases only make sense once the genre exists (they need a
        # genre_id to point at), so this section is edit-only.
        if self.genre:
            self.setMinimumSize(420, 420)
            layout.addRow(QLabel("Aliases:"))
            self.tab_aliases = EntityAliasesTab(self.controller, self.genre, "Genre", "genre_id", placeholder="e.g. Film Scores")
            layout.addRow(self.tab_aliases)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.validate)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def load_data(self):
        """Pre-fill fields for editing, then populate the parent combo either way."""
        if self.genre:
            self.name_input.setText(self.genre.genre_name)
            if self.genre.description:
                self.desc_input.setText(self.genre.description)
            if self.tab_aliases:
                self.tab_aliases.load(self.genre)

        # Clear existing items except the first "(No parent)" option
        for i in range(self.parent_combo.count() - 1, 0, -1):
            self.parent_combo.removeItem(i)

        try:
            valid_parents = get_valid_parents(self.controller, self.genre)
            for g in valid_parents:
                self.parent_combo.addItem(g.genre_name, g.genre_id)
        except SQLAlchemyError as e:
            logger.error(f"Error loading valid parents: {e!s}")
            QMessageBox.warning(self, "Error", "Could not load parent options")

        # Pre-select current parent, if editing
        current_idx = 0
        if self.genre and self.genre.parent_id:
            idx = self.parent_combo.findData(self.genre.parent_id)
            if idx >= 0:
                current_idx = idx
        self.parent_combo.setCurrentIndex(current_idx)

    def validate(self):
        """Validate the form and save the genre, rejecting empty or duplicate names."""
        name = self.name_input.text().strip()
        if not name:
            QMessageBox.warning(self, "Validation", "Genre name is required")
            return

        exclude_id = self.genre.genre_id if self.genre else None
        if find_duplicate_genre_name(self.controller, name, exclude_id=exclude_id):
            QMessageBox.warning(self, "Validation", "Genre name already exists")
            return

        parent_id = self.parent_combo.currentData()
        try:
            if self.genre:  # Editing
                self.controller.update.update_entity("Genre", self.genre.genre_id, genre_name=name, description=self.desc_input.text().strip() or None, parent_id=parent_id)
                self.result_genre = self.genre
            else:  # Creating
                self.result_genre = self.controller.add.add_entity("Genre", genre_name=name, description=self.desc_input.text().strip() or None, parent_id=parent_id)
            self.accept()
        except SQLAlchemyError as e:
            QMessageBox.critical(self, "Error", f"Failed to save genre: {e!s}")


class GenreSetParentDialog(QDialog):
    """Set the parent genre for one or more selected genres at once."""

    def __init__(self, controller, genres, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.genres = genres
        self.setup_ui()
        self.load_data()

    def setup_ui(self):
        """Build the summary label and parent combo for the selected genres."""
        self.setWindowTitle("Set Parent Genre")

        layout = QFormLayout(self)

        summary = f"Set parent for '{self.genres[0].genre_name}':" if len(self.genres) == 1 else f"Set parent for {len(self.genres)} selected genres:"
        layout.addRow(QLabel(summary))

        self.parent_combo = QComboBox()
        self.parent_combo.addItem("(No parent)", None)
        layout.addRow("Parent Genre:", self.parent_combo)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.validate)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def load_data(self):
        """Populate the parent combo, excluding the selected genres and their descendants."""
        all_genres = self.controller.get.get_all_entities("Genre")
        selected_ids = {g.genre_id for g in self.genres}

        # A valid parent must not be one of the selected genres themselves,
        # nor a descendant of any of them -- either case would create a cycle.
        invalid_ids = set(selected_ids)
        for genre_id in selected_ids:
            for g in all_genres:
                if g.genre_id in invalid_ids:
                    continue
                if is_hierarchy_descendant(genre_id, g.genre_id, all_genres, id_attr="genre_id"):
                    invalid_ids.add(g.genre_id)

        valid_parents = [g for g in all_genres if g.genre_id not in invalid_ids]
        valid_parents.sort(key=lambda g: g.genre_name.lower())

        for g in valid_parents:
            self.parent_combo.addItem(g.genre_name, g.genre_id)

        # Pre-select the current parent, but only if every selected genre
        # already shares that same parent.
        parent_ids = {g.parent_id for g in self.genres}
        if len(parent_ids) == 1:
            idx = self.parent_combo.findData(next(iter(parent_ids)))
            if idx >= 0:
                self.parent_combo.setCurrentIndex(idx)

    def validate(self):
        """Apply the chosen parent to every selected genre."""
        parent_id = self.parent_combo.currentData()
        try:
            for genre in self.genres:
                self.controller.update.update_entity("Genre", genre.genre_id, parent_id=parent_id)
            self.accept()
        except SQLAlchemyError as e:
            QMessageBox.critical(self, "Error", f"Failed to set parent: {e!s}")
