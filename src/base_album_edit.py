"""
Controller pattern: self.controller.get.get_entity_object("Album", **kwargs)
self.controller.get.get_all_entities
self.controller.update.update_entity("Album", self.album.album_id, **kwargs)
Tabs: Basic, track list, artwork, artist credits, publishers places & awards, advanced
"""

from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from src.base_track_view import BaseTrackView
from src.logger_config import logger


class AlbumEditor(QDialog):
    """QDialog to edit all album metadata with tabs for each section"""

    def __init__(self, controller, album):
        super().__init__()
        self.controller = controller  # grants access to db
        self.album = album  # Album ORM object

        self.setWindowTitle(f"Edit Album: {album.album_name}")
        self.setMinimumSize(1000, 700)

        self.init_ui()
        self.load_album_data()
        self.setup_connections()

    def init_ui(self):
        """Initialize the UI with tabs"""
        main_layout = QVBoxLayout(self)

        # Title
        title_label = QLabel(f"Editing: {self.album.album_name}")
        title_label.setStyleSheet("font-size: 16px; font-weight: bold;")
        main_layout.addWidget(title_label)

        # Tab widget
        self.tab_widget = QTabWidget()

        # Create tabs
        self.basic_tab = self.create_basic_tab()
        self.tracklist_tab = self.create_tracklist_tab()
        self.artwork_tab = self.create_artwork_tab()
        self.artists_tab = self.create_artists_tab()
        self.publishers_tab = self.create_publishers_tab()
        self.places_tab = self.create_places_tab()
        self.awards_tab = self.create_awards_tab()
        self.advanced_tab = self.create_advanced_tab()

        # Add tabs
        self.tab_widget.addTab(self.basic_tab, "Basic")
        self.tab_widget.addTab(self.tracklist_tab, "Track List")
        self.tab_widget.addTab(self.artwork_tab, "Artwork")
        self.tab_widget.addTab(self.artists_tab, "Artist Credits")
        self.tab_widget.addTab(self.publishers_tab, "Publishers")
        self.tab_widget.addTab(self.places_tab, "Places")
        self.tab_widget.addTab(self.awards_tab, "Awards")
        self.tab_widget.addTab(self.advanced_tab, "Advanced")

        main_layout.addWidget(self.tab_widget)

        # Buttons
        button_layout = QHBoxLayout()

        self.save_button = QPushButton("Save")
        self.save_button.setStyleSheet("background-color: #4CAF50; color: white;")
        self.save_button.clicked.connect(self.save_album)

        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setStyleSheet("background-color: #f44336; color: white;")
        self.cancel_button.clicked.connect(self.reject)

        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.clicked.connect(self.refresh_data)

        button_layout.addStretch()
        button_layout.addWidget(self.refresh_button)
        button_layout.addWidget(self.cancel_button)
        button_layout.addWidget(self.save_button)

        main_layout.addLayout(button_layout)

    def create_basic_tab(self):
        """Create the basic information tab"""
        tab = QWidget()
        layout = QVBoxLayout(tab)

        # Album info group
        info_group = QGroupBox("Album Information")
        info_layout = QFormLayout()

        self.title_edit = QLineEdit()
        self.subtitle_edit = QLineEdit()
        self.language_combo = QComboBox()
        self.language_combo.addItems(
            ["English", "Japanese", "Spanish", "French", "German", "Other"]
        )

        self.release_type_combo = QComboBox()
        self.release_type_combo.addItems(
            [
                "Album",
                "Single",
                "EP",
                "Compilation",
                "Live",
                "Soundtrack",
                "Remix",
                "Demo",
                "Mixtape",
                "Other",
            ]
        )

        self.description_edit = QTextEdit()
        self.description_edit.setMaximumHeight(100)

        self.catalog_edit = QLineEdit()
        self.mbid_edit = QLineEdit()

        info_layout.addRow("Title:", self.title_edit)
        info_layout.addRow("Subtitle:", self.subtitle_edit)
        info_layout.addRow("Language:", self.language_combo)
        info_layout.addRow("Release Type:", self.release_type_combo)
        info_layout.addRow("Catalog #:", self.catalog_edit)
        info_layout.addRow("MBID:", self.mbid_edit)
        info_layout.addRow("Description:", self.description_edit)

        info_group.setLayout(info_layout)
        layout.addWidget(info_group)

        # Release date group
        date_group = QGroupBox("Release Date")
        date_layout = QHBoxLayout()

        self.year_spin = QSpinBox()
        self.year_spin.setRange(1800, datetime.now().year)
        self.year_spin.setSpecialValueText("Year")

        self.month_spin = QSpinBox()
        self.month_spin.setRange(0, 12)
        self.month_spin.setSpecialValueText("Month")
        self.month_spin.setPrefix("")

        self.day_spin = QSpinBox()
        self.day_spin.setRange(0, 31)
        self.day_spin.setSpecialValueText("Day")
        self.day_spin.setPrefix("")

        date_layout.addWidget(QLabel("Year:"))
        date_layout.addWidget(self.year_spin)
        date_layout.addStretch()
        date_layout.addWidget(QLabel("Month:"))
        date_layout.addWidget(self.month_spin)
        date_layout.addStretch()
        date_layout.addWidget(QLabel("Day:"))
        date_layout.addWidget(self.day_spin)

        date_group.setLayout(date_layout)
        layout.addWidget(date_group)

        # Flags group
        flags_group = QGroupBox("Album Flags")
        flags_layout = QVBoxLayout()

        self.live_checkbox = QCheckBox("Live Recording")
        self.compilation_checkbox = QCheckBox("Compilation")
        self.fixed_checkbox = QCheckBox("Metadata Fixed")

        flags_layout.addWidget(self.live_checkbox)
        flags_layout.addWidget(self.compilation_checkbox)
        flags_layout.addWidget(self.fixed_checkbox)

        flags_group.setLayout(flags_layout)
        layout.addWidget(flags_group)

        layout.addStretch()
        return tab

    def create_tracklist_tab(self):
        """Create the tracklist tab using BaseTrackView"""
        tab = QWidget()
        layout = QVBoxLayout(tab)

        # Get tracks for this album
        tracks = self.controller.get.get_all_entities(
            "Track", album_id=self.album.album_id
        )

        # Header with track count
        header_layout = QHBoxLayout()
        self.track_count_label = QLabel(f"Tracks: {len(tracks)}")
        self.total_duration_label = QLabel(
            f"Total Duration: {self.format_duration(self.album.total_duration)}"
        )

        header_layout.addWidget(self.track_count_label)
        header_layout.addStretch()
        header_layout.addWidget(self.total_duration_label)

        layout.addLayout(header_layout)

        # Create BaseTrackView for displaying tracks
        self.track_view = BaseTrackView(
            controller=self.controller,
            tracks=tracks,
            title=f"Tracks - {self.album.album_name}",
            enable_drag=True,
            enable_drop=True,
        )

        # Remove the dialog wrapping and just use the widget
        self.track_view.setParent(tab)
        self.track_view.setWindowFlags(Qt.Widget)  # Make it a regular widget

        layout.addWidget(self.track_view)

        # Track management buttons
        button_layout = QHBoxLayout()

        self.add_track_button = QPushButton("Add Track")
        self.add_track_button.clicked.connect(self.add_track)

        self.edit_track_button = QPushButton("Edit Selected")
        self.edit_track_button.clicked.connect(self.edit_selected_track)

        self.remove_track_button = QPushButton("Remove Selected")
        self.remove_track_button.clicked.connect(self.remove_selected_tracks)

        self.refresh_tracks_button = QPushButton("Refresh List")
        self.refresh_tracks_button.clicked.connect(self.refresh_tracklist)

        button_layout.addWidget(self.add_track_button)
        button_layout.addWidget(self.edit_track_button)
        button_layout.addWidget(self.remove_track_button)
        button_layout.addStretch()
        button_layout.addWidget(self.refresh_tracks_button)

        layout.addLayout(button_layout)

        return tab

    def create_artwork_tab(self):
        """Create the artwork tab"""
        tab = QWidget()
        layout = QVBoxLayout(tab)

        # Front cover
        front_group = QGroupBox("Front Cover")
        front_layout = QVBoxLayout()

        self.front_cover_label = QLabel("No image selected")
        self.front_cover_label.setAlignment(Qt.AlignCenter)
        self.front_cover_label.setMinimumHeight(200)
        self.front_cover_label.setStyleSheet(
            "border: 1px solid #ccc; background-color: #f0f0f0;"
        )

        self.front_cover_path_edit = QLineEdit()
        self.front_cover_browse_button = QPushButton("Browse...")
        self.front_cover_browse_button.clicked.connect(
            lambda: self.browse_image("front")
        )

        front_button_layout = QHBoxLayout()
        front_button_layout.addWidget(self.front_cover_path_edit)
        front_button_layout.addWidget(self.front_cover_browse_button)

        front_layout.addWidget(self.front_cover_label)
        front_layout.addLayout(front_button_layout)
        front_group.setLayout(front_layout)
        layout.addWidget(front_group)

        # Rear cover
        rear_group = QGroupBox("Rear Cover")
        rear_layout = QVBoxLayout()

        self.rear_cover_label = QLabel("No image selected")
        self.rear_cover_label.setAlignment(Qt.AlignCenter)
        self.rear_cover_label.setMinimumHeight(200)
        self.rear_cover_label.setStyleSheet(
            "border: 1px solid #ccc; background-color: #f0f0f0;"
        )

        self.rear_cover_path_edit = QLineEdit()
        self.rear_cover_browse_button = QPushButton("Browse...")
        self.rear_cover_browse_button.clicked.connect(lambda: self.browse_image("rear"))

        rear_button_layout = QHBoxLayout()
        rear_button_layout.addWidget(self.rear_cover_path_edit)
        rear_button_layout.addWidget(self.rear_cover_browse_button)

        rear_layout.addWidget(self.rear_cover_label)
        rear_layout.addLayout(rear_button_layout)
        rear_group.setLayout(rear_layout)
        layout.addWidget(rear_group)

        # Liner notes
        liner_group = QGroupBox("Liner Notes")
        liner_layout = QVBoxLayout()

        self.liner_path_edit = QLineEdit()
        self.liner_browse_button = QPushButton("Browse PDF/Image...")
        self.liner_browse_button.clicked.connect(lambda: self.browse_image("liner"))

        liner_button_layout = QHBoxLayout()
        liner_button_layout.addWidget(self.liner_path_edit)
        liner_button_layout.addWidget(self.liner_browse_button)

        liner_layout.addLayout(liner_button_layout)
        liner_group.setLayout(liner_layout)
        layout.addWidget(liner_group)

        layout.addStretch()
        return tab

    def create_artists_tab(self):
        """Create the artist credits tab"""
        tab = QWidget()
        layout = QVBoxLayout(tab)

        # Album artists section
        album_artists_group = QGroupBox("Album Artists")
        album_artists_layout = QVBoxLayout()

        self.album_artists_list = QListWidget()

        # Add/remove buttons for album artists
        album_buttons_layout = QHBoxLayout()
        self.add_album_artist_button = QPushButton("Add Artist")
        self.remove_album_artist_button = QPushButton("Remove Selected")

        album_buttons_layout.addWidget(self.add_album_artist_button)
        album_buttons_layout.addWidget(self.remove_album_artist_button)
        album_buttons_layout.addStretch()

        album_artists_layout.addWidget(self.album_artists_list)
        album_artists_layout.addLayout(album_buttons_layout)
        album_artists_group.setLayout(album_artists_layout)
        layout.addWidget(album_artists_group)

        # Track artists section (summary)
        track_artists_group = QGroupBox("Track Artists Summary")
        track_artists_layout = QVBoxLayout()

        self.track_artists_table = QTableWidget()
        self.track_artists_table.setColumnCount(3)
        self.track_artists_table.setHorizontalHeaderLabels(
            ["Track", "Primary Artist", "Other Roles"]
        )
        self.track_artists_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.Stretch
        )

        track_artists_layout.addWidget(self.track_artists_table)
        track_artists_group.setLayout(track_artists_layout)
        layout.addWidget(track_artists_group)

        layout.addStretch()
        return tab

    def create_publishers_tab(self):
        """Create the publishers tab"""
        tab = QWidget()
        layout = QVBoxLayout(tab)

        # Publishers list
        publishers_group = QGroupBox("Publishers")
        publishers_layout = QVBoxLayout()

        self.publishers_list = QListWidget()

        # Add/remove buttons
        pub_buttons_layout = QHBoxLayout()
        self.add_publisher_button = QPushButton("Add Publisher")
        self.remove_publisher_button = QPushButton("Remove Selected")

        pub_buttons_layout.addWidget(self.add_publisher_button)
        pub_buttons_layout.addWidget(self.remove_publisher_button)
        pub_buttons_layout.addStretch()

        publishers_layout.addWidget(self.publishers_list)
        publishers_layout.addLayout(pub_buttons_layout)
        publishers_group.setLayout(publishers_layout)
        layout.addWidget(publishers_group)

        layout.addStretch()
        return tab

    def create_places_tab(self):
        """Create the places tab"""
        tab = QWidget()
        layout = QVBoxLayout(tab)

        # Places list
        places_group = QGroupBox("Associated Places")
        places_layout = QVBoxLayout()

        self.places_list = QListWidget()

        # Add/remove buttons
        places_buttons_layout = QHBoxLayout()
        self.add_place_button = QPushButton("Add Place")
        self.remove_place_button = QPushButton("Remove Selected")

        places_buttons_layout.addWidget(self.add_place_button)
        places_buttons_layout.addWidget(self.remove_place_button)
        places_buttons_layout.addStretch()

        places_layout.addWidget(self.places_list)
        places_layout.addLayout(places_buttons_layout)
        places_group.setLayout(places_layout)
        layout.addWidget(places_group)

        layout.addStretch()
        return tab

    def create_awards_tab(self):
        """Create the awards tab"""
        tab = QWidget()
        layout = QVBoxLayout(tab)

        # Awards list
        awards_group = QGroupBox("Awards & Certifications")
        awards_layout = QVBoxLayout()

        self.awards_list = QListWidget()

        # Add/remove buttons
        awards_buttons_layout = QHBoxLayout()
        self.add_award_button = QPushButton("Add Award")
        self.remove_award_button = QPushButton("Remove Selected")

        awards_buttons_layout.addWidget(self.add_award_button)
        awards_buttons_layout.addWidget(self.remove_award_button)
        awards_buttons_layout.addStretch()

        awards_layout.addWidget(self.awards_list)
        awards_layout.addLayout(awards_buttons_layout)
        awards_group.setLayout(awards_layout)
        layout.addWidget(awards_group)

        # RIAA certification
        riaa_group = QGroupBox("RIAA Certification")
        riaa_layout = QVBoxLayout()

        self.sales_spin = QSpinBox()
        self.sales_spin.setRange(0, 1000000000)
        self.sales_spin.setSuffix(" units")

        self.certification_label = QLabel("Current: None")
        self.certification_label.setStyleSheet("font-weight: bold;")

        riaa_layout.addWidget(QLabel("Estimated Sales:"))
        riaa_layout.addWidget(self.sales_spin)
        riaa_layout.addWidget(self.certification_label)

        riaa_group.setLayout(riaa_layout)
        layout.addWidget(riaa_group)

        layout.addStretch()
        return tab

    def create_advanced_tab(self):
        """Create the advanced settings tab"""
        tab = QWidget()
        layout = QVBoxLayout(tab)

        # Audio properties group
        audio_group = QGroupBox("Audio Properties")
        audio_layout = QFormLayout()

        self.gain_spin = QSpinBox()
        self.gain_spin.setRange(-50, 50)
        self.gain_spin.setSuffix(" dB")

        self.peak_spin = QSpinBox()
        self.peak_spin.setRange(0, 100)
        self.peak_spin.setSuffix(" %")

        audio_layout.addRow("Album Gain:", self.gain_spin)
        audio_layout.addRow("Album Peak:", self.peak_spin)

        audio_group.setLayout(audio_layout)
        layout.addWidget(audio_group)

        # Status group
        status_group = QGroupBox("Album Status")
        status_layout = QVBoxLayout()

        self.status_combo = QComboBox()
        self.status_combo.addItems(
            [
                "official",
                "promotion",
                "bootleg",
                "withdrawn",
                "expunged",
                "cancelled",
                "unofficial",
            ]
        )

        status_layout.addWidget(self.status_combo)
        status_group.setLayout(status_layout)
        layout.addWidget(status_group)

        # Links group
        links_group = QGroupBox("External Links")
        links_layout = QFormLayout()

        self.wikipedia_edit = QLineEdit()
        self.website_edit = QLineEdit()

        links_layout.addRow("Wikipedia:", self.wikipedia_edit)
        links_layout.addRow("Website:", self.website_edit)

        links_group.setLayout(links_layout)
        layout.addWidget(links_group)

        # Aliases group
        aliases_group = QGroupBox("Album Aliases")
        aliases_layout = QVBoxLayout()

        self.aliases_list = QListWidget()

        aliases_buttons_layout = QHBoxLayout()
        self.add_alias_button = QPushButton("Add Alias")
        self.remove_alias_button = QPushButton("Remove Selected")

        aliases_buttons_layout.addWidget(self.add_alias_button)
        aliases_buttons_layout.addWidget(self.remove_alias_button)
        aliases_buttons_layout.addStretch()

        aliases_layout.addWidget(self.aliases_list)
        aliases_layout.addLayout(aliases_buttons_layout)
        aliases_group.setLayout(aliases_layout)
        layout.addWidget(aliases_group)

        layout.addStretch()
        return tab

    def load_album_data(self):
        """Load album data into the form"""
        # Basic info
        self.title_edit.setText(self.album.album_name or "")
        self.subtitle_edit.setText(self.album.album_subtitle or "")

        if self.album.album_language:
            index = self.language_combo.findText(self.album.album_language)
            if index >= 0:
                self.language_combo.setCurrentIndex(index)

        if self.album.release_type:
            index = self.release_type_combo.findText(self.album.release_type)
            if index >= 0:
                self.release_type_combo.setCurrentIndex(index)

        self.description_edit.setPlainText(self.album.album_description or "")
        self.catalog_edit.setText(self.album.catalog_number or "")
        self.mbid_edit.setText(self.album.MBID or "")

        # Release date
        if self.album.release_year:
            self.year_spin.setValue(self.album.release_year)
        if self.album.release_month:
            self.month_spin.setValue(self.album.release_month)
        if self.album.release_day:
            self.day_spin.setValue(self.album.release_day)

        # Flags
        self.live_checkbox.setChecked(bool(self.album.is_live))
        self.compilation_checkbox.setChecked(bool(self.album.is_compilation))
        self.fixed_checkbox.setChecked(bool(self.album.is_fixed))

        # Artwork paths
        if self.album.front_cover_path:
            self.front_cover_path_edit.setText(self.album.front_cover_path)
            self.load_image_preview(self.album.front_cover_path, self.front_cover_label)

        if self.album.rear_cover_path:
            self.rear_cover_path_edit.setText(self.album.rear_cover_path)
            self.load_image_preview(self.album.rear_cover_path, self.rear_cover_label)

        if self.album.album_liner_path:
            self.liner_path_edit.setText(self.album.album_liner_path)

        # Load lists
        self.load_album_artists()
        self.load_publishers()
        self.load_places()
        self.load_awards()
        self.load_aliases()
        self.load_track_artists_summary()

        # Awards tab
        if self.album.estimated_sales:
            self.sales_spin.setValue(self.album.estimated_sales)
            self.update_certification_label()

        # Advanced tab
        if self.album.album_gain:
            self.gain_spin.setValue(int(self.album.album_gain))
        if self.album.album_peak:
            self.peak_spin.setValue(int(self.album.album_peak * 100))

        if self.album.status:
            index = self.status_combo.findText(self.album.status)
            if index >= 0:
                self.status_combo.setCurrentIndex(index)

        if self.album.album_wikipedia_link:
            self.wikipedia_edit.setText(self.album.album_wikipedia_link)

    def load_album_artists(self):
        """Load album artists into the list"""
        self.album_artists_list.clear()
        for artist in self.album.album_artists:
            item = QListWidgetItem(artist.artist_name)
            item.setData(Qt.UserRole, artist.artist_id)
            self.album_artists_list.addItem(item)

    def load_publishers(self):
        """Load publishers into the list"""
        self.publishers_list.clear()
        for publisher in self.album.publishers:
            item = QListWidgetItem(publisher.publisher_name)
            item.setData(Qt.UserRole, publisher.publisher_id)
            self.publishers_list.addItem(item)

    def load_places(self):
        """Load places into the list"""
        self.places_list.clear()
        for place in self.album.places:
            item = QListWidgetItem(f"{place.place_name} ({place.place_type})")
            item.setData(Qt.UserRole, place.place_id)
            self.places_list.addItem(item)

    def load_awards(self):
        """Load awards into the list"""
        self.awards_list.clear()
        for award in self.album.awards:
            item_text = f"{award.award_name}"
            if award.award_year:
                item_text += f" ({award.award_year})"
            if award.award_category:
                item_text += f" - {award.award_category}"

            item = QListWidgetItem(item_text)
            item.setData(Qt.UserRole, award.award_id)
            self.awards_list.addItem(item)

    def load_aliases(self):
        """Load album aliases into the list"""
        self.aliases_list.clear()
        for alias in self.album.album_aliases:
            item_text = alias.alias_name
            if alias.alias_type:
                item_text += f" ({alias.alias_type})"

            item = QListWidgetItem(item_text)
            item.setData(Qt.UserRole, alias.alias_id)
            self.aliases_list.addItem(item)

    def load_track_artists_summary(self):
        """Load track artists summary"""
        self.track_artists_table.setRowCount(0)

        tracks = self.controller.get.get_all_entities(
            "Track", album_id=self.album.album_id
        )

        for i, track in enumerate(tracks):
            self.track_artists_table.insertRow(i)

            # Track name
            self.track_artists_table.setItem(i, 0, QTableWidgetItem(track.track_name))

            # Primary artist
            primary_artists = track.primary_artist_names
            self.track_artists_table.setItem(
                i, 1, QTableWidgetItem(str(primary_artists))
            )

            # Other roles
            other_roles = []
            for role in track.artist_roles:
                if role.role and role.role.role_name != "Primary":
                    other_roles.append(
                        f"{role.artist.artist_name} ({role.role.role_name})"
                    )

            self.track_artists_table.setItem(
                i, 2, QTableWidgetItem(", ".join(other_roles))
            )

    def setup_connections(self):
        """Setup signal connections"""
        # Sales spin connection for RIAA certification
        self.sales_spin.valueChanged.connect(self.update_certification_label)

        # Album artists buttons
        self.add_album_artist_button.clicked.connect(self.add_album_artist)
        self.remove_album_artist_button.clicked.connect(
            lambda: self.remove_list_item(self.album_artists_list, "album_artist")
        )

    def update_certification_label(self):
        """Update RIAA certification label based on sales"""
        sales = self.sales_spin.value()
        if sales < 250000:
            cert = "None"
        elif sales < 500000:
            cert = "Silver"
        elif sales < 1000000:
            cert = "Gold"
        else:
            platinum_count = sales // 1000000
            cert = f"{platinum_count}× Platinum" if platinum_count > 1 else "Platinum"

        self.certification_label.setText(f"Current: {cert}")

    def browse_image(self, image_type):
        """Browse for image files"""
        file_filter = "Images (*.png *.jpg *.jpeg *.bmp *.gif);;All files (*.*)"
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            f"Select {image_type.replace('_', ' ').title()} Image",
            "",
            file_filter,
        )

        if file_path:
            if image_type == "front":
                self.front_cover_path_edit.setText(file_path)
                self.load_image_preview(file_path, self.front_cover_label)
            elif image_type == "rear":
                self.rear_cover_path_edit.setText(file_path)
                self.load_image_preview(file_path, self.rear_cover_label)
            elif image_type == "liner":
                self.liner_path_edit.setText(file_path)

    def load_image_preview(self, file_path, label):
        """Load and display image preview"""
        try:
            pixmap = QPixmap(file_path)
            if not pixmap.isNull():
                # Scale to fit while maintaining aspect ratio
                scaled_pixmap = pixmap.scaled(
                    label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation
                )
                label.setPixmap(scaled_pixmap)
            else:
                label.setText("Invalid image")
        except Exception as e:
            logger.error(f"Error loading image preview: {e}")
            label.setText("Error loading image")

    def add_album_artist(self):
        """Open dialog to add album artist"""
        # This would typically open an artist selection dialog
        # For now, just show a message
        QMessageBox.information(
            self,
            "Add Artist",
            "Artist selection dialog would open here.\n"
            "Implementation would depend on your artist selection UI.",
        )

    def remove_list_item(self, list_widget, item_type):
        """Remove selected item from list"""
        current_item = list_widget.currentItem()
        if current_item:
            list_widget.takeItem(list_widget.row(current_item))

    def add_track(self):
        """Add a new track to the album"""
        QMessageBox.information(
            self,
            "Add Track",
            "Track editor would open here.\n"
            "Implementation would depend on your track editing UI.",
        )

    def edit_selected_track(self):
        """Edit the selected track"""
        selected_tracks = self.track_view.get_selected_tracks()
        if not selected_tracks:
            QMessageBox.warning(self, "No Selection", "Please select a track to edit.")
            return

        # Open track editor for first selected track
        track = selected_tracks[0]
        QMessageBox.information(
            self,
            "Edit Track",
            f"Would open track editor for: {track.track_name}\n"
            "Implementation would depend on your track editing UI.",
        )

    def remove_selected_tracks(self):
        """Remove selected tracks from album"""
        selected_tracks = self.track_view.get_selected_tracks()
        if not selected_tracks:
            QMessageBox.warning(self, "No Selection", "Please select tracks to remove.")
            return

        confirm = QMessageBox.question(
            self,
            "Confirm Removal",
            f"Remove {len(selected_tracks)} track(s) from album?\n"
            "This will only remove the association, not delete the tracks from the library.",
            QMessageBox.Yes | QMessageBox.No,
        )

        if confirm == QMessageBox.Yes:
            try:
                for track in selected_tracks:
                    # Update track to remove album association
                    self.controller.update.update_entity(
                        "Track", track.track_id, album_id=None
                    )

                self.refresh_tracklist()
                QMessageBox.information(
                    self,
                    "Success",
                    f"Removed {len(selected_tracks)} track(s) from album.",
                )

            except Exception as e:
                logger.error(f"Error removing tracks: {e}")
                QMessageBox.critical(
                    self, "Error", f"Failed to remove tracks: {str(e)}"
                )

    def refresh_tracklist(self):
        """Refresh the tracklist view"""
        tracks = self.controller.get.get_all_entities(
            "Track", album_id=self.album.album_id
        )
        self.track_view.load_data(tracks)
        self.track_count_label.setText(f"Tracks: {len(tracks)}")
        self.total_duration_label.setText(
            f"Total Duration: {self.format_duration(self.album.total_duration)}"
        )

    def refresh_data(self):
        """Refresh all data from database"""
        try:
            # Reload album from database
            self.album = self.controller.get.get_entity_object(
                "Album", album_id=self.album.album_id
            )

            # Reload all data
            self.load_album_data()
            self.refresh_tracklist()

            QMessageBox.information(
                self, "Refreshed", "Album data refreshed from database."
            )

        except Exception as e:
            logger.error(f"Error refreshing album data: {e}")
            QMessageBox.critical(self, "Error", f"Failed to refresh data: {str(e)}")

    def save_album(self):
        """Save album changes to database"""
        try:
            # Collect basic data
            album_data = {
                "album_name": self.title_edit.text().strip() or None,
                "album_subtitle": self.subtitle_edit.text().strip() or None,
                "album_language": self.language_combo.currentText() or None,
                "release_type": self.release_type_combo.currentText() or None,
                "album_description": self.description_edit.toPlainText().strip()
                or None,
                "catalog_number": self.catalog_edit.text().strip() or None,
                "MBID": self.mbid_edit.text().strip() or None,
                "release_year": self.year_spin.value()
                if self.year_spin.value() > 0
                else None,
                "release_month": self.month_spin.value()
                if self.month_spin.value() > 0
                else None,
                "release_day": self.day_spin.value()
                if self.day_spin.value() > 0
                else None,
                "is_live": 1 if self.live_checkbox.isChecked() else 0,
                "is_compilation": 1 if self.compilation_checkbox.isChecked() else 0,
                "is_fixed": 1 if self.fixed_checkbox.isChecked() else 0,
                "front_cover_path": self.front_cover_path_edit.text().strip() or None,
                "rear_cover_path": self.rear_cover_path_edit.text().strip() or None,
                "album_liner_path": self.liner_path_edit.text().strip() or None,
                "estimated_sales": self.sales_spin.value() or None,
                "album_gain": self.gain_spin.value()
                if self.gain_spin.value() != 0
                else None,
                "album_peak": self.peak_spin.value() / 100
                if self.peak_spin.value() > 0
                else None,
                "status": self.status_combo.currentText() or None,
                "album_wikipedia_link": self.wikipedia_edit.text().strip() or None,
            }

            # Update album in database
            self.controller.update.update_entity(
                "Album", self.album.album_id, **album_data
            )

            # Update album object
            self.album = self.controller.get.get_entity_object(
                "Album", album_id=self.album.album_id
            )

            self.accept()

        except Exception as e:
            logger.error(f"Error saving album: {e}")
            QMessageBox.critical(self, "Error", f"Failed to save album: {str(e)}")

    @staticmethod
    def format_duration(seconds):
        """Format duration in seconds to HH:MM:SS or MM:SS"""
        if not seconds:
            return "0:00"

        seconds = int(seconds)
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        secs = seconds % 60

        if hours > 0:
            return f"{hours}:{minutes:02d}:{secs:02d}"
        else:
            return f"{minutes}:{secs:02d}"

    def closeEvent(self, event):
        """Handle dialog close event"""
        # Clean up track view if needed
        if hasattr(self, "track_view"):
            self.track_view.setParent(None)
        event.accept()
