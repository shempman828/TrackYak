from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
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


class AlbumDetailView(QWidget):
    """main album detail view"""

    def __init__(self, album, controller, parent=None, editable=False):
        super().__init__(parent)
        self.album = album
        self.controller = controller
        self.editable = editable
        self.helper = RelationshipHelpers(controller, album, self.show_updated_view)
        self.field_widgets = {}
        self.tab_builder = AlbumTabBuilder(self)

        if self.editable:
            self.init_editable_widgets()

        self.init_ui()

    def init_editable_widgets(self):
        """Initialize editable widgets based on ALBUM_FIELDS mapping"""
        for field_name, field_config in ALBUM_FIELDS.items():
            if not field_config.editable:
                continue
            self.field_widgets[field_name] = AlbumUIComponents.create_editable_field(
                field_config, getattr(self.album, field_name, None)
            )

    def init_ui(self):
        """Initialize the main UI"""
        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(15, 15, 15, 15)
        main_layout.setSpacing(15)

        # Create scroll area
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QScrollArea.NoFrame)

        # Main content
        content_widget = QWidget()
        content_layout = QVBoxLayout(content_widget)
        content_layout.setSpacing(20)

        # Header section
        content_layout.addWidget(self.create_header_section())

        # Tabs
        tabs = QTabWidget()
        tabs.addTab(self.create_discs_tab(), "Tracks")
        tabs.addTab(self.tab_builder.build_metadata_tab(), "Metadata")
        tabs.addTab(self.tab_builder.build_artists_tab(), "Artist Credits")
        tabs.addTab(self.tab_builder.build_relationships_tab(), "Publishers && Places")
        tabs.addTab(self.tab_builder.build_awards_tab(), "Awards")
        tabs.addTab(self.tab_builder.build_statistics_tab(), "Statistics")

        content_layout.addWidget(tabs)
        content_layout.addStretch()

        scroll_area.setWidget(content_widget)
        main_layout.addWidget(scroll_area)

        # Edit/save buttons if in editable mode
        if self.editable:
            self.add_edit_buttons(main_layout)

        self.setLayout(main_layout)
        self.adjustSize()
        self.resize(
            self.sizeHint().boundedTo(
                QApplication.primaryScreen().availableGeometry().size() * 0.9
            )
        )

    def create_header_section(self):
        """Create the header section with album cover, title, and basic info"""
        header_widget = QWidget()
        header_layout = QHBoxLayout(header_widget)
        header_layout.setSpacing(20)

        # Album cover
        cover_widget = self.create_cover_section()
        header_layout.addWidget(cover_widget)

        # Album info
        info_widget = self.create_info_section()
        header_layout.addWidget(info_widget, 1)

        return header_widget

    def create_cover_section(self):
        """Create the album cover section"""
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

        # Change cover button (editable mode only)
        if self.editable:
            change_btn = QPushButton("Change Cover")
            change_btn.clicked.connect(self.change_front_cover_path)
            layout.addWidget(change_btn)

        return cover_widget

    def create_info_section(self):
        """Create the album information section"""
        info_widget = QWidget()
        layout = QVBoxLayout(info_widget)
        layout.setSpacing(10)

        # Album title
        if self.editable:
            title_widget = self.field_widgets.get("album_name", QLineEdit())
            title_widget.setText(self.album.album_name or "")
            title_widget.setStyleSheet("font-size: 18px; font-weight: bold;")
            layout.addWidget(title_widget)
        else:
            title_label = QLabel(self.album.album_name or "Unknown Album")
            title_label.setStyleSheet("font-size: 18px; font-weight: bold;")
            title_label.setWordWrap(True)
            layout.addWidget(title_label)

        # Artist name
        if hasattr(self.album, "artist") and self.album.artist:
            artist_label = QLabel(f"by {self.album.artist.artist_name}")
            artist_label.setStyleSheet("font-size: 14px; color: #666;")
            layout.addWidget(artist_label)

        # Release year and format
        details_widget = QWidget()
        details_layout = QHBoxLayout(details_widget)
        details_layout.setContentsMargins(0, 0, 0, 0)

        if self.album.release_year:
            year_label = QLabel(str(self.album.release_year))
            details_layout.addWidget(year_label)

        # RIAA certification (editable preview)
        if self.editable:
            # Make sure RIAA_certification widget exists
            if "RIAA_certification" not in self.field_widgets:
                # Create it if it doesn't exist
                from src.album_components import AlbumUIComponents

                field_config = ALBUM_FIELDS.get("RIAA_certification")
                if field_config:
                    self.field_widgets["RIAA_certification"] = (
                        AlbumUIComponents.create_editable_field(
                            field_config,
                            getattr(self.album, "RIAA_certification", None),
                        )
                    )

            riaa_section = self.create_riaa_section()
            layout.addWidget(riaa_section)

        layout.addStretch()
        return info_widget

    def create_riaa_section(self):
        """Create RIAA certification section for editable mode"""
        # Create container widget
        container_widget = QWidget()
        layout = QHBoxLayout(container_widget)
        layout.setContentsMargins(0, 0, 0, 0)

        # RIAA certification label
        riaa_label = QLabel("RIAA Certification:")
        layout.addWidget(riaa_label)

        # Get the editable field widget
        riaa_field = self.field_widgets.get("RIAA_certification")
        if not riaa_field:
            riaa_field = QLineEdit()

        if self.album.RIAA_certification:
            riaa_field.setText(self.album.RIAA_certification)

        riaa_field.textChanged.connect(self.update_riaa_preview)
        layout.addWidget(riaa_field)

        # Preview label
        self.riaa_preview = QLabel()
        layout.addWidget(self.riaa_preview)
        self.update_riaa_preview()

        return container_widget

    def add_edit_buttons(self, layout):
        """Add edit/save buttons"""
        button_layout = QHBoxLayout()
        save_btn = QPushButton("Save Changes")
        save_btn.clicked.connect(self.save_changes)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(
            lambda: self.parent().reject() if self.parent() else self.close()
        )
        button_layout.addWidget(save_btn)
        button_layout.addWidget(cancel_btn)
        layout.addLayout(button_layout)

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

    def load_album_cover(self):
        """Load and display the album cover image"""
        try:
            if hasattr(self.album, "front_cover_path") and self.album.front_cover_path:
                pixmap = QPixmap()
                if pixmap.loadFromData(self.album.front_cover_path):
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

    def change_front_cover_path(self):
        """Change the album cover image"""
        try:
            file_path, _ = QFileDialog.getOpenFileName(
                self,
                "Select Album Cover",
                "",
                "Image Files (*.png *.jpg *.jpeg *.bmp *.gif)",
                options=QFileDialog.Option.DontUseNativeDialog,  # Add this
            )

            if file_path:
                with open(file_path, "rb") as f:
                    image_data = f.read()

                # Update the album object
                self.album.front_cover_path = image_data

                # Update the display
                self.load_album_cover()

        except Exception as e:
            logger.error(f"Error changing cover image: {e}")
            QMessageBox.warning(self, "Error", f"Could not load image: {str(e)}")

    def show_updated_view(self):
        """Refresh the view after changes"""
        try:
            # Reload album data
            updated_album = self.controller.get.get_entity_object(
                "Album", album_id=self.album.album_id
            )
            if updated_album:
                self.album = updated_album

            # Reinitialize UI
            self.init_ui()

        except Exception as e:
            logger.error(f"Error refreshing album view: {e}")
            QMessageBox.warning(self, "Error", "Could not refresh album data")

    def save_changes(self):
        """Save all changes made in editable mode"""
        try:
            # Update album object from field widgets
            for field_name, widget in self.field_widgets.items():
                field_config = ALBUM_FIELDS.get(field_name)
                if field_config and field_config.editable:
                    value = AlbumUIComponents.get_field_value(widget, field_config.type)
                    setattr(self.album, field_name, value)

            # Calculate RIAA certification if needed
            if (
                hasattr(self.album, "RIAA_certification")
                and self.album.RIAA_certification
            ):
                self.album.RIAA_certification = self.calculate_RIAA_certification(
                    self.album.RIAA_certification
                )

            # Save to database
            success = self.controller.update.update_album(self.album)

            if success:
                QMessageBox.information(self, "Success", "Album updated successfully!")
                self.show_updated_view()
            else:
                QMessageBox.warning(self, "Error", "Failed to update album")

        except Exception as e:
            logger.error(f"Error saving album changes: {e}")
            QMessageBox.critical(self, "Error", f"Could not save changes: {str(e)}")

    def calculate_RIAA_certification(self, certification_text):
        """Calculate and format RIAA certification"""
        try:
            # Simple implementation - you might want to expand this
            certification_text = certification_text.upper().strip()

            # Basic validation
            valid_prefixes = ["GOLD", "PLATINUM", "DIAMOND", "MULTI-PLATINUM"]
            for prefix in valid_prefixes:
                if prefix in certification_text:
                    return certification_text

            # If no valid prefix found, return as is
            return certification_text

        except Exception as e:
            logger.error(f"Error calculating RIAA certification: {e}")
            return certification_text

    def update_riaa_preview(self):
        """Update RIAA certification preview"""
        try:
            if hasattr(self, "riaa_preview"):
                riaa_widget = self.field_widgets.get("RIAA_certification")
                if riaa_widget:
                    text = riaa_widget.text()
                    calculated = self.calculate_RIAA_certification(text)
                    self.riaa_preview.setText(f"Preview: {calculated}")

        except Exception as e:
            logger.error(f"Error updating RIAA preview: {e}")

    def create_discs_tab(self):
        """Create a tab showing disc structure using DiscManagementView"""
        tab_widget = QWidget()
        layout = QVBoxLayout(tab_widget)
        layout.setContentsMargins(0, 0, 0, 0)

        # Create the disc management view
        disc_view = DiscManagementView(self.album, self.controller, parent=self)
        layout.addWidget(disc_view)

        return tab_widget

    def format_duration(self, seconds):
        """Format duration in seconds to a readable string (MM:SS or HH:MM:SS)"""
        if not seconds:
            return "0:00"

        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)

        if hours > 0:
            return f"{hours}:{minutes:02d}:{secs:02d}"
        else:
            return f"{minutes}:{secs:02d}"
