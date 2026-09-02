# file: artist_detail_bio.py
import webbrowser

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from src.foundation.logger_config import logger


class BioWidget(QWidget):
    """Biography section widget with MBID, Wikipedia, and Website links"""

    def __init__(self, artist):
        super().__init__()
        self.artist = artist
        # True when there's nothing to show at all (no bio, no MBID, no
        # links). Callers use this to drop the widget entirely rather than
        # leave an empty box eating layout space beside the infobox.
        self.is_empty = not any(
            getattr(self.artist, attr, "")
            for attr in ("biography", "MBID", "wikipedia_link", "website_link")
        )
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(16)

        # --- Biography Text ---
        # When there's no biography we render nothing here (no placeholder,
        # no separator) so the section doesn't waste vertical space beside
        # the infobox.
        biography = getattr(self.artist, "biography", "")
        if biography:
            self.bio_label = QLabel(biography)
            self.bio_label.setObjectName("BiographyText")
            self.bio_label.setWordWrap(True)
            self.bio_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            layout.addWidget(self.bio_label)

        mbid = getattr(self.artist, "MBID", "")
        wikipedia = getattr(self.artist, "wikipedia_link", "")
        website = getattr(self.artist, "website_link", "")

        # Separator — only when it actually divides bio text from a links row
        if biography and (mbid or wikipedia or website):
            separator = QFrame()
            separator.setFrameShape(QFrame.HLine)
            separator.setFrameShadow(QFrame.Sunken)
            separator.setMaximumHeight(1)
            layout.addWidget(separator)

        # --- External Links Section ---
        links_layout = QHBoxLayout()
        links_layout.setSpacing(12)

        # MBID
        if mbid:
            mbid_container = QWidget()
            mbid_layout = QVBoxLayout(mbid_container)
            mbid_layout.setContentsMargins(0, 0, 0, 0)

            mbid_label = QLabel("MusicBrainz ID")
            mbid_label.setObjectName("LinkLabel")

            mbid_value = QLabel(mbid)
            mbid_value.setObjectName("MbidValue")
            mbid_value.setTextInteractionFlags(Qt.TextSelectableByMouse)

            mbid_layout.addWidget(mbid_label)
            mbid_layout.addWidget(mbid_value)
            links_layout.addWidget(mbid_container)

        # Wikipedia Link
        if wikipedia:
            wikipedia_btn = QPushButton("🌐 Wikipedia")
            wikipedia_btn.setObjectName("WikipediaButton")
            wikipedia_btn.setCursor(Qt.PointingHandCursor)
            wikipedia_btn.clicked.connect(lambda: self.open_link(wikipedia))
            links_layout.addWidget(wikipedia_btn)

        # Website Link
        if website:
            website_btn = QPushButton("🔗 Website")
            website_btn.setObjectName("WebsiteButton")
            website_btn.setCursor(Qt.PointingHandCursor)
            website_btn.clicked.connect(lambda: self.open_link(website))
            links_layout.addWidget(website_btn)

        links_layout.addStretch()

        # Only add links section if we have any links
        if mbid or wikipedia or website:
            layout.addLayout(links_layout)

    def open_link(self, url):
        """Open external link in default browser"""
        try:
            webbrowser.open(url)
        except (OSError, webbrowser.Error) as e:
            logger.error(f"Failed to open URL {url}: {e}")
