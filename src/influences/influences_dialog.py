from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton, QTextEdit, QVBoxLayout, QWidget
from sqlalchemy.exc import SQLAlchemyError

from src.common.widgets.entity_completer_edit import EntityCompleterEdit
from src.foundation.logger_config import logger
from src.foundation.status_utility import show_status_message

_INFLUENCE_ROLE = Qt.UserRole


class InfluenceAddError(Exception):
    """A database write for a new influence or artist failed."""


def _repolish(widget):
    """Re-apply stylesheet rules keyed on a dynamic property that just changed."""
    widget.style().unpolish(widget)
    widget.style().polish(widget)


class _ArtistField(QWidget):
    """Artist name field with autocomplete and an "Existing artist" / "Will create new" chip."""

    def __init__(self, placeholder, known_names_lower, parent=None):
        super().__init__(parent)
        self._known_names_lower = known_names_lower

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.field = EntityCompleterEdit(placeholder, self, allow_create_new=True)
        self.field.textEdited.connect(self._refresh_chip)
        self.field.picked.connect(self._refresh_chip)
        layout.addWidget(self.field, 1)

        self.chip = QLabel()
        self.chip.setObjectName("InfluenceMatchChip")
        layout.addWidget(self.chip)

        self._refresh_chip()

    def set_index(self, display_to_id):
        """Set the completer's {name: artist_id} index."""
        self.field.set_index(display_to_id)

    def note_known_name(self, name, artist_id):
        """Register a newly created artist so the chip and later lookups treat it as existing."""
        self._known_names_lower.add(name.strip().lower())
        self.field.add_to_index(name, artist_id)

    def text(self):
        """Return the trimmed field text."""
        return self.field.text().strip()

    def matched_id(self):
        """Return the artist_id of a completer pick, or None."""
        return self.field.matched_id()

    def swap_text_with(self, other):
        """Swap the typed names with another field and refresh both chips."""
        mine, theirs = self.text(), other.text()
        self.field.reset()
        other.field.reset()
        self.field.setText(theirs)
        other.field.setText(mine)
        self._refresh_chip()
        other._refresh_chip()

    def _refresh_chip(self, *_args):
        """Update the chip for the current text: empty, existing, or new."""
        text = self.text()
        if not text:
            state, label = "empty", ""
        elif self.field.matched_id() is not None or text.lower() in self._known_names_lower:
            state, label = "existing", "Existing artist"
        else:
            state, label = "new", "Will create new"
        self.chip.setText(label)
        self.chip.setProperty("state", state)
        self.chip.setVisible(bool(label))
        _repolish(self.chip)


