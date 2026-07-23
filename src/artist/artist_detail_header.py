from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from src.artist.artist_detail_alias import AliasesCarousel
from src.artist.artist_detail_bio import BioWidget
from src.artist.artist_detail_dates import DateDisplayWidget
from src.core.logger_config import logger
from src.image.pixmap_with_fallback import load_pixmap_with_fallback

# Bounding box for the portrait — the actual photo is scaled to fit inside
# it while keeping its own aspect ratio, not stretched or cropped square.
PORTRAIT_MAX_SIZE = QSize(160, 160)
INFOBOX_WIDTH = 220


class PlacesWidget(QWidget):
    """Widget to display birth and death places"""

    def __init__(self, artist, controller, parent=None):
        super().__init__(parent)
        self.artist = artist
        self.controller = controller
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        # Get places from database
        try:
            associations = (
                self.controller.get.get_all_entities(
                    "PlaceAssociation",
                    entity_id=self.artist.artist_id,
                    entity_type="Artist",
                )
                or []
            )

            birth_places = []
            death_places = []
            other_places = []

            for assoc in associations:
                if not assoc.place:
                    continue
                type_name = (
                    assoc.association_type.type_name if assoc.association_type else ""
                )
                if type_name == "Birthplace":
                    birth_places.append(assoc.place)
                elif type_name == "Deathplace":
                    death_places.append(assoc.place)
                else:
                    other_places.append(assoc.place)

            # Display birth place
            if birth_places:
                birth_text = "Born in: " + ", ".join(
                    [p.place_name for p in birth_places[:3]]
                )
                if len(birth_places) > 3:
                    birth_text += f" (+{len(birth_places) - 3} more)"
                birth_label = QLabel(birth_text)
                birth_label.setObjectName("PlaceLabel")
                birth_label.setWordWrap(True)
                layout.addWidget(birth_label)

            # Display death place
            if death_places:
                death_text = "Died in: " + ", ".join(
                    [p.place_name for p in death_places[:3]]
                )
                if len(death_places) > 3:
                    death_text += f" (+{len(death_places) - 3} more)"
                death_label = QLabel(death_text)
                death_label.setObjectName("PlaceLabel")
                death_label.setWordWrap(True)
                layout.addWidget(death_label)

            # Display other significant places (if any and space permits)
            if other_places and not (birth_places or death_places):
                places_text = "Associated with: " + ", ".join(
                    [p.place_name for p in other_places[:2]]
                )
                if len(other_places) > 2:
                    places_text += f" (+{len(other_places) - 2} more)"
                places_label = QLabel(places_text)
                places_label.setObjectName("PlaceLabel")
                places_label.setWordWrap(True)
                layout.addWidget(places_label)

        except Exception as e:
            logger.error(f"Error loading places: {e}")


class ArtistInfobox(QGroupBox):
    """Wikipedia-style infobox: portrait plus at-a-glance facts, anchored
    beside the article title in HeaderWidget."""

    def __init__(self, artist, controller=None, parent=None):
        super().__init__(parent)
        self.artist = artist
        self.controller = controller
        self.setObjectName("ArtistInfobox")
        self.setFixedWidth(INFOBOX_WIDTH)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        # Portrait, centered
        portrait = QLabel()
        portrait.setObjectName("ArtistPortrait")
        portrait.setMaximumSize(PORTRAIT_MAX_SIZE)
        portrait.setAlignment(Qt.AlignCenter)
        portrait.setPixmap(
            load_pixmap_with_fallback(
                getattr(self.artist, "profile_pic_path", "") or "",
                PORTRAIT_MAX_SIZE,
            )
        )
        portrait_row = QHBoxLayout()
        portrait_row.addStretch()
        portrait_row.addWidget(portrait)
        portrait_row.addStretch()
        layout.addLayout(portrait_row)

        # Type badge, centered
        type_names = sorted(t.type_name for t in (self.artist.types or []))
        type_badge = QLabel(", ".join(type_names) or "Artist")
        type_badge.setObjectName("TypeBadge")
        type_badge.setAlignment(Qt.AlignCenter)
        type_badge.setFixedHeight(24)
        type_badge.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        layout.addWidget(type_badge, alignment=Qt.AlignHCenter)

        # Dates / age
        has_date_data = any(
            [
                self.artist.begin_year,
                self.artist.end_year,
                self.artist.begin_month,
                self.artist.end_month,
                self.artist.begin_day,
                self.artist.end_day,
            ]
        )
        if has_date_data:
            layout.addWidget(DateDisplayWidget(self.artist))

        # Places
        if hasattr(self.artist, "places") and self.controller:
            try:
                if self.artist.places:
                    layout.addWidget(PlacesWidget(self.artist, self.controller))
            except Exception as e:
                logger.error(f"Error accessing places: {e}")

        # Gender
        if hasattr(self.artist, "gender") and self.artist.gender:
            gender_label = QLabel(f"Gender: {self.artist.gender}")
            gender_label.setObjectName("InfoLabel")
            layout.addWidget(gender_label)

        # Group indicator
        if hasattr(self.artist, "isgroup") and self.artist.isgroup == 1:
            group_label = QLabel("🎵 Group")
            group_label.setObjectName("GroupLabel")
            layout.addWidget(group_label)

        # Track count
        track_count = getattr(self.artist, "track_count", 0)
        if track_count > 0:
            tracks_label = QLabel(f"Tracks: {track_count}")
            tracks_label.setObjectName("InfoLabel")
            layout.addWidget(tracks_label)


class HeaderWidget(QWidget):
    """Article lead section: title, aliases, and biography beside an
    at-a-glance infobox."""

    def __init__(self, artist, controller=None):
        super().__init__()
        self.artist = artist
        self.controller = controller
        self.aliases_widget = None
        self.init_ui()

    def init_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(20)

        # --- Left: article title ---
        title_col = QVBoxLayout()
        title_col.setSpacing(8)

        name_row = QHBoxLayout()
        name_row.setSpacing(8)

        self.name_label = QLabel(self.artist.artist_name)
        self.name_label.setObjectName("ArtistName")
        self.name_label.setWordWrap(True)
        name_row.addWidget(self.name_label)

        career_span = getattr(self.artist, "career_span", None)
        if career_span:
            self.years_label = QLabel(f"({career_span})")
            self.years_label.setObjectName("ArtistYears")
            name_row.addWidget(self.years_label)

        name_row.addStretch()
        title_col.addLayout(name_row)

        aliases = getattr(self.artist, "aliases_list", []) or []
        if aliases:
            self.aliases_widget = AliasesCarousel(aliases)
            title_col.addWidget(self.aliases_widget)

        # Lead biography flows here, beside the infobox, rather than
        # waiting below it — matches how a wiki article's lead paragraph
        # wraps around its infobox instead of leaving it stranded.
        title_col.addWidget(BioWidget(self.artist), 1)
        layout.addLayout(title_col, 1)

        # --- Right: infobox ---
        layout.addWidget(ArtistInfobox(self.artist, self.controller), 0)

    def cleanup(self):
        """Clean up resources when widget is destroyed"""
        if self.aliases_widget:
            self.aliases_widget.stop()
