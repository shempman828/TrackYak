from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton, QTextEdit, QVBoxLayout, QWidget
from sqlalchemy.exc import SQLAlchemyError

from src.common.widgets.entity_completer_edit import EntityCompleterEdit
from src.foundation.logger_config import logger
from src.foundation.status_utility import show_status_message


def _repolish(widget):
    """Force Qt to re-evaluate a stylesheet rule keyed on a dynamic
    property after that property changes on an already-shown widget."""
    widget.style().unpolish(widget)
    widget.style().polish(widget)


class _ArtistField(QWidget):
    """One "who" side of an influence relationship: a name field with
    autocomplete/create-new (EntityCompleterEdit) plus a small chip
    reporting whether the typed name currently resolves to an existing
    artist, would create a new one, or is still empty. Replaces the old
    plain-text status label ("✓ Using existing artist...") with a real
    dropdown of matches and an at-a-glance chip, instead of asking the
    user to read a sentence to find out what will happen.
    """

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
        self.field.set_index(display_to_id)

    def note_known_name(self, name, artist_id):
        """Register a name the dialog now considers resolvable (either
        freshly created, or swapped in from the other field), so the chip
        and later resolution both see it as existing."""
        self._known_names_lower.add(name.strip().lower())
        self.field.add_to_index(name, artist_id)

    def text(self):
        return self.field.text().strip()

    def matched_id(self):
        return self.field.matched_id()

    def swap_text_with(self, other):
        mine, theirs = self.text(), other.text()
        self.field.reset()
        other.field.reset()
        self.field.setText(theirs)
        other.field.setText(mine)
        self._refresh_chip()
        other._refresh_chip()

    def _refresh_chip(self, *_args):
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
    def __init__(self, controller, all_artists, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.all_artists = list(all_artists)  # List of (artist_id, artist_name)
        self.created_artists = []
        self.setWindowTitle("Add Influence Relationship")
        self.setModal(True)
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)

        index = {name: artist_id for artist_id, name in self.all_artists}
        known_names_lower = {name.strip().lower() for _artist_id, name in self.all_artists}

        layout.addWidget(self._section_label("INFLUENCER"))
        self.influencer_field = _ArtistField(
            "Search or type new artist name…", known_names_lower, self
        )
        self.influencer_field.set_index(index)
        layout.addWidget(self.influencer_field)

        swap_row = QHBoxLayout()
        swap_row.addStretch()
        self.swap_button = QPushButton("⇅")
        self.swap_button.setObjectName("InfluenceSwapButton")
        self.swap_button.setToolTip("Swap influencer and influenced")
        self.swap_button.setCursor(Qt.PointingHandCursor)
        self.swap_button.clicked.connect(self._swap_fields)
        swap_row.addWidget(self.swap_button)
        swap_row.addStretch()
        layout.addLayout(swap_row)

        layout.addWidget(self._section_label("INFLUENCED"))
        self.influenced_field = _ArtistField(
            "Search or type new artist name…", known_names_lower, self
        )
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

        self.setLayout(layout)
        self.resize(480, 420)

    @staticmethod
    def _section_label(text):
        label = QLabel(text)
        label.setProperty("influenceSection", "true")
        return label

    def _swap_fields(self):
        self.influencer_field.swap_text_with(self.influenced_field)

    def _resolve_artist(self, field):
        """Get or create the artist named in `field`, preferring a
        completer pick over a name lookup over creating a new artist."""
        name = field.text()
        matched_id = field.matched_id()
        if matched_id is not None:
            return matched_id, False

        for artist_id, artist_name in self.all_artists:
            if artist_name.lower() == name.lower():
                return artist_id, False

        try:
            new_artist = self.controller.add.add_entity("Artist", artist_name=name)
        except SQLAlchemyError as e:
            raise Exception(f"Failed to create new artist '{name}': {e!s}") from e

        new_artist_id = new_artist.artist_id
        self.all_artists.append((new_artist_id, name))
        self.created_artists.append((new_artist_id, name))
        self.influencer_field.note_known_name(name, new_artist_id)
        self.influenced_field.note_known_name(name, new_artist_id)
        return new_artist_id, True

    def add_influence(self):
        influencer_name = self.influencer_field.text()
        influenced_name = self.influenced_field.text()
        description = self.description_edit.toPlainText().strip()

        if not influencer_name or not influenced_name:
            show_status_message(self, "Please enter both influencer and influenced artist names!")
            return

        if influencer_name.lower() == influenced_name.lower():
            show_status_message(self, "An artist cannot influence themselves!")
            return

        try:
            self.created_artists = []

            influencer_id, _influencer_created = self._resolve_artist(self.influencer_field)
            influenced_id, _influenced_created = self._resolve_artist(self.influenced_field)

            influence_data = {
                "influencer_id": influencer_id,
                "influenced_id": influenced_id,
                "description": description if description else None,
            }

            self.controller.add.add_entity("ArtistInfluence", **influence_data)

            self.accept()

        except Exception as e:
            # Intentional broad boundary catch: _resolve_artist() above
            # re-raises DB failures as a plain Exception (not a specific
            # subclass), so this Qt button-click slot must catch the base
            # type to avoid crashing the app instead of showing this dialog.
            logger.exception("Failed to add influence")
            QMessageBox.critical(self, "Error", f"Failed to add influence: {e!s}")

    def get_created_artists(self):
        """Return list of newly created artists (artist_id, artist_name)"""
        return self.created_artists


