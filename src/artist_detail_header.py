from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from src.artist_detail_alias import AliasesCarousel
from src.artist_detail_dates import DateDisplayWidget
from src.logger_config import logger


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
            places = self.artist.places if hasattr(self.artist, "places") else []

            # Separate birth and death places (you might need to adjust based on your schema)
            birth_places = []
            death_places = []
            other_places = []

            for place in places:
                # You'll need to determine how to identify birth/death places
                # This is a placeholder - adjust based on your Place model
                if hasattr(place, "association_type"):
                    if place.association_type == "birth":
                        birth_places.append(place)
                    elif place.association_type == "death":
                        death_places.append(place)
                    else:
                        other_places.append(place)
                else:
                    other_places.append(place)

            # Display birth place
            if birth_places:
                birth_text = "Born in: " + ", ".join(
                    [p.place_name for p in birth_places[:3]]
                )
                if len(birth_places) > 3:
                    birth_text += f" (+{len(birth_places) - 3} more)"
                birth_label = QLabel(birth_text)
                birth_label.setObjectName("PlaceLabel")
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
                layout.addWidget(places_label)

        except Exception as e:
            logger.error(f"Error loading places: {e}")


class HeaderWidget(QWidget):
    """Main header widget for artist detail pane"""

    def __init__(self, artist, controller=None):
        super().__init__()
        self.artist = artist
        self.controller = controller
        self.init_ui()
        self.apply_styles()

    def init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(16, 16, 16, 16)
        main_layout.setSpacing(12)

        # Artist name (very prominent)
        self.name_label = QLabel(self.artist.artist_name)
        self.name_label.setObjectName("ArtistName")

        # Artist type badge
        type_badge = QLabel(self.artist.artist_type or "Artist")
        type_badge.setObjectName("TypeBadge")
        type_badge.setAlignment(Qt.AlignCenter)
        type_badge.setFixedHeight(24)
        type_badge.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

        # Name and type row
        name_row = QHBoxLayout()
        name_row.addWidget(self.name_label)
        name_row.addWidget(type_badge)
        name_row.addStretch()

        main_layout.addLayout(name_row)

        # Aliases section
        aliases = getattr(self.artist, "aliases_list", []) or []
        if aliases:
            self.aliases_widget = AliasesCarousel(aliases)
            main_layout.addWidget(self.aliases_widget)

        # Separator
        separator1 = QFrame()
        separator1.setFrameShape(QFrame.HLine)
        separator1.setFrameShadow(QFrame.Sunken)
        main_layout.addWidget(separator1)

        # Date information (only if we have any date data)
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
            self.date_widget = DateDisplayWidget(self.artist)
            main_layout.addWidget(self.date_widget)

        # Places information
        if hasattr(self.artist, "places") and self.controller:
            try:
                # Check if there are any places
                places = self.artist.places
                if places:
                    self.places_widget = PlacesWidget(self.artist, self.controller)
                    main_layout.addWidget(self.places_widget)
            except Exception as e:
                logger.error(f"Error accessing places: {e}")

        # Group/Individual info
        info_row = QHBoxLayout()

        # Gender/Group status
        if hasattr(self.artist, "gender") and self.artist.gender:
            gender_label = QLabel(f"Gender: {self.artist.gender}")
            gender_label.setObjectName("InfoLabel")
            info_row.addWidget(gender_label)

        # Group indicator
        if hasattr(self.artist, "isgroup") and self.artist.isgroup == 1:
            group_label = QLabel("🎵 Group")
            group_label.setObjectName("GroupLabel")
            info_row.addWidget(group_label)

        info_row.addStretch()

        # Track count
        track_count = getattr(self.artist, "track_count", 0)
        if track_count > 0:
            tracks_label = QLabel(f"Tracks: {track_count}")
            tracks_label.setObjectName("InfoLabel")
            info_row.addWidget(tracks_label)

        main_layout.addLayout(info_row)

    def apply_styles(self):
        """Apply custom styles to the header"""
        self.setStyleSheet("""
            #ArtistName {
                font-size: 28px;
                font-weight: bold;
                color: #333333;
            }

            #TypeBadge {
                background-color: #e0e0e0;
                color: #666666;
                padding: 2px 8px;
                border-radius: 12px;
                font-size: 12px;
                font-weight: 500;
                margin-left: 8px;
            }

            #AliasLabel {
                font-size: 14px;
                color: #666666;
                font-style: italic;
            }

            #DateLabel {
                font-size: 14px;
                color: #444444;
            }

            #AgeLabel {
                font-size: 13px;
                color: #666666;
                font-weight: 500;
            }

            #PlaceLabel {
                font-size: 13px;
                color: #555555;
            }

            #InfoLabel {
                font-size: 12px;
                color: #666666;
                padding: 2px 6px;
                background-color: #f5f5f5;
                border-radius: 4px;
            }

            #GroupLabel {
                font-size: 12px;
                color: #ffffff;
                background-color: #4a6fa5;
                padding: 2px 8px;
                border-radius: 4px;
                font-weight: 500;
            }

            QFrame {
                color: #dddddd;
            }
        """)

    def cleanup(self):
        """Clean up resources when widget is destroyed"""
        if hasattr(self, "aliases_widget"):
            self.aliases_widget.stop()
