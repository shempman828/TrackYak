"""
Unified Album Editor combining the best features of base_album_edit and album_detail.

This module provides a comprehensive QDialog for editing all album metadata using:
- ALBUM_FIELDS mapping for maintainable field definitions
- RelationshipHelpers for managing artists, publishers, places, and awards
- AlbumTabBuilder for organized tab structure
- DiscManagementView for track management
- Proper save/refresh functionality

Controller pattern:
    self.controller.get.get_entity_object("Album", **kwargs)
    self.controller.get.get_all_entities("Entity", **filters)
    self.controller.update.update_entity("Album", album_id, **kwargs)
"""

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from src.album_components import AlbumUIComponents
from src.album_editing_relationship_helpers import RelationshipHelpers
from src.album_tab import AlbumTabBuilder
from src.db_mapping_albums import ALBUM_FIELDS
from src.disc_view import DiscManagementView
from src.logger_config import logger


class AlbumEditor(QDialog):
    """
    Comprehensive album editor dialog with tabbed interface.

    Features:
    - Metadata editing using ALBUM_FIELDS mapping
    - Track management via DiscManagementView
    - Artist, publisher, place, and award relationships
    - Cover art management
    - Statistics and advanced settings
    """

    def __init__(self, controller, album, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.album = album

        # Initialize helpers
        self.helper = RelationshipHelpers(controller, album, self.refresh_view)
        self.field_widgets = {}
        self.tab_builder = AlbumTabBuilder(self)

        # UI setup
        self.setWindowTitle(f"Edit Album: {album.album_name}")
        self.setMinimumSize(1000, 700)

        self.init_editable_widgets()
        self.init_ui()
        self.setup_connections()

    def init_editable_widgets(self):
        """Initialize editable widgets based on ALBUM_FIELDS mapping"""
        for field_name, field_config in ALBUM_FIELDS.items():
            if not field_config.editable:
                continue

            current_value = getattr(self.album, field_name, None)
            self.field_widgets[field_name] = AlbumUIComponents.create_editable_field(
                field_config, current_value
            )

    def init_ui(self):
        """Initialize the main UI with scrollable content and tabs"""
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(15, 15, 15, 15)
        main_layout.setSpacing(15)

        # Header with title
        title_label = QLabel(f"Editing: {self.album.album_name}")
        title_label.setStyleSheet("font-size: 16px; font-weight: bold;")
        main_layout.addWidget(title_label)

        # Create scroll area
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QScrollArea.NoFrame)

        # Main content widget
        content_widget = QWidget()
        content_layout = QVBoxLayout(content_widget)
        content_layout.setSpacing(20)

        # Header section with cover and basic info
        content_layout.addWidget(self.create_header_section())

        # Tab widget for detailed sections
        tabs = QTabWidget()
        tabs.addTab(self.create_metadata_tab(), "Metadata")
        tabs.addTab(self.create_tracks_tab(), "Tracks")
        tabs.addTab(self.create_artwork_tab(), "Artwork")
        tabs.addTab(self.tab_builder.build_artists_tab(), "Artist Credits")
        tabs.addTab(self.tab_builder.build_relationships_tab(), "Publishers & Places")
        tabs.addTab(self.tab_builder.build_awards_tab(), "Awards")
        tabs.addTab(self.create_advanced_tab(), "Advanced")

        content_layout.addWidget(tabs)
        content_layout.addStretch()

        scroll_area.setWidget(content_widget)
        main_layout.addWidget(scroll_area)

        # Dialog buttons
        self.add_dialog_buttons(main_layout)

        # Size the dialog appropriately
        self.adjustSize()
        self.resize(
            self.sizeHint().boundedTo(
                QApplication.primaryScreen().availableGeometry().size() * 0.9
            )
        )

    def create_header_section(self):
        """Create the header section with album cover and title"""
        header_widget = QWidget()
        header_layout = QHBoxLayout(header_widget)
        header_layout.setSpacing(20)

        # Album cover
        cover_widget = self.create_cover_section()
        header_layout.addWidget(cover_widget)

        # Basic info preview
        info_widget = self.create_info_preview()
        header_layout.addWidget(info_widget, 1)

        return header_widget

    def create_cover_section(self):
        """Create the album cover display and change button"""
        cover_widget = QWidget()
        layout = QVBoxLayout(cover_widget)
        layout.setAlignment(Qt.AlignTop)

        # Album cover image
        self.cover_label = QLabel()
        self.cover_label.setAlignment(Qt.AlignCenter)
        self.cover_label.setFixedSize(200, 200)
        self.cover_label.setStyleSheet("border: 1px solid #ccc; background: #f0f0f0;")
        self.load_album_cover()
        layout.addWidget(self.cover_label)

        # Change cover button
        change_btn = QPushButton("Change Cover")
        change_btn.clicked.connect(self.change_front_cover)
        layout.addWidget(change_btn)

        return cover_widget

    def create_info_preview(self):
        """Create a preview of basic album info"""
        info_widget = QWidget()
        layout = QVBoxLayout(info_widget)
        layout.setSpacing(10)

        # Album title
        title_widget = self.field_widgets.get("album_name")
        if title_widget:
            title_widget.setStyleSheet("font-size: 18px; font-weight: bold;")
            layout.addWidget(title_widget)

        # Artist name (if available)
        if hasattr(self.album, "album_artists") and self.album.album_artists:
            artist_names = ", ".join(
                [artist.artist_name for artist in self.album.album_artists[:3]]
            )
            if len(self.album.album_artists) > 3:
                artist_names += "..."
            artist_label = QLabel(f"by {artist_names}")
            artist_label.setStyleSheet("font-size: 14px; color: #666;")
            layout.addWidget(artist_label)

        # Release year
        year_widget = self.field_widgets.get("release_year")
        if year_widget:
            year_layout = QHBoxLayout()
            year_layout.addWidget(QLabel("Release Year:"))
            year_layout.addWidget(year_widget)
            year_layout.addStretch()
            layout.addLayout(year_layout)

        layout.addStretch()
        return info_widget

    def create_metadata_tab(self):
        """Create the metadata tab using ALBUM_FIELDS mapping"""
        return self.tab_builder.build_metadata_tab()

    def create_tracks_tab(self):
        """Create the tracks tab with DiscManagementView"""
        tracks_widget = QWidget()
        layout = QVBoxLayout(tracks_widget)
        layout.setContentsMargins(10, 10, 10, 10)

        # Header
        header_label = QLabel("Album Tracks")
        header_label.setStyleSheet("font-size: 14px; font-weight: bold;")
        layout.addWidget(header_label)

        # Disc management view
        try:
            self.disc_view = DiscManagementView(
                self.album, self.controller, editable=True
            )
            layout.addWidget(self.disc_view)
        except Exception as e:
            logger.error(f"Error creating DiscManagementView: {e}")
            error_label = QLabel("Error loading track view. See logs for details.")
            error_label.setStyleSheet("color: red;")
            layout.addWidget(error_label)

        return tracks_widget

    def create_artwork_tab(self):
        """Create the artwork tab for managing cover images"""
        artwork_widget = QWidget()
        layout = QVBoxLayout(artwork_widget)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(15)

        # Front cover section
        front_group = QWidget()
        front_layout = QVBoxLayout(front_group)
        front_layout.addWidget(QLabel("Front Cover"))

        self.front_cover_display = QLabel()
        self.front_cover_display.setAlignment(Qt.AlignCenter)
        self.front_cover_display.setFixedSize(300, 300)
        self.front_cover_display.setStyleSheet(
            "border: 1px solid #ccc; background: #f0f0f0;"
        )
        front_layout.addWidget(self.front_cover_display)

        front_btn_layout = QHBoxLayout()
        change_front_btn = QPushButton("Change Front Cover")
        change_front_btn.clicked.connect(self.change_front_cover)
        clear_front_btn = QPushButton("Clear")
        clear_front_btn.clicked.connect(lambda: self.clear_cover("front"))
        front_btn_layout.addWidget(change_front_btn)
        front_btn_layout.addWidget(clear_front_btn)
        front_layout.addLayout(front_btn_layout)

        layout.addWidget(front_group)

        # Rear cover section
        rear_group = QWidget()
        rear_layout = QVBoxLayout(rear_group)
        rear_layout.addWidget(QLabel("Rear Cover"))

        self.rear_cover_display = QLabel()
        self.rear_cover_display.setAlignment(Qt.AlignCenter)
        self.rear_cover_display.setFixedSize(300, 300)
        self.rear_cover_display.setStyleSheet(
            "border: 1px solid #ccc; background: #f0f0f0;"
        )
        rear_layout.addWidget(self.rear_cover_display)

        rear_btn_layout = QHBoxLayout()
        change_rear_btn = QPushButton("Change Rear Cover")
        change_rear_btn.clicked.connect(self.change_rear_cover)
        clear_rear_btn = QPushButton("Clear")
        clear_rear_btn.clicked.connect(lambda: self.clear_cover("rear"))
        rear_btn_layout.addWidget(change_rear_btn)
        rear_btn_layout.addWidget(clear_rear_btn)
        rear_layout.addLayout(rear_btn_layout)

        layout.addWidget(rear_group)

        # Load existing images
        self.load_artwork_previews()

        layout.addStretch()
        return artwork_widget

    def create_advanced_tab(self):
        """Create the advanced settings tab"""
        advanced_widget = QWidget()
        layout = QVBoxLayout(advanced_widget)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(15)

        # ReplayGain section
        if "album_gain" in self.field_widgets:
            gain_layout = QHBoxLayout()
            gain_layout.addWidget(QLabel("Album Gain (dB):"))
            gain_layout.addWidget(self.field_widgets["album_gain"])
            gain_layout.addStretch()
            layout.addLayout(gain_layout)

        if "album_peak" in self.field_widgets:
            peak_layout = QHBoxLayout()
            peak_layout.addWidget(QLabel("Album Peak:"))
            peak_layout.addWidget(self.field_widgets["album_peak"])
            peak_layout.addStretch()
            layout.addLayout(peak_layout)

        # Status field
        if "status" in self.field_widgets:
            status_layout = QHBoxLayout()
            status_layout.addWidget(QLabel("Status:"))
            status_layout.addWidget(self.field_widgets["status"])
            status_layout.addStretch()
            layout.addLayout(status_layout)

        # Links section
        if "album_wikipedia_link" in self.field_widgets:
            wiki_layout = QHBoxLayout()
            wiki_layout.addWidget(QLabel("Wikipedia Link:"))
            wiki_layout.addWidget(self.field_widgets["album_wikipedia_link"])
            layout.addLayout(wiki_layout)

        layout.addStretch()
        return advanced_widget

    def add_dialog_buttons(self, layout):
        """Add Save and Cancel buttons"""
        button_box = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        button_box.accepted.connect(self.save_changes)
        button_box.rejected.connect(self.reject)

        # Add refresh button
        refresh_btn = button_box.addButton("Refresh", QDialogButtonBox.ActionRole)
        refresh_btn.clicked.connect(self.refresh_from_database)

        layout.addWidget(button_box)

    def setup_connections(self):
        """Setup signal connections"""
        # Connect any field-specific signals here
        # Example: sales field updating certification
        if "estimated_sales" in self.field_widgets:
            sales_widget = self.field_widgets["estimated_sales"]
            if hasattr(sales_widget, "valueChanged"):
                sales_widget.valueChanged.connect(self.update_certification_preview)

    # =========================================================================
    # Image Loading and Management
    # =========================================================================

    def load_album_cover(self):
        """Load and display the album cover in the header"""
        try:
            if hasattr(self.album, "front_cover_path") and self.album.front_cover_path:
                pixmap = QPixmap()

                # Handle both file paths and binary data
                if isinstance(self.album.front_cover_path, bytes):
                    if pixmap.loadFromData(self.album.front_cover_path):
                        scaled_pixmap = pixmap.scaled(
                            200, 200, Qt.KeepAspectRatio, Qt.SmoothTransformation
                        )
                        self.cover_label.setPixmap(scaled_pixmap)
                        return
                elif isinstance(self.album.front_cover_path, str):
                    if pixmap.load(self.album.front_cover_path):
                        scaled_pixmap = pixmap.scaled(
                            200, 200, Qt.KeepAspectRatio, Qt.SmoothTransformation
                        )
                        self.cover_label.setPixmap(scaled_pixmap)
                        return

            # Fallback: show placeholder
            self.cover_label.setText("No Cover\nImage")
            self.cover_label.setStyleSheet(
                "border: 1px solid #ccc; background: #f0f0f0; color: #999;"
            )

        except Exception as e:
            logger.error(f"Error loading album cover: {e}")
            self.cover_label.setText("Error\nLoading Cover")
            self.cover_label.setStyleSheet(
                "border: 1px solid #ccc; background: #f0f0f0; color: #ff0000;"
            )

    def load_artwork_previews(self):
        """Load artwork previews in the artwork tab"""
        # Front cover
        if hasattr(self.album, "front_cover_path") and self.album.front_cover_path:
            self.load_image_to_label(
                self.album.front_cover_path, self.front_cover_display, 300
            )
        else:
            self.front_cover_display.setText("No Front Cover")

        # Rear cover
        if hasattr(self.album, "rear_cover_path") and self.album.rear_cover_path:
            self.load_image_to_label(
                self.album.rear_cover_path, self.rear_cover_display, 300
            )
        else:
            self.rear_cover_display.setText("No Rear Cover")

    def load_image_to_label(self, image_data, label, size):
        """Load image data into a QLabel"""
        try:
            pixmap = QPixmap()

            if isinstance(image_data, bytes):
                pixmap.loadFromData(image_data)
            elif isinstance(image_data, str):
                pixmap.load(image_data)

            if not pixmap.isNull():
                scaled_pixmap = pixmap.scaled(
                    size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation
                )
                label.setPixmap(scaled_pixmap)
            else:
                label.setText("Invalid Image")
        except Exception as e:
            logger.error(f"Error loading image: {e}")
            label.setText("Error Loading Image")

    def change_front_cover(self):
        """Change the album front cover"""
        self.change_cover_image("front")

    def change_rear_cover(self):
        """Change the album rear cover"""
        self.change_cover_image("rear")

    def change_cover_image(self, cover_type):
        """Generic method to change cover images"""
        try:
            file_path, _ = QFileDialog.getOpenFileName(
                self,
                f"Select {cover_type.title()} Cover",
                "",
                "Image Files (*.png *.jpg *.jpeg *.bmp *.gif);;All files (*.*)",
            )

            if not file_path:
                return

            # Read the image file
            with open(file_path, "rb") as f:
                image_data = f.read()

            # Update the album object
            if cover_type == "front":
                self.album.front_cover_path = image_data
                self.load_album_cover()  # Update header
                self.load_image_to_label(image_data, self.front_cover_display, 300)
            elif cover_type == "rear":
                self.album.rear_cover_path = image_data
                self.load_image_to_label(image_data, self.rear_cover_display, 300)

        except Exception as e:
            logger.error(f"Error changing {cover_type} cover: {e}")
            QMessageBox.warning(self, "Error", f"Could not load image: {str(e)}")

    def clear_cover(self, cover_type):
        """Clear a cover image"""
        if cover_type == "front":
            self.album.front_cover_path = None
            self.cover_label.setText("No Cover\nImage")
            self.front_cover_display.setText("No Front Cover")
        elif cover_type == "rear":
            self.album.rear_cover_path = None
            self.rear_cover_display.setText("No Rear Cover")

    # =========================================================================
    # Data Management
    # =========================================================================

    def update_certification_preview(self):
        """Update RIAA certification preview based on sales"""
        # This would be implemented if you have a certification display
        # For now, it's a placeholder for future enhancement
        pass

    def refresh_view(self):
        """Refresh the view after relationship changes"""
        try:
            # Reload album data
            updated_album = self.controller.get.get_entity_object(
                "Album", album_id=self.album.album_id
            )
            if updated_album:
                self.album = updated_album

            # Reinitialize UI components that might have changed
            # This is called by RelationshipHelpers after modifications
            logger.info("Album view refreshed after relationship change")

        except Exception as e:
            logger.error(f"Error refreshing album view: {e}")
            QMessageBox.warning(self, "Error", "Could not refresh album data")

    def refresh_from_database(self):
        """Manually refresh all data from the database"""
        try:
            # Reload album from database
            self.album = self.controller.get.get_entity_object(
                "Album", album_id=self.album.album_id
            )

            # Reinitialize widgets with fresh data
            self.init_editable_widgets()

            # Reload UI
            self.init_ui()

            QMessageBox.information(
                self, "Refreshed", "Album data refreshed from database."
            )

        except Exception as e:
            logger.error(f"Error refreshing album data: {e}")
            QMessageBox.critical(self, "Error", f"Failed to refresh data: {str(e)}")

    def save_changes(self):
        """Save all changes to the database"""
        try:
            # Update album object from field widgets
            for field_name, widget in self.field_widgets.items():
                field_config = ALBUM_FIELDS.get(field_name)
                if field_config and field_config.editable:
                    value = AlbumUIComponents.get_field_value(widget, field_config.type)
                    setattr(self.album, field_name, value)

            # Save to database using controller
            success = self.controller.update.update_album(self.album)

            if success:
                QMessageBox.information(self, "Success", "Album updated successfully!")
                self.accept()  # Close the dialog
            else:
                QMessageBox.warning(
                    self, "Warning", "Album update returned no confirmation."
                )

        except Exception as e:
            logger.error(f"Error saving album changes: {e}")
            QMessageBox.critical(self, "Error", f"Failed to save changes: {str(e)}")

    def get_album_place_associations(self):
        """Get place associations for the album"""
        try:
            return (
                self.controller.get.get_all_entities(
                    "AlbumPlace", album_id=self.album.album_id
                )
                or []
            )
        except Exception as e:
            logger.error(f"Error loading album place associations: {e}")
            return []

    # =========================================================================
    # Utility Methods
    # =========================================================================

    @staticmethod
    def format_duration(seconds):
        """Format duration in seconds to readable string"""
        if not seconds:
            return "0:00"

        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)

        if hours > 0:
            return f"{hours}:{minutes:02d}:{secs:02d}"
        else:
            return f"{minutes}:{secs:02d}"

    def get_month_name(self, month_num):
        """Convert month number to month name"""
        months = [
            "January",
            "February",
            "March",
            "April",
            "May",
            "June",
            "July",
            "August",
            "September",
            "October",
            "November",
            "December",
        ]
        return months[month_num - 1] if 1 <= month_num <= 12 else ""