class _InfluenceRow(QFrame):
    """One relationship row in RemoveInfluenceDialog's results list: a
    compact card naming both artists plus, when present, the relationship's
    description -- replacing the old plain "A → B" text line."""

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
        self.setProperty("selected", "true" if selected else "false")
        _repolish(self)


class RemoveInfluenceDialog(QDialog):
    def __init__(self, controller, all_influences, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.all_influences = all_influences
        self.selected_influence = None
        self._selected_row = None
        self.setWindowTitle("Remove Influence Relationship")
        self.setModal(True)
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout()

        # Search
        layout.addWidget(QLabel("Search Influence Relationships:"))
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("Search by artist name...")
        self.search_box.setClearButtonEnabled(True)
        self.search_box.textChanged.connect(self.filter_influences)
        layout.addWidget(self.search_box)

        # Search status
        self.search_status = QLabel("Type to search...")
        layout.addWidget(self.search_status)

        # Results list
        layout.addWidget(QLabel("Select Relationship to Remove:"))
        self.results_list = QListWidget()
        self.results_list.itemClicked.connect(self.on_item_selected)
        layout.addWidget(self.results_list)

        # Selected item display
        self.selected_display = QLabel("No relationship selected")
        layout.addWidget(self.selected_display)

        # Buttons
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

        self.setLayout(layout)
        self.resize(520, 440)

        self.filter_influences("")

    def filter_influences(self, text):
        """Filter and display results in the list widget"""
        self.results_list.clear()
        self._selected_row = None

        if not text.strip():
            # Show all when search is empty
            influences_to_show = self.all_influences
            self.search_status.setText(f"Showing all {len(influences_to_show)} relationships")
        else:
            # Filter based on search text
            search_lower = text.lower()
            influences_to_show = []
            for inf in self.all_influences:
                influencer_name = inf["influencer_name"].lower()
                influenced_name = inf["influenced_name"].lower()

                if search_lower in influencer_name or search_lower in influenced_name:
                    influences_to_show.append(inf)

            self.search_status.setText(f"Found {len(influences_to_show)} relationships")

        # Populate the list widget with card-style rows
        for inf in influences_to_show:
            item = QListWidgetItem()
            item.setData(1000, inf)  # Store the influence data in the item
            row = _InfluenceRow(inf)
            item.setSizeHint(row.sizeHint())
            self.results_list.addItem(item)
            self.results_list.setItemWidget(item, row)

        # Clear selection when filtering
        self.remove_button.setEnabled(False)
        self.selected_influence = None
        self.selected_display.setText("No relationship selected")

    def on_item_selected(self, item):
        """Handle when user clicks an item in the list"""
        if self._selected_row is not None:
            self._selected_row.set_selected(False)

        influence_data = item.data(1000)
        self.selected_influence = influence_data
        row = self.results_list.itemWidget(item)
        row.set_selected(True)
        self._selected_row = row

        influencer_name = influence_data["influencer_name"]
        influenced_name = influence_data["influenced_name"]

        self.selected_display.setText(f"Selected: {influencer_name} → {influenced_name}")
        self.remove_button.setEnabled(True)

    def remove_influence(self):
        """Remove the selected influence relationship"""
        if not self.selected_influence:
            show_status_message(self, "Please select a relationship to remove!")
            return

        try:
            influencer_id = self.selected_influence["influencer_id"]
            influenced_id = self.selected_influence["influenced_id"]
            influencer_name = self.selected_influence["influencer_name"]
            influenced_name = self.selected_influence["influenced_name"]

            # Confirm deletion
            reply = QMessageBox.question(
                self,
                "Confirm Removal",
                f"Remove this influence relationship?\n\n{influencer_name} → {influenced_name}",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )

            if reply == QMessageBox.Yes:
                success = self.controller.delete.delete_entity(
                    "ArtistInfluence", influencer_id=influencer_id, influenced_id=influenced_id
                )
                if success:
                    QMessageBox.information(
                        self,
                        "Success",
                        f"Influence relationship removed:\n{influencer_name} → {influenced_name}",
                    )
                    self.accept()
                else:
                    QMessageBox.critical(self, "Error", "Failed to remove relationship")

        except (SQLAlchemyError, KeyError) as e:
            logger.error(f"Error removing influence: {e}")
            QMessageBox.critical(self, "Error", f"Failed to remove: {e!s}")
