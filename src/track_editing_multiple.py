from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from src.db_mapping_tracks import TRACK_FIELDS
from src.logger_config import logger
from src.track_editing_loaders import DataLoaders
from src.track_editing_searchers import SearchHandlers


class MultiTrackEditDialog(QDialog):
    """Multi-track editing dialog for bulk editing fields marked as multiple=True."""

    field_modified = Signal()

    def __init__(self, tracks, controller, parent=None):
        super().__init__()
        self.tracks = tracks  # List of track ORM models
        self.controller = controller  # access to db operations
        self.modified_fields = set()

        self.is_multi_track = True

        # Initialize field storage dictionaries BEFORE creating tabs
        self.field_widgets = {}
        self.readonly_labels = {}  # Store readonly labels for data loading

        self.setWindowTitle(f"Edit Multiple Tracks ({len(tracks)} selected)")
        self.setMinimumWidth(800)
        self.setMinimumHeight(600)

        # Initialize helper classes
        self.loaders = DataLoaders(self, self.is_multi_track)
        self.searchers = SearchHandlers(self, self.is_multi_track)

        self.layout = QVBoxLayout(self)
        self.tabs = QTabWidget()

        # Create only tabs that have multiple=True fields
        self._create_basic_tab()
        self._create_date_tab()
        self._create_classical_tab()
        self._create_properties_tab()
        self._create_user_tab()
        self._create_roles_tab()
        self._create_places_tab()
        self._create_moods_tab()

        self.layout.addWidget(self.tabs)

        # Add buttons
        self.button_box = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        self.button_box.accepted.connect(self._on_save)
        self.button_box.rejected.connect(self.reject)
        self.layout.addWidget(self.button_box)

        # Load current track data
        self._load_track_data()

    def _create_tab_from_fields(self, category_name, tab_name):
        """Create a tab with fields from a specific category that are multiple=True and editable."""
        tab = QWidget()
        layout = QFormLayout(tab)

        # Get ONLY multiple=True AND editable fields for this category
        category_fields = {
            field_name: field_config
            for field_name, field_config in TRACK_FIELDS.items()
            if (
                field_config.category == category_name
                and getattr(field_config, "multiple", False)
                and field_config.editable
            )
        }

        for field_name, field_config in category_fields.items():
            label_text = field_config.friendly
            if field_config.tooltip:
                label = QLabel(f"{label_text} ℹ️")
                label.setToolTip(field_config.tooltip)
            else:
                label = QLabel(label_text)

            # Create editable widgets for editable fields (all should be editable due to our filter)
            if field_config.type == bool:  # noqa: E721
                widget = QCheckBox()
                widget.toggled.connect(
                    lambda checked, fn=field_name: self._on_field_modified(fn)
                )
            elif field_config.type == int:  # noqa: E721
                widget = QSpinBox()
                if field_config.min is not None:
                    widget.setMinimum(int(field_config.min))
                if field_config.max is not None:
                    widget.setMaximum(int(field_config.max))
                widget.valueChanged.connect(
                    lambda value, fn=field_name: self._on_field_modified(fn)
                )
            elif field_config.type == float:  # noqa: E721
                widget = QDoubleSpinBox()
                if field_config.min is not None:
                    widget.setMinimum(field_config.min)
                if field_config.max is not None:
                    widget.setMaximum(field_config.max)
                widget.setDecimals(2)
                widget.valueChanged.connect(
                    lambda value, fn=field_name: self._on_field_modified(fn)
                )
            elif field_config.longtext:
                widget = QTextEdit()
                widget.textChanged.connect(
                    lambda fn=field_name: self._on_field_modified(fn)
                )
            else:
                widget = QLineEdit()
                if field_config.placeholder:
                    widget.setPlaceholderText(field_config.placeholder)
                if field_config.length:
                    widget.setMaxLength(field_config.length)
                widget.textChanged.connect(
                    lambda text, fn=field_name: self._on_field_modified(fn)
                )

            self.field_widgets[field_name] = widget
            layout.addRow(label, widget)

        # Only add the tab if it has fields
        if category_fields:
            self.tabs.addTab(tab, tab_name)
            return tab
        return None

    def _create_basic_tab(self):
        """Create basic track information tab with multiple=True fields only."""
        tab = self._create_tab_from_fields("Basic", "Basic Info")
        if not tab:
            return None

        # Add informational label for multi-edit
        info_label = QLabel("Changes will apply to all selected tracks")
        info_label.setStyleSheet("QLabel { color: #666666; font-style: italic; }")
        tab.layout().insertRow(0, info_label)
        return tab

    def _create_date_tab(self):
        """Create date-related information tab with multiple=True fields only."""
        return self._create_tab_from_fields("Date", "Dates")

    def _create_classical_tab(self):
        """Create classical music information tab with multiple=True fields only."""
        return self._create_tab_from_fields("Classical", "Classical")

    def _create_properties_tab(self):
        """Create audio properties tab with multiple=True fields only."""
        return self._create_tab_from_fields("Properties", "Properties")

    def _create_user_tab(self):
        """Create user-related information tab with multiple=True fields only."""
        return self._create_tab_from_fields("User", "User Data")

    # Relationship tabs are kept the same but will show empty since they don't have multiple=True fields
    def _create_roles_tab(self):
        """Search-based artist roles management."""
        tab = QWidget()
        layout = QVBoxLayout(tab)

        # Search and add section
        search_layout = QHBoxLayout()

        self.artist_search_edit = QLineEdit()
        self.artist_search_edit.setPlaceholderText(
            "Search artists... (min 2 characters)"
        )
        self.artist_search_edit.textChanged.connect(
            self.searchers._on_artist_search_changed
        )
        search_layout.addWidget(self.artist_search_edit)

        # Add search results dropdown
        self.artist_search_combo = QComboBox()
        self.artist_search_combo.setVisible(False)
        self.artist_search_combo.currentIndexChanged.connect(
            self.searchers._on_artist_selected
        )
        search_layout.addWidget(self.artist_search_combo)

        self.role_edit = QLineEdit()
        self.role_edit.setPlaceholderText("Role (performer, composer, etc.)")
        self.role_edit.textChanged.connect(self.searchers._on_role_changed)
        search_layout.addWidget(self.role_edit)

        self.add_artist_role_btn = QPushButton("Add Role")
        self.add_artist_role_btn.clicked.connect(self.searchers._add_artist_role)
        self.add_artist_role_btn.setEnabled(False)
        search_layout.addWidget(self.add_artist_role_btn)

        layout.addLayout(search_layout)

        # Current roles table
        self.artist_roles_table = QTableWidget(0, 3)
        self.artist_roles_table.setHorizontalHeaderLabels(["Artist", "Role", "Actions"])
        self.artist_roles_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.Stretch
        )
        layout.addWidget(self.artist_roles_table)

        self.tabs.addTab(tab, "Artists && Roles")

    def _create_genres_tab(self):
        """Search-based genre management."""
        tab = QWidget()
        layout = QVBoxLayout(tab)

        # Search and add section
        search_layout = QHBoxLayout()

        self.genre_search_edit = QLineEdit()
        self.genre_search_edit.setPlaceholderText("Search genres... (min 2 characters)")
        self.genre_search_edit.textChanged.connect(
            self.searchers._on_genre_search_changed
        )
        search_layout.addWidget(self.genre_search_edit)

        # Add search results dropdown
        self.genre_search_combo = QComboBox()
        self.genre_search_combo.setVisible(False)
        self.genre_search_combo.currentIndexChanged.connect(
            self.searchers._on_genre_selected
        )
        search_layout.addWidget(self.genre_search_combo)

        self.add_genre_btn = QPushButton("Add Genre")
        self.add_genre_btn.clicked.connect(self.searchers._add_genre)
        self.add_genre_btn.setEnabled(False)
        search_layout.addWidget(self.add_genre_btn)

        layout.addLayout(search_layout)

        # Current genres list
        self.genres_list = QListWidget()
        layout.addWidget(self.genres_list)

        self.tabs.addTab(tab, "Genres")

    def _create_places_tab(self):
        """Search-based place associations with free-form type input."""
        tab = QWidget()
        layout = QVBoxLayout(tab)

        # Search and add section
        search_layout = QHBoxLayout()

        self.place_search_edit = QLineEdit()
        self.place_search_edit.setPlaceholderText("Search places... (min 2 characters)")
        self.place_search_edit.textChanged.connect(
            self.searchers._on_place_search_changed
        )
        search_layout.addWidget(self.place_search_edit)

        # Add search results dropdown
        self.place_search_combo = QComboBox()
        self.place_search_combo.setVisible(False)
        self.place_search_combo.currentIndexChanged.connect(
            self.searchers._on_place_selected
        )
        search_layout.addWidget(self.place_search_combo)

        self.place_type_edit = QLineEdit()
        self.place_type_edit.setPlaceholderText("Type (Recorded, Composed, etc.)")
        search_layout.addWidget(self.place_type_edit)

        self.add_place_btn = QPushButton("Add Place")
        self.add_place_btn.clicked.connect(self.searchers._add_place_association)
        self.add_place_btn.setEnabled(False)
        search_layout.addWidget(self.add_place_btn)

        layout.addLayout(search_layout)

        # Current places table
        self.place_associations_table = QTableWidget(0, 3)
        self.place_associations_table.setHorizontalHeaderLabels(
            ["Place", "Type", "Actions"]
        )
        self.place_associations_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.Stretch
        )
        layout.addWidget(self.place_associations_table)

        self.tabs.addTab(tab, "Places")

    def _create_moods_tab(self):
        """Search-based mood associations."""
        tab = QWidget()
        layout = QVBoxLayout(tab)

        # Search and add section
        search_layout = QHBoxLayout()

        self.mood_search_edit = QLineEdit()
        self.mood_search_edit.setPlaceholderText("Search moods... (min 2 characters)")
        self.mood_search_edit.textChanged.connect(
            self.searchers._on_mood_search_changed
        )
        search_layout.addWidget(self.mood_search_edit)

        # Add search results dropdown
        self.mood_search_combo = QComboBox()
        self.mood_search_combo.setVisible(False)
        self.mood_search_combo.currentIndexChanged.connect(
            self.searchers._on_mood_selected
        )
        search_layout.addWidget(self.mood_search_combo)

        self.add_mood_btn = QPushButton("Add Mood")
        self.add_mood_btn.clicked.connect(self.searchers._add_mood)
        self.add_mood_btn.setEnabled(False)
        search_layout.addWidget(self.add_mood_btn)

        layout.addLayout(search_layout)

        # Current moods list
        self.moods_list = QListWidget()
        layout.addWidget(self.moods_list)

        self.tabs.addTab(tab, "Moods")

    def _load_track_data(self):
        """Load current track data into all fields, showing common values or empty if inconsistent."""
        if not self.tracks:
            return

        # For each field, check if all tracks have the same value
        for field_name, widget in self.field_widgets.items():
            values = []
            for track in self.tracks:
                if hasattr(track, field_name):
                    value = getattr(track, field_name)
                    values.append(value)

            if not values:
                continue

            # Check if all values are the same
            first_value = values[0]
            all_same = all(value == first_value for value in values)

            if all_same:
                # All tracks have the same value, display it
                value = first_value
            else:
                # Tracks have different values, display empty/placeholder
                value = None

            if value is not None:
                if isinstance(widget, QCheckBox):
                    widget.setChecked(bool(value))
                elif isinstance(widget, (QSpinBox, QDoubleSpinBox)):
                    widget.setValue(value)
                elif isinstance(widget, QTextEdit):
                    widget.setPlainText(str(value))
                elif isinstance(widget, QLineEdit):
                    widget.setText(str(value))
            else:
                # Handle None/inconsistent values by clearing the widget
                if isinstance(widget, QCheckBox):
                    widget.setChecked(False)
                elif isinstance(widget, (QSpinBox, QDoubleSpinBox)):
                    widget.setValue(0)
                elif isinstance(widget, QTextEdit):
                    widget.setPlainText("")
                elif isinstance(widget, QLineEdit):
                    widget.setText("")
                    widget.setPlaceholderText("(different values)")

    def _on_field_modified(self, field_name):
        """Mark field as modified when changed."""
        self.modified_fields.add(field_name)
        self.field_modified.emit()

    def _has_meaningful_change(self, old_value, new_value):
        """Determine if a change is meaningful (not just empty/None changes)."""
        if old_value is None and new_value in (None, "", 0, 0.0):
            return False
        if old_value == new_value:
            return False
        if str(old_value).strip() == str(new_value).strip():
            return False
        return True

    def _on_save(self):
        """Save modified fields to all selected tracks."""
        try:
            updates = {}

            for field_name in self.modified_fields:
                if field_name in self.field_widgets:
                    widget = self.field_widgets[field_name]
                    field_config = TRACK_FIELDS[field_name]

                    if isinstance(widget, QCheckBox):
                        new_value = widget.isChecked()
                    elif isinstance(widget, (QSpinBox, QDoubleSpinBox)):
                        new_value = widget.value()
                    elif isinstance(widget, QTextEdit):
                        new_value = widget.toPlainText()
                    elif isinstance(widget, QLineEdit):
                        new_value = widget.text()
                    else:
                        continue

                    # Convert to proper type
                    if field_config.type == int:  # noqa: E721
                        try:
                            new_value = (
                                int(new_value) if new_value not in ("", None) else None
                            )
                        except (ValueError, TypeError):
                            new_value = None
                    elif field_config.type == float:  # noqa: E721
                        try:
                            new_value = (
                                float(new_value)
                                if new_value not in ("", None)
                                else None
                            )
                        except (ValueError, TypeError):
                            new_value = None
                    elif field_config.type == bool:  # noqa: E721
                        new_value = bool(new_value)

                    updates[field_name] = new_value

            if updates:
                # Apply updates to all selected tracks
                for track in self.tracks:
                    self.controller.update.update_entity(
                        "Track", track.track_id, **updates
                    )
                logger.info(
                    f"Updated {len(self.tracks)} tracks with fields: {list(updates.keys())}"
                )

            self.accept()

        except Exception as e:
            logger.error(f"Error saving multiple tracks: {e}")
            # Show error message to user
            from PySide6.QtWidgets import QMessageBox

            QMessageBox.critical(self, "Save Error", f"Failed to save tracks: {str(e)}")

    # Delegate methods to helper classes
    def _load_artist_roles(self):
        self.loaders._load_artist_roles()

    def _load_genres(self):
        self.loaders._load_genres()

    def _load_place_associations(self):
        self.loaders._load_place_associations()

    def _load_moods(self):
        self.loaders._load_moods()

    # Delegate search handlers
    def __getattr__(self, name):
        """Delegate unknown methods to search handlers."""
        if hasattr(self.searchers, name):
            return getattr(self.searchers, name)
        elif hasattr(self.loaders, name):
            return getattr(self.loaders, name)
        # Add this to break the recursion:
        raise AttributeError(
            f"'{self.__class__.__name__}' object has no attribute '{name}'"
        )
