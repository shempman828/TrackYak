from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)
from sqlalchemy.exc import SQLAlchemyError

from src.album.base_album_widget import ScrollableAlbumFlow
from src.artist.artist_detail_awards import AwardsWidget
from src.artist.artist_detail_credits import CreditsWidget
from src.artist.artist_detail_header import HeaderWidget
from src.artist.artist_detail_influences import InfluencesWidget
from src.artist.artist_detail_member import MembershipWidget
from src.foundation.logger_config import logger


class ArtistDetailTab(QWidget):
    """Read-only article view of an artist: an infobox header, a lead
    biography, and whichever sections the artist actually has data for."""

    def __init__(self, artist: Any, controller: Any):
        super().__init__()
        self.artist = artist
        self.controller = controller
        self._scroll_area = None
        self.init_ui()
        logger.info(f"Initialized ArtistDetailTab for artist: {artist.artist_name}")

    def init_ui(self):
        """Initialize the user interface."""
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(10)

        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._scroll_area = scroll_area

        article = QWidget()
        article_layout = QVBoxLayout(article)
        article_layout.setContentsMargins(5, 5, 5, 5)
        article_layout.setSpacing(18)
        article.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.MinimumExpanding)

        # Title, aliases, and lead biography, beside an at-a-glance infobox
        article_layout.addWidget(HeaderWidget(self.artist, self.controller))

        # Whatever sections the artist actually has content for, in order
        sections = self._build_sections()
        if sections:
            article_layout.addLayout(self._build_toc(sections))
        for _heading, block in sections:
            article_layout.addWidget(block)

        article_layout.addStretch(1)

        scroll_area.setWidget(article)
        main_layout.addWidget(scroll_area)

        self.setLayout(main_layout)
        self.setWindowTitle(f"Artist: {self.artist.artist_name}")

    def _build_sections(self) -> list[tuple[str, QWidget]]:
        """Build (heading, section block) for every section with content.

        Content widgets follow the existing convention of calling
        `self.setVisible(False)` on themselves when the artist has nothing
        to show for that section; that flag only reads reliably once the
        widget has a parent, so we check it after wrapping in a section
        block rather than before.
        """
        candidates = []

        if self.artist.albums:
            candidates.append(("Discography", lambda: ScrollableAlbumFlow(self.artist.albums)))

        candidates += [
            ("Membership", lambda: MembershipWidget(self.artist)),
            ("Credits", lambda: CreditsWidget(self.artist, self.controller)),
            ("Awards", lambda: AwardsWidget(self.artist, self.controller)),
            ("Influences", lambda: InfluencesWidget(self.artist, self.controller)),
        ]

        sections = []
        for heading, build in candidates:
            try:
                content = build()
            except (SQLAlchemyError, AttributeError) as e:
                logger.error(f"Error building '{heading}' section: {e}")
                continue

            if content is None:
                continue

            block = self._section_block(heading, content)
            if content.isHidden():
                block.deleteLater()
                continue

            sections.append((heading, block))

        return sections

    def _section_block(self, heading: str, content: QWidget) -> QWidget:
        """Wrap a content widget in a plain, always-expanded article section."""
        block = QWidget()
        block.setObjectName("ArticleSection")
        layout = QVBoxLayout(block)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        title = QLabel(heading)
        title.setObjectName("SectionHeading")
        layout.addWidget(title)
        layout.addWidget(content)

        return block

    def _build_toc(self, sections: list[tuple[str, QWidget]]) -> QHBoxLayout:
        """A row of jump-to-section chips for the sections that were built."""
        toc = QHBoxLayout()
        toc.setSpacing(6)

        label = QLabel("Jump to:")
        label.setObjectName("TocLabel")
        toc.addWidget(label)

        for heading, block in sections:
            chip = QPushButton(heading)
            chip.setObjectName("TocChip")
            chip.setFlat(True)
            chip.setCursor(Qt.PointingHandCursor)
            chip.clicked.connect(lambda _checked=False, b=block: self._scroll_to(b))
            toc.addWidget(chip)

        toc.addStretch()
        return toc

    def _scroll_to(self, widget: QWidget):
        if self._scroll_area:
            self._scroll_area.ensureWidgetVisible(widget)
