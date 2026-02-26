from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QScrollArea, QSizePolicy, QVBoxLayout, QWidget

from src.artist_detail_awards import AwardsWidget
from src.artist_detail_bio import BioWidget
from src.artist_detail_container import SectionContainer
from src.artist_detail_credits import CreditsWidget
from src.artist_detail_header import HeaderWidget, PlacesWidget
from src.artist_detail_influences import InfluencesWidget
from src.artist_detail_member import MembershipWidget
from src.base_album_widget import ScrollableAlbumFlow
from src.logger_config import logger

SECTION_DEFS = [
    # Header section
    (
        "Header",
        {
            "factory": lambda artist, controller=None: HeaderWidget(artist, controller),
            "collapsed": False,  # Always visible
        },
    ),
    # Main biography text
    (
        "Biography",
        {
            "factory": lambda artist, controller=None: BioWidget(artist),
            "collapsed": True,
        },
    ),
    # Membership / groups or individual members
    (
        "Membership",
        {
            "factory": lambda artist, controller=None: MembershipWidget(artist),
            "collapsed": True,
        },
    ),
    # Discography (albums where artist is album artist)
    (
        "Discography",
        {
            "factory": lambda artist, controller=None: ScrollableAlbumFlow(
                artist.albums
            ),
            "collapsed": True,
        },
    ),
    # Credits section (roles, instruments, production, artwork)
    (
        "Credits",
        {
            "factory": lambda artist, controller=None: CreditsWidget(artist),
            "collapsed": True,
        },
    ),
    # Awards section
    (
        "Awards",
        {
            "factory": lambda artist, controller=None: AwardsWidget(artist),
            "collapsed": True,
        },
    ),
    (
        "Influences",
        {
            "factory": lambda artist, controller=None: InfluencesWidget(
                artist, controller
            ),
            "collapsed": True,
        },
    ),
    # Places associated with the artist (non-birth places)
    (
        "Places",
        {
            "factory": lambda artist, controller: PlacesWidget(artist, controller),
            "collapsed": True,
        },
    ),
]


class ArtistDetailTab(QWidget):
    """Detailed artist information tab with editing and extended functionality for both individuals and groups"""

    def __init__(self, artist: Any, controller: Any):
        super().__init__()
        self.artist = artist
        self.controller = controller  # access to db operations
        self.sections = {}
        self.init_ui()
        logger.info(f"Initialized ArtistDetailTab for artist: {artist.artist_name}")

    def init_ui(self):
        """Initialize the user interface."""
        # Main layout
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(10)

        # Create a scroll area for the content
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        # Create the scroll content widget
        scroll_content = QWidget()
        scroll_layout = QVBoxLayout(scroll_content)
        scroll_layout.setContentsMargins(5, 5, 5, 5)
        scroll_layout.setSpacing(15)

        # Set size policy for scroll content
        scroll_content.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.MinimumExpanding
        )

        # Create and add all sections
        for section_name, section_config in SECTION_DEFS:
            try:
                # Pass the controller to every factory as defined in SECTION_DEFS
                # This satisfies the (artist, controller) signature of the lambdas
                widget = section_config["factory"](self.artist, self.controller)

                if not widget:
                    continue

                # Create section container
                section_container = SectionContainer(
                    title=section_name,
                    collapsible=section_name not in ["Header", "Actions"],
                    collapsed=section_config.get("collapsed", True),
                )

                section_container.set_content(widget)
                self.sections[section_name] = section_container
                scroll_layout.addWidget(section_container)

                # Connect signals
                if section_container._collapsible:
                    section_container.toggle_button.toggled.connect(
                        lambda collapsed, name=section_name: self.on_section_toggled(
                            name, collapsed
                        )
                    )

            except Exception as e:
                logger.error(f"Error creating section '{section_name}': {e}")

        # Add stretch at the end to push content up
        scroll_layout.addStretch(1)

        # Set the scroll content
        scroll_area.setWidget(scroll_content)

        # Add scroll area to main layout
        main_layout.addWidget(scroll_area)

        # Set main widget properties
        self.setLayout(main_layout)
        self.setWindowTitle(f"Artist: {self.artist.artist_name}")

    def on_section_toggled(self, section_name: str, collapsed: bool):
        """Handle section collapse/expand events."""
        logger.debug(
            f"Section '{section_name}' {'collapsed' if collapsed else 'expanded'}"
        )

    def refresh_artist_data(self, artist: Any = None):
        """Refresh all sections with updated artist data."""
        if artist:
            self.artist = artist

        logger.info(f"Refreshing artist data for: {self.artist.artist_name}")

        # Refresh each section
        for section_name, container in self.sections.items():
            try:
                # Find the factory for this section
                section_config = next(
                    (config for name, config in SECTION_DEFS if name == section_name),
                    None,
                )

                if section_config:
                    # Recreate the widget passing both artist and controller
                    # This ensures ActionWidget gets its dependency back on refresh
                    new_widget = section_config["factory"](self.artist, self.controller)

                    # Update the container with the new widget
                    if new_widget:
                        container.set_content(new_widget)

            except Exception as e:
                logger.error(f"Error refreshing section '{section_name}': {e}")
                continue

    def get_section_widget(self, section_name: str):
        """Get a specific section widget by name."""
        container = self.sections.get(section_name)
        if container:
            # SectionContainer stores its content in content_layout
            if container.content_layout.count() > 0:
                return container.content_layout.itemAt(0).widget()
        return None

    def collapse_all_sections(self):
        """Collapse all collapsible sections."""
        for section_name, container in self.sections.items():
            if container._collapsible and container.toggle_button.isChecked():
                container.toggle_button.setChecked(False)

    def expand_all_sections(self):
        """Expand all collapsible sections."""
        for section_name, container in self.sections.items():
            if container._collapsible and not container.toggle_button.isChecked():
                container.toggle_button.setChecked(True)

    def save_changes(self):
        """Save any pending changes in the tab."""
        # Check if ActionWidget has a save method
        actions_widget = self.get_section_widget("Actions")
        if actions_widget and hasattr(actions_widget, "save_changes"):
            success = actions_widget.save_changes()
            if success:
                # Refresh all data after successful save
                self.refresh_artist_data()
            return success
        return False

    def is_modified(self) -> bool:
        """Check if any data in the tab has been modified."""
        # Check each section for modifications
        for section_name, container in self.sections.items():
            widget = self.get_section_widget(section_name)
            if widget and hasattr(widget, "is_modified"):
                if widget.is_modified():
                    return True
        return False

    def set_edit_mode(self, enabled: bool):
        """Enable or disable edit mode across all sections."""
        for section_name, container in self.sections.items():
            widget = self.get_section_widget(section_name)
            if widget and hasattr(widget, "set_edit_mode"):
                widget.set_edit_mode(enabled)
            elif widget and hasattr(widget, "setEnabled"):
                widget.setEnabled(enabled)

    def update_artist_image(self, image_path: str):
        """Update the artist's profile image in the header."""
        header_widget = self.get_section_widget("Header")
        if header_widget and hasattr(header_widget, "update_profile_image"):
            header_widget.update_profile_image(image_path)

    def update_artist_biography(self, biography: str):
        """Update the artist's biography."""
        bio_widget = self.get_section_widget("Biography")
        if bio_widget and hasattr(bio_widget, "set_biography"):
            bio_widget.set_biography(biography)

        # Also update the artist object
        self.artist.biography = biography
