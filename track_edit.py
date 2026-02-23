# track_editing.py
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QShortcut
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QMenu,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from db_mapping_tracks import TRACK_FIELDS
from logger_config import logger
from track_editing_loaders import DataLoaders
from track_editing_searchers import SearchHandlers


class TrackEditDialog(QDialog):
    """Track editing dialog with real-time updates and smart field handling."""

    field_modified = Signal()

    def __init__(self, track, controller, parent=None):
        super().__init__()
        self.track = track  # track ORM model
        self.controller = controller  # access to db operations
        self.modified_fields = set()

        self.is_multi_track = False

        # Initialize field storage dictionaries BEFORE creating tabs
        self.field_widgets = {}
        self.readonly_labels = {}  # Store readonly labels for data loading

        self.setWindowTitle(f"Edit Track: {track.track_name}")
        self.setMinimumWidth(800)
        self.setMinimumHeight(600)

        # Initialize helper classes
        self.loaders = DataLoaders(self, self.is_multi_track)
        self.searchers = SearchHandlers(self, self.is_multi_track)

        self.main_layout = QHBoxLayout(self)  # Horizontal layout

        # Create sidebar
        self.sidebar = QListWidget()
        self.sidebar.setMaximumWidth(150)
        self.sidebar.itemClicked.connect(self._on_sidebar_item_clicked)

        # Create container for tabs
        self.tab_container = QWidget()
        self.tab_layout = QVBoxLayout(self.tab_container)
        self.tabs = QTabWidget()
        self.tab_layout.addWidget(self.tabs)

        # Add to main layout
        self.main_layout.addWidget(self.sidebar)
        self.main_layout.addWidget(self.tab_container)

        # Create all tabs
        self._create_tab_from_fields("Basic", "Basic")
        self._create_lyrics_tab()
        self._create_tab_from_fields("Date", "Dates")
        self._create_tab_from_fields("Classical", "Classical")
        self._create_tab_from_fields("Properties", "Properties")
        self._create_tab_from_fields("User", "User Data")
        self._create_identification_tab()
        self._create_roles_tab()
        self._create_genres_tab()
        self._create_places_tab()
        self._create_moods_tab()
        self._create_awards_tab()
        self._create_tab_from_fields("Alias", "Name Aliases")
        self._create_samples_tab()
        self._create_tab_from_fields("Advanced", "Advanced")

        self.main_layout.addWidget(self.tabs)
        self._setup_tab_shortcuts()
        self._populate_sidebar()

        # Add buttons
        self.button_box = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        self.button_box.accepted.connect(self._on_save)
        self.button_box.rejected.connect(self.reject)
        self.main_layout.addWidget(self.button_box)

        # Load current track data
        self._load_track_data()

    def _create_tab_from_fields(self, category_name, tab_name):
        """Create a tab with fields from a specific category."""
        tab = QWidget()
        layout = QFormLayout(tab)

        # Get ALL fields for this category (both editable and non-editable)
        category_fields = {
            field_name: field_config
            for field_name, field_config in TRACK_FIELDS.items()
            if field_config.category == category_name
        }

        # Remove the local declaration - use the instance variables instead
        for field_name, field_config in category_fields.items():
            label_text = field_config.friendly
            if field_config.tooltip:
                label = QLabel(f"{label_text} ℹ️")
                label.setToolTip(field_config.tooltip)
            else:
                label = QLabel(label_text)

            if not field_config.editable:
                # Create simple QLabel for readonly fields (not clickable)
                value_widget = QLabel()
                value_widget.setWordWrap(True)

                self.readonly_labels[field_name] = value_widget
                layout.addRow(label, value_widget)
            else:
                # Create editable widgets for editable fields
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

        self.tabs.addTab(tab, tab_name)
        return tab

    def _create_lyrics_tab(self):
        """Create lyrics tab with search functionality."""
        tab = QWidget()
        layout = QVBoxLayout(tab)

        # Lyrics search button
        search_layout = QHBoxLayout()
        self.search_lyrics_btn = QPushButton("Search Lyrics Online")
        self.search_lyrics_btn.clicked.connect(lambda: self._search_lyrics(self.track))
        search_layout.addWidget(self.search_lyrics_btn)
        search_layout.addStretch()
        layout.addLayout(search_layout)

        # Lyrics editor
        lyrics_label = QLabel("Lyrics:")
        lyrics_label.setToolTip(TRACK_FIELDS["lyrics"].tooltip)
        self.lyrics_edit = QTextEdit()
        self.lyrics_edit.textChanged.connect(lambda: self._on_field_modified("lyrics"))
        layout.addWidget(lyrics_label)
        layout.addWidget(self.lyrics_edit)

        self.tabs.addTab(tab, "Lyrics")

    def _create_identification_tab(self):
        """Create identification information tab with Wikipedia search."""
        tab = self._create_tab_from_fields("Identification", "Identification")

        # Add Wikipedia search button to the identification tab
        wikipedia_layout = QHBoxLayout()
        wikipedia_label = QLabel("Wikipedia Link:")
        wikipedia_label.setToolTip(TRACK_FIELDS["track_wikipedia_link"].tooltip)

        # The track_wikipedia_link_edit should already be created by _create_tab_from_fields
        self.track_wikipedia_link_edit = self.field_widgets["track_wikipedia_link"]

        self.wikipedia_search_btn = QPushButton("Search Wikipedia")
        self.wikipedia_search_btn.clicked.connect(self._search_wikipedia)
        wikipedia_layout.addWidget(self.track_wikipedia_link_edit)
        wikipedia_layout.addWidget(self.wikipedia_search_btn)

        # Find the Wikipedia row in the form and replace it with our enhanced layout
        form_layout = tab.layout()
        for i in range(form_layout.rowCount()):
            item = form_layout.itemAt(i, QFormLayout.LabelRole)
            if item and item.widget() and "Wikipedia" in item.widget().text():
                # Remove the existing row
                field_item = form_layout.itemAt(i, QFormLayout.FieldRole)
                if field_item:
                    form_layout.removeRow(i)
                    break

        # Add the enhanced Wikipedia row
        form_layout.insertRow(-1, wikipedia_label, wikipedia_layout)

        return tab

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

    def _create_awards_tab(self):
        """Search-based award associations."""
        tab = QWidget()
        layout = QVBoxLayout(tab)

        # Search and add section
        search_layout = QHBoxLayout()

        self.award_search_edit = QLineEdit()
        self.award_search_edit.setPlaceholderText("Search awards... (min 2 characters)")
        self.award_search_edit.textChanged.connect(
            self.searchers._on_award_search_changed
        )
        search_layout.addWidget(self.award_search_edit)

        # Add search results dropdown
        self.award_search_combo = QComboBox()
        self.award_search_combo.setVisible(False)
        self.award_search_combo.currentIndexChanged.connect(
            self.searchers._on_award_selected
        )
        search_layout.addWidget(self.award_search_combo)

        self.award_category_edit = QLineEdit()
        self.award_category_edit.setPlaceholderText("Category (optional)")
        search_layout.addWidget(self.award_category_edit)

        self.award_year_edit = QSpinBox()
        self.award_year_edit.setRange(1900, 2100)
        self.award_year_edit.setSpecialValueText("Year")
        search_layout.addWidget(self.award_year_edit)

        self.add_award_btn = QPushButton("Add Award")
        self.add_award_btn.clicked.connect(self._add_award)
        self.add_award_btn.setEnabled(False)
        search_layout.addWidget(self.add_award_btn)

        layout.addLayout(search_layout)

        # Current awards table
        self.awards_table = QTableWidget(0, 4)
        self.awards_table.setHorizontalHeaderLabels(
            ["Award", "Category", "Year", "Actions"]
        )
        self.awards_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        layout.addWidget(self.awards_table)

        self.tabs.addTab(tab, "Awards")

    def _load_track_data(self):
        """Load current track data into all fields."""
        # Load editable fields
        for field_name, widget in self.field_widgets.items():
            if hasattr(self.track, field_name):
                value = getattr(self.track, field_name)
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
                    # Handle None values by clearing the widget
                    if isinstance(widget, QCheckBox):
                        widget.setChecked(False)
                    elif isinstance(widget, (QSpinBox, QDoubleSpinBox)):
                        widget.setValue(0)
                    elif isinstance(widget, QTextEdit):
                        widget.setPlainText("")
                    elif isinstance(widget, QLineEdit):
                        widget.setText("")

        # Load readonly fields
        for field_name, label_widget in self.readonly_labels.items():
            if hasattr(self.track, field_name):
                value = getattr(self.track, field_name)
                field_config = TRACK_FIELDS.get(field_name)
                self._update_readonly_label(
                    field_name, field_config, label_widget, value
                )

        # Load relationship data
        self.loaders._load_artist_roles()
        self.loaders._load_genres()
        self.loaders._load_place_associations()
        self.loaders._load_moods()
        self.loaders._load_awards()
        self.loaders._load_samples()

    def _create_samples_tab(self):
        """Create samples management tab with search functionality."""
        tab = QWidget()
        layout = QVBoxLayout(tab)

        # Search for tracks that this track samples
        samples_section = QVBoxLayout()
        samples_label = QLabel("Samples Used (Tracks this track samples):")
        samples_label.setStyleSheet("font-weight: bold;")
        samples_section.addWidget(samples_label)

        # Search and add samples
        search_samples_layout = QHBoxLayout()

        self.sample_search_edit = QLineEdit()
        self.sample_search_edit.setPlaceholderText(
            "Search tracks to sample... (min 2 characters)"
        )
        self.sample_search_edit.textChanged.connect(
            self.searchers._on_sample_search_changed
        )
        search_samples_layout.addWidget(self.sample_search_edit)

        # Add search results dropdown
        self.sample_search_combo = QComboBox()
        self.sample_search_combo.setVisible(False)
        self.sample_search_combo.currentIndexChanged.connect(
            self.searchers._on_sample_selected
        )
        search_samples_layout.addWidget(self.sample_search_combo)

        self.add_sample_btn = QPushButton("Add Sample")
        self.add_sample_btn.clicked.connect(self.searchers._add_sample)
        self.add_sample_btn.setEnabled(False)
        search_samples_layout.addWidget(self.add_sample_btn)

        samples_section.addLayout(search_samples_layout)

        # Current samples used list
        self.samples_used_list = QListWidget()
        self.samples_used_list.itemDoubleClicked.connect(self._open_sampled_track)
        samples_section.addWidget(self.samples_used_list)

        layout.addLayout(samples_section)

        # Separator
        separator = QFrame()
        separator.setFrameShape(QFrame.HLine)
        separator.setFrameShadow(QFrame.Sunken)
        layout.addWidget(separator)

        # Tracks that sample this track
        sampled_by_section = QVBoxLayout()
        sampled_by_label = QLabel("Sampled By (Tracks that sample this track):")
        sampled_by_label.setStyleSheet("font-weight: bold;")
        sampled_by_section.addWidget(sampled_by_label)

        # This section is read-only - shows tracks that sample the current track
        self.sampled_by_list = QListWidget()
        self.sampled_by_list.itemDoubleClicked.connect(self._open_sampling_track)
        sampled_by_section.addWidget(self.sampled_by_list)

        layout.addLayout(sampled_by_section)

        # Add help text
        help_label = QLabel("Double-click on any track to open its edit dialog.")
        help_label.setStyleSheet("font-style: italic; color: #666;")
        layout.addWidget(help_label)

        layout.addStretch()

        self._setup_samples_context_menu()
        self.tabs.addTab(tab, "Samples")
        return tab

    def _open_sampled_track(self, item):
        """Open the edit dialog for a sampled track."""
        if item and item.data(Qt.UserRole):
            track_id = item.data(Qt.UserRole)
            track = self.controller.get.get_entity_object("Track", track_id=track_id)
            if track:
                from track_edit import TrackEditDialog

                dialog = TrackEditDialog(track, self.controller, self)
                dialog.exec()

    def _open_sampling_track(self, item):
        """Open the edit dialog for a track that samples this track."""
        if item and item.data(Qt.UserRole):
            track_id = item.data(Qt.UserRole)
            track = self.controller.get.get_entity_object("Track", track_id=track_id)
            if track:
                from track_edit import TrackEditDialog

                dialog = TrackEditDialog(track, self.controller, self)
                dialog.exec()

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
        """Save only modified track fields using TRACK_FIELDS configuration."""
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

                    old_value = getattr(self.track, field_name, None)

                    if self._has_meaningful_change(old_value, new_value):
                        # Convert to proper type
                        if field_config.type == int:  # noqa: E721
                            try:
                                new_value = (
                                    int(new_value)
                                    if new_value not in ("", None)
                                    else None
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
                self.controller.update.update_entity(
                    "Track", self.track.track_id, **updates
                )
                logger.info(
                    f"Updated track {self.track.track_id} with fields: {list(updates.keys())}"
                )

            self.accept()

        except Exception as e:
            logger.error(f"Error saving track: {e}")
            # Show error message to user
            from PySide6.QtWidgets import QMessageBox

            QMessageBox.critical(self, "Save Error", f"Failed to save track: {str(e)}")

    def _setup_samples_context_menu(self):
        """Setup context menu for samples list."""
        self.samples_used_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.samples_used_list.customContextMenuRequested.connect(
            self._show_samples_context_menu
        )

    def _show_samples_context_menu(self, position):
        """Show context menu for samples list."""
        item = self.samples_used_list.itemAt(position)
        if item:
            menu = QMenu(self)
            remove_action = menu.addAction("Remove Sample")

            action = menu.exec(self.samples_used_list.mapToGlobal(position))
            if action == remove_action:
                row = self.samples_used_list.row(item)
                self.searchers._remove_sample(row)

    # Delegate search handlers
    def __getattr__(self, name):
        """Delegate unknown methods to search handlers."""
        if hasattr(self.searchers, name):
            return getattr(self.searchers, name)
        elif hasattr(self.loaders, name):
            return getattr(self.loaders, name)
        raise AttributeError(
            f"'{self.__class__.__name__}' object has no attribute '{name}'"
        )

    def _update_readonly_label(self, field_name, field_config, label_widget, value):
        """Update a readonly label with new value and appropriate formatting."""
        if value is None or value == "":
            display_text = "—"  # Use em dash for empty values
        else:
            if field_config.type == bool:  # noqa: E721
                display_text = "Yes" if value else "No"
            elif field_config.type in (int, float):
                display_text = str(value)
            else:
                display_text = str(value)

        label_widget.setText(display_text)
        if len(display_text) > 50 and not field_config.longtext:
            label_widget.setToolTip(display_text)
            label_widget.setText(display_text[:47] + "...")
        else:
            label_widget.setToolTip("")

    def _setup_tab_shortcuts(self):
        """Set up keyboard shortcuts for tab navigation."""
        for i in range(min(self.tabs.count(), 9)):  # 1-9 shortcuts
            shortcut = QShortcut(f"Ctrl+{i + 1}", self)
            shortcut.activated.connect(lambda idx=i: self.tabs.setCurrentIndex(idx))

    def _populate_sidebar(self):
        """Populate sidebar with tab names."""
        for i in range(self.tabs.count()):
            tab_name = self.tabs.tabText(i)
            self.sidebar.addItem(tab_name)

    def _on_sidebar_item_clicked(self, item):
        """Switch to tab when sidebar item clicked."""
        index = self.sidebar.row(item)
        self.tabs.setCurrentIndex(index)