class AddInfluenceDialog(QDialog):
    """Add an influence relationship, creating either artist if the name is new."""

    def __init__(self, controller, all_artists, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.all_artists = list(all_artists)  # List of (artist_id, artist_name)
        # First artist wins when two share a name; a completer pick is more precise.
        self._id_by_lower_name = {}
        for artist_id, name in self.all_artists:
            self._id_by_lower_name.setdefault(name.strip().lower(), artist_id)
        self.created_artists = []
        self.added_influence = None  # ((influencer_id, name), (influenced_id, name)) once saved
        self.setWindowTitle("Add Influence Relationship")
        self.setModal(True)
        self.init_ui()

    def init_ui(self):
        """Build the two artist fields, swap button, description, and buttons."""
        layout = QVBoxLayout(self)

        index = {name: artist_id for artist_id, name in self.all_artists}
        known_names_lower = set(self._id_by_lower_name)

        layout.addWidget(self._section_label("INFLUENCER"))
        self.influencer_field = _ArtistField("Search or type new artist name…", known_names_lower, self)
        self.influencer_field.set_index(index)
        layout.addWidget(self.influencer_field)

        swap_row = QHBoxLayout()
        swap_row.addStretch()
        self.swap_button = QPushButton("⇅")
        self.swap_button.setObjectName("InfluenceSwapButton")
        self.swap_button.setToolTip("Swap influencer and influenced")
        self.swap_button.setAccessibleName("Swap influencer and influenced")
        self.swap_button.setCursor(Qt.PointingHandCursor)
        self.swap_button.clicked.connect(self._swap_fields)
        swap_row.addWidget(self.swap_button)
        swap_row.addStretch()
        layout.addLayout(swap_row)

        layout.addWidget(self._section_label("INFLUENCED"))
        self.influenced_field = _ArtistField("Search or type new artist name…", known_names_lower, self)
        self.influenced_field.set_index(index)
        layout.addWidget(self.influenced_field)

        layout.addWidget(QLabel("Description (optional):"))
        self.description_edit = QTextEdit()
        self.description_edit.setMaximumHeight(80)
        layout.addWidget(self.description_edit)

        button_layout = QHBoxLayout()
        button_layout.addStretch()
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self.reject)
        button_layout.addWidget(self.cancel_button)
        self.add_button = QPushButton("Add Influence")
        self.add_button.setObjectName("PrimaryButton")
        self.add_button.clicked.connect(self.add_influence)
        button_layout.addWidget(self.add_button)
        layout.addLayout(button_layout)

        self.resize(480, 420)

    @staticmethod
    def _section_label(text):
        label = QLabel(text)
        label.setProperty("influenceSection", "true")
        return label

    def _swap_fields(self):
        self.influencer_field.swap_text_with(self.influenced_field)

    def _existing_id(self, field):
        """Return the artist_id `field` names (completer pick, then name lookup), or None."""
        matched_id = field.matched_id()
        if matched_id is not None:
            return matched_id
        return self._id_by_lower_name.get(field.text().lower())

    def _create_artist(self, name):
        """Create an artist and register it with both fields; raise InfluenceAddError on failure."""
        new_artist = self.controller.add.add_entity("Artist", artist_name=name)
        if new_artist is None:
            raise InfluenceAddError(f"Could not create the new artist '{name}'.")
        new_artist_id = new_artist.artist_id
        self.all_artists.append((new_artist_id, name))
        self._id_by_lower_name[name.lower()] = new_artist_id
        self.created_artists.append((new_artist_id, name))
        self.influencer_field.note_known_name(name, new_artist_id)
        self.influenced_field.note_known_name(name, new_artist_id)
        return new_artist_id

    def _influence_exists(self, influencer_id, influenced_id):
        """Return True when this exact relationship is already stored."""
        existing = self.controller.get.get_entity_object("ArtistInfluence", influencer_id=influencer_id, influenced_id=influenced_id)
        return existing is not None

    def add_influence(self):
        """Validate both names, create any new artists, then save the relationship."""
        influencer_name = self.influencer_field.text()
        influenced_name = self.influenced_field.text()
        description = self.description_edit.toPlainText().strip()

        if not influencer_name or not influenced_name:
            show_status_message(self, "Please enter both influencer and influenced artist names!")
            return

        influencer_id = self._existing_id(self.influencer_field)
        influenced_id = self._existing_id(self.influenced_field)
        # Compare IDs when both exist (two artists can share a name); else compare names.
        both_exist = influencer_id is not None and influenced_id is not None
        is_self = influencer_id == influenced_id if both_exist else influencer_name.lower() == influenced_name.lower()
        if is_self:
            show_status_message(self, "An artist cannot influence themselves!")
            return

        created_before = len(self.created_artists)
        try:
            if influencer_id is not None and influenced_id is not None and self._influence_exists(influencer_id, influenced_id):
                show_status_message(self, f"{influencer_name} → {influenced_name} already exists.")
                return

            # Create new artists only after every check passes.
            if influencer_id is None:
                influencer_id = self._create_artist(influencer_name)
            if influenced_id is None:
                influenced_id = self._create_artist(influenced_name)

            saved = self.controller.add.add_entity("ArtistInfluence", influencer_id=influencer_id, influenced_id=influenced_id, description=description or None)
            if saved is None:
                raise InfluenceAddError("Could not save the influence relationship.")
        except (InfluenceAddError, SQLAlchemyError) as e:
            orphans = self.created_artists[created_before:]
            if orphans:
                logger.warning(f"Influence save failed; artists created without a relationship: {orphans}")
            logger.exception("Failed to add influence")
            QMessageBox.critical(self, "Error", f"Failed to add influence: {e!s}")
            return

        self.added_influence = ((influencer_id, influencer_name), (influenced_id, influenced_name))
        self.accept()

    def get_created_artists(self):
        """Return list of newly created artists (artist_id, artist_name)"""
        return self.created_artists


class _InfluenceRow(QFrame):
    """Card row for RemoveInfluenceDialog: "A → B" plus the optional description."""

    def __init__(self, influence, parent=None):
        super().__init__(parent)
        self.setObjectName("InfluenceRelRow")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(6)

        layout.addWidget(QLabel(influence["influencer_name"]))
        arrow = QLabel("→")
        arrow.setObjectName("InfluenceRelArrow")
        layout.addWidget(arrow)
        layout.addWidget(QLabel(influence["influenced_name"]))
        layout.addStretch()

        description = influence.get("description")
        if description:
            desc_label = QLabel(description)
            desc_label.setProperty("textRole", "muted")
            desc_label.setWordWrap(True)
            layout.addWidget(desc_label, 1)

    def set_selected(self, selected):
        """Toggle the row's selected style."""
        self.setProperty("selected", "true" if selected else "false")
        _repolish(self)


