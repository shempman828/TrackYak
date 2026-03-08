"""
album_tab.py  —  AlbumTabBuilder

Changes (this revision)
───────────────────────
• _build_artists_list: QGroupBox title font shrunk to 11 px; artist rows use
  compact margins so the tab doesn't feel overwhelmingly large.
• _build_publishers_section / _build_places_section: after any Remove action
  the parent dialog's _on_subdialog_closed() is NOT called here (the helper
  already calls refresh_view which triggers _rebuild_current_tab).  No change
  needed here for the reload-on-close requirement.
"""

from PySide6.QtGui import QFont
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from src.db_mapping_albums import ALBUM_FIELDS
from src.logger_config import logger


class AlbumTabBuilder:
    """Builder class for creating the different tabs in the album view"""

    def __init__(self, album_view):
        self.view = album_view
        self.album = album_view.album
        self.controller = album_view.controller
        self.helper = album_view.helper

    # =========================================================================
    # Public tab builders
    # =========================================================================

    def build_metadata_tab(self):
        """Build the technical metadata tab using field mapping"""
        tab = QWidget()
        layout = QFormLayout(tab)
        layout.setVerticalSpacing(10)

        desc_widget = self.view.field_widgets.get("album_description", QLineEdit())
        desc_widget.setText(self.album.album_description or "")
        layout.addRow("Description:", desc_widget)

        self._add_metadata_fields(layout)
        layout.addRow(QWidget(), QWidget())  # Spacer

        return tab

    def build_relationships_tab(self):
        """Build tab for publishers and place associations"""
        tab = QWidget()
        layout = QVBoxLayout(tab)

        layout.addWidget(self._build_publishers_section())
        layout.addWidget(self._build_places_section())
        layout.addStretch()
        return tab

    def build_awards_tab(self):
        """Build the awards tab with add functionality"""
        tab = QWidget()
        layout = QVBoxLayout(tab)

        add_btn = QPushButton("Add Album Award")
        add_btn.clicked.connect(self.helper.add_album_award)
        layout.addWidget(add_btn)

        layout.addWidget(self._build_awards_list())
        layout.addStretch()
        return tab

    def build_artists_tab(self):
        """Build the artists and credits tab"""
        tab = QWidget()
        layout = QVBoxLayout(tab)

        add_btn = QPushButton("Add Artist Credit")
        add_btn.clicked.connect(self.helper.add_artist_credit)
        layout.addWidget(add_btn)

        layout.addWidget(self._build_artists_list())
        layout.addStretch()
        return tab

    def build_statistics_tab(self):
        """Build the statistics and analytics tab"""
        tab = QWidget()
        layout = QFormLayout(tab)
        layout.setVerticalSpacing(10)
        self._add_statistics_fields(layout)
        layout.addRow(QWidget(), QWidget())  # Spacer
        return tab

    # =========================================================================
    # Internal section builders
    # =========================================================================

    def _add_metadata_fields(self, layout):
        """Add metadata fields to form layout"""
        if self.album.album_gain:
            gain_widget = self.view.field_widgets.get(
                "album_gain", QLabel(f"{self.album.album_gain:.2f} dB")
            )
            gain_widget.setText(f"{self.album.album_gain:.2f}")
            layout.addRow("Album Gain:", gain_widget)

        actual_track_count = len(self.album.tracks) if self.album.tracks else 0
        layout.addRow("Track Count:", QLabel(str(actual_track_count)))

        wiki_widget = self.view.field_widgets.get("album_wikipedia_link", QLineEdit())
        wiki_widget.setText(self.album.album_wikipedia_link or "")
        layout.addRow("Wikipedia:", wiki_widget)

        metadata_fields = ["album_language", "MBID", "status"]
        for field_name in metadata_fields:
            field_config = ALBUM_FIELDS.get(field_name)
            if not field_config:
                continue
            current_value = getattr(self.album, field_name, None)
            widget = self.view.field_widgets.get(field_name)
            if widget and current_value is not None:
                if isinstance(widget, (QLineEdit, QTextEdit)):
                    widget.setText(str(current_value))
                elif isinstance(widget, QSpinBox):
                    widget.setValue(int(current_value))
                elif isinstance(widget, QCheckBox):
                    widget.setChecked(bool(current_value))
                layout.addRow(f"{field_config.friendly}:", widget or QLineEdit())
            elif current_value:
                layout.addRow(f"{field_config.friendly}:", QLabel(str(current_value)))

    def _build_publishers_section(self):
        """Build publishers section"""
        group = QGroupBox("Publishers")
        layout = QVBoxLayout(group)

        album_publishers = self.controller.get.get_all_entities(
            "AlbumPublisher", album_id=self.album.album_id
        )

        if album_publishers:
            for album_publisher in album_publishers:
                publisher = self.controller.get.get_entity_object(
                    "Publisher", publisher_id=album_publisher.publisher_id
                )
                if publisher:
                    widget = QWidget()
                    widget_layout = QHBoxLayout(widget)
                    widget_layout.setContentsMargins(0, 0, 0, 0)
                    widget_layout.addWidget(QLabel(publisher.publisher_name))

                    remove_btn = QPushButton("Remove")
                    remove_btn.clicked.connect(
                        lambda checked, ap=album_publisher: (
                            self.helper.remove_publisher(ap)
                        )
                    )
                    widget_layout.addWidget(remove_btn)
                    layout.addWidget(widget)
        else:
            layout.addWidget(QLabel("No publishers associated"))

        add_btn = QPushButton("Add Publisher")
        add_btn.clicked.connect(self.helper.add_publisher)
        layout.addWidget(add_btn)

        return group

    def _build_places_section(self):
        """Build places section"""
        group = QGroupBox("Place Associations")
        layout = QVBoxLayout(group)

        place_associations = self.view.get_album_place_associations()

        if place_associations:
            for association in place_associations:
                place = self.controller.get.get_entity_object(
                    "Place", place_id=association.place_id
                )
                if place:
                    widget = QWidget()
                    widget_layout = QHBoxLayout(widget)
                    widget_layout.setContentsMargins(0, 0, 0, 0)

                    place_text = f"{place.place_name} ({association.association_type})"
                    widget_layout.addWidget(QLabel(place_text))

                    remove_btn = QPushButton("Remove")
                    remove_btn.clicked.connect(
                        lambda checked, a=association: self.helper.remove_place(a)
                    )
                    widget_layout.addWidget(remove_btn)
                    layout.addWidget(widget)
        else:
            layout.addWidget(QLabel("No place associations"))

        add_btn = QPushButton("Add Place Association")
        add_btn.clicked.connect(self.helper.add_place)
        layout.addWidget(add_btn)

        return group

    def _build_awards_list(self):
        """Build the awards list content"""
        try:
            award_associations = (
                self.controller.get.get_all_entities(
                    "AwardAssociation",
                    entity_id=self.album.album_id,
                    entity_type="Album",
                )
                or []
            )
            album_awards = [
                assoc.award for assoc in award_associations if assoc.award is not None
            ]
        except Exception as e:
            logger.error(f"Error loading album awards: {e}")
            album_awards = []

        if not album_awards:
            label = QLabel("No awards associated with this album.")
            label.setAlignment(Qt.AlignCenter)
            label.setStyleSheet("font-style: italic;")
            return label

        group = QGroupBox("Album Awards")
        layout = QVBoxLayout(group)

        for award in album_awards:
            layout.addWidget(self._build_award_widget(award))
            if award != album_awards[-1]:
                sep = QFrame()
                sep.setFrameShape(QFrame.HLine)
                sep.setFrameShadow(QFrame.Sunken)
                layout.addWidget(sep)

        return group

    def _build_award_widget(self, award):
        """Build individual award widget"""
        widget = QWidget()
        layout = QVBoxLayout(widget)

        try:
            award_name = getattr(award, "award_name", "Unknown Award")
            award_year = getattr(award, "award_year", None)
            award_category = getattr(award, "award_category", None)
            award_desc = getattr(award, "award_description", None)

            layout.addWidget(QLabel(f"<b>{award_name}</b>"))

            details_widget = QWidget()
            details_layout = QHBoxLayout(details_widget)
            if award_year:
                details_layout.addWidget(QLabel(f"Year: {award_year}"))
            if award_category:
                details_layout.addWidget(QLabel(f"Category: {award_category}"))
            details_layout.addStretch()
            layout.addWidget(details_widget)

            if award_desc:
                desc_label = QLabel(award_desc)
                desc_label.setWordWrap(True)
                layout.addWidget(desc_label)

            remove_btn = QPushButton("Remove Award")
            remove_btn.clicked.connect(
                lambda checked, a=award: self.helper.remove_album_award_association(a)
            )
            layout.addWidget(remove_btn)

        except Exception as e:
            logger.error(f"Error displaying award {award}: {e}")
            layout.addWidget(QLabel(f"Error displaying award: {str(e)}"))

        return widget

    def _build_artists_list(self):
        """Build the artists and credits list.

        FIX: Each role group now uses a smaller font (11 px) and tighter
        margins so the tab doesn't feel oversized.
        """
        if not hasattr(self.album, "album_roles") or not self.album.album_roles:
            return QLabel("No artist information available.")

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setSpacing(4)
        layout.setContentsMargins(0, 0, 0, 0)

        # Group roles by type
        roles_by_type: dict[str, list] = {}
        for role_assoc in self.album.album_roles:
            role_name = role_assoc.role.role_name if role_assoc.role else "Unknown Role"
            roles_by_type.setdefault(role_name, [])
            artist_name = (
                role_assoc.artist.artist_name if role_assoc.artist else "Unknown Artist"
            )
            roles_by_type[role_name].append((artist_name, role_assoc))

        # Smaller font for the group-box title
        small_font = QFont()
        small_font.setPointSize(9)

        for role_name, artist_tuples in roles_by_type.items():
            role_group = QGroupBox(role_name)
            role_group.setFont(small_font)  # ← shrinks the group title
            role_layout = QVBoxLayout(role_group)
            role_layout.setContentsMargins(6, 4, 6, 4)
            role_layout.setSpacing(2)

            for artist_name, role_assoc in sorted(artist_tuples, key=lambda x: x[0]):
                artist_widget = QWidget()
                artist_layout = QHBoxLayout(artist_widget)
                artist_layout.setContentsMargins(0, 0, 0, 0)
                artist_layout.setSpacing(6)

                name_label = QLabel(artist_name)
                name_label.setStyleSheet("font-size: 11px;")  # ← smaller text
                artist_layout.addWidget(name_label)

                remove_btn = QPushButton("Remove")
                remove_btn.setFixedHeight(22)  # ← compact button
                remove_btn.setStyleSheet("font-size: 10px; padding: 1px 6px;")
                remove_btn.clicked.connect(
                    lambda checked, ra=role_assoc: self.helper.remove_artist_credit(ra)
                )
                artist_layout.addWidget(remove_btn)
                artist_layout.addStretch()

                role_layout.addWidget(artist_widget)

            layout.addWidget(role_group)

        return container

    def _add_statistics_fields(self, layout):
        """Add statistics fields to form layout"""
        total_plays = getattr(self.album, "total_plays", 0)
        layout.addRow("Total Plays:", QLabel(f"{total_plays:,}"))

        avg_rating = getattr(self.album, "average_rating", None)
        if avg_rating:
            rating_stars = "★" * int(round(avg_rating))
            layout.addRow(
                "Average Rating:", QLabel(f"{rating_stars} ({avg_rating:.1f}/5)")
            )

        if self.album.tracks:
            rated_tracks = len([t for t in self.album.tracks if t.user_rating])
            played_tracks = len(
                [t for t in self.album.tracks if t.play_count and t.play_count > 0]
            )
            layout.addRow(
                "Rated Tracks:", QLabel(f"{rated_tracks}/{len(self.album.tracks)}")
            )
            layout.addRow(
                "Played Tracks:", QLabel(f"{played_tracks}/{len(self.album.tracks)}")
            )

        if self.album.total_duration:
            layout.addRow(
                "Total Duration:",
                QLabel(self.view.format_duration(self.album.total_duration)),
            )
            if self.album.tracks:
                avg_dur = self.album.total_duration / len(self.album.tracks)
                layout.addRow(
                    "Average Track Duration:",
                    QLabel(self.view.format_duration(avg_dur)),
                )