class RemoveInfluenceDialog(QDialog):
    """Pick an existing influence relationship from a searchable list and delete it."""

    def __init__(self, controller, all_influences, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.all_influences = all_influences
        self.selected_influence = None
        self.removed_influence = None  # the influence dict once deleted
        self._selected_row = None
        self.setWindowTitle("Remove Influence Relationship")
        self.setModal(True)
        self.init_ui()

    def init_ui(self):
        """Build the search box, result list, selection label, and buttons."""
        layout = QVBoxLayout(self)

        layout.addWidget(QLabel("Search Influence Relationships:"))
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("Search by artist name...")
        self.search_box.setClearButtonEnabled(True)
        self.search_box.textChanged.connect(self.filter_influences)
        layout.addWidget(self.search_box)

        self.search_status = QLabel()
        self.search_status.setProperty("textRole", "muted")
        layout.addWidget(self.search_status)

        layout.addWidget(QLabel("Select Relationship to Remove:"))
        self.results_list = QListWidget()
        # currentItemChanged also fires for arrow-key navigation, not only clicks.
        self.results_list.currentItemChanged.connect(self.on_item_selected)
        layout.addWidget(self.results_list)

        self.selected_display = QLabel("No relationship selected")
        layout.addWidget(self.selected_display)

        button_layout = QHBoxLayout()
        button_layout.addStretch()
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self.reject)
        button_layout.addWidget(self.cancel_button)

        self.remove_button = QPushButton("Remove Influence")
        self.remove_button.setProperty("danger", "true")
        self.remove_button.clicked.connect(self.remove_influence)
        self.remove_button.setEnabled(False)
        button_layout.addWidget(self.remove_button)
        layout.addLayout(button_layout)

        self.resize(520, 440)

        self.filter_influences("")

    def filter_influences(self, text):
        """Show the relationships whose artist names contain `text` (all when blank)."""
        # Clear selection state first: clear() fires currentItemChanged with None.
        self._selected_row = None
        self.results_list.clear()

        search_lower = text.strip().lower()
        if not search_lower:
            influences_to_show = self.all_influences
            self.search_status.setText(f"Showing all {len(influences_to_show)} relationships")
        else:
            influences_to_show = [inf for inf in self.all_influences if search_lower in inf["influencer_name"].lower() or search_lower in inf["influenced_name"].lower()]
            self.search_status.setText(f"Found {len(influences_to_show)} relationships")

        for inf in influences_to_show:
            item = QListWidgetItem()
            item.setData(_INFLUENCE_ROLE, inf)
            item.setData(Qt.AccessibleTextRole, f"{inf['influencer_name']} influenced {inf['influenced_name']}")
            row = _InfluenceRow(inf)
            item.setSizeHint(row.sizeHint())
            self.results_list.addItem(item)
            self.results_list.setItemWidget(item, row)

        self._clear_selection()

    def _clear_selection(self):
        self.remove_button.setEnabled(False)
        self.selected_influence = None
        self.selected_display.setText("No relationship selected")

    def on_item_selected(self, item, _previous=None):
        """Track the current row (click or keyboard) and enable Remove."""
        if self._selected_row is not None:
            self._selected_row.set_selected(False)
            self._selected_row = None
        if item is None:
            self._clear_selection()
            return

        influence_data = item.data(_INFLUENCE_ROLE)
        self.selected_influence = influence_data
        row = self.results_list.itemWidget(item)
        if row is not None:
            row.set_selected(True)
            self._selected_row = row

        self.selected_display.setText(f"Selected: {influence_data['influencer_name']} → {influence_data['influenced_name']}")
        self.remove_button.setEnabled(True)

    def remove_influence(self):
        """Confirm, then delete the selected relationship and close on success."""
        if not self.selected_influence:
            show_status_message(self, "Please select a relationship to remove!")
            return

        influence = self.selected_influence
        arrow_text = f"{influence['influencer_name']} → {influence['influenced_name']}"
        reply = QMessageBox.question(self, "Confirm Removal", f"Remove this influence relationship?\n\n{arrow_text}", QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply != QMessageBox.Yes:
            return

        try:
            success = self.controller.delete.delete_entity("ArtistInfluence", influencer_id=influence["influencer_id"], influenced_id=influence["influenced_id"])
        except SQLAlchemyError as e:
            logger.error(f"Error removing influence: {e}")
            QMessageBox.critical(self, "Error", f"Failed to remove: {e!s}")
            return

        if not success:
            QMessageBox.critical(self, "Error", "Failed to remove relationship")
            return
        self.removed_influence = influence
        self.accept()
