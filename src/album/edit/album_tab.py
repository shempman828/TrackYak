"""Builds the Album credit, Publishers & Places and Awards tabs for the album editor."""

import html

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QFrame, QGroupBox, QHBoxLayout, QLabel, QPushButton, QScrollArea, QSizePolicy, QVBoxLayout, QWidget
from sqlalchemy.exc import SQLAlchemyError

from src.common.widgets.entity_completer_context import artist_context_map
from src.common.widgets.entity_completer_edit import build_entity_search_widget
from src.common.widgets.flow_layout import FlowLayout
from src.common.widgets.qt_text import esc_amp
from src.foundation.display_settings import apply_scaled_style
from src.foundation.logger_config import logger
from src.track.edit.track_edit_places import PlacesTab as TrackPlacesTab

# Prefixes that mark a role as a variant of a base role, e.g. "Assistant
# Producer" / "Additional Producer" are both variants of "Producer".
_ROLE_VARIANT_PREFIXES = ("Additional ", "Assistant ")


def _role_group_sort_key(name: str) -> tuple:
    """Sort key: "Album Artist" first, then base roles A-Z with their Additional/Assistant variants."""
    if name == "Album Artist":
        return (0, "", 0, "")

    base_name = name
    variant_rank = 0
    for prefix in _ROLE_VARIANT_PREFIXES:
        if name.startswith(prefix) and len(name) > len(prefix):
            base_name = name[len(prefix) :]
            variant_rank = 1
            break

    return (1, base_name.lower(), variant_rank, name.lower())


def _placeholder_label(text: str) -> QLabel:
    """Return an empty-state label in the app's placeholder text style."""
    label = QLabel(text)
    label.setProperty("textRole", "placeholder")
    return label


def _remove_button(text: str, accessible_name: str) -> QPushButton:
    """Return a danger-styled remove button with an accessible name."""
    button = QPushButton(text)
    button.setProperty("danger", True)
    button.setAccessibleName(accessible_name)
    return button


class _CreditFlowArea(QWidget):
    """Container for a role group's FlowLayout of credit chips that reserves height for wrapped rows."""

    # Same workaround as artist_edit_types._ChipArea: Qt cannot know a FlowLayout's
    # wrapped height ahead of time, so minimumHeight is recomputed on every resize.

    def resizeEvent(self, event):
        """Recompute the reserved height for the new width."""
        super().resizeEvent(event)
        self.refresh_height()

    def refresh_height(self):
        """Set minimumHeight to the flow layout's height at the current width."""
        layout = self.layout()
        if layout is None:
            return
        height = layout.heightForWidth(self.width())
        if height != self.minimumHeight():
            self.setMinimumHeight(height)


class AlbumTabBuilder:
    """Build the relationship tabs of the album editor from its album and helper."""

    def __init__(self, album_view):
        self.view = album_view
        self.album = album_view.album
        self.controller = album_view.controller
        self.helper = album_view.helper

    # =========================================================================
    # Public tab builders
    # =========================================================================

    def build_relationships_tab(self):
        """Build the Publishers & Places tab."""
        tab = QWidget()
        layout = QVBoxLayout(tab)

        layout.addWidget(self._build_publishers_section())
        layout.addWidget(self._build_places_section())
        layout.addWidget(self._build_track_places_section())
        layout.addStretch()
        return tab

    def build_awards_tab(self):
        """Build the Awards tab."""
        tab = QWidget()
        layout = QVBoxLayout(tab)

        add_btn = QPushButton("Add Album Award")
        add_btn.clicked.connect(self.helper.add_album_award)
        layout.addWidget(add_btn)

        layout.addWidget(self._build_awards_list())
        layout.addStretch()
        return tab

    def build_artists_tab(self):
        """Build the Album credit tab."""
        tab = QWidget()
        layout = QVBoxLayout(tab)

        layout.addWidget(self._build_add_artist_credit_row())

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(self._build_artists_list())
        layout.addWidget(scroll)
        return tab

    # =========================================================================
    # Internal section builders
    # =========================================================================

    def _build_add_artist_credit_row(self):
        """Build the inline artist + role completer row with its Add button."""
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 8)
        row_layout.setSpacing(6)

        artist_search = build_entity_search_widget(self.controller, "Artist", "artist_name", "artist_id", "Search artists…", context_builder=artist_context_map)

        role_search = build_entity_search_widget(self.controller, "Role", "role_name", "role_id", "Role (e.g. Performer, Composer…)")

        add_btn = QPushButton("Add Artist Credit")
        add_btn.setEnabled(False)

        def _update_add_btn(*_args):
            add_btn.setEnabled(len(artist_search.text().strip()) >= 2 and len(role_search.text().strip()) >= 2)

        artist_search.textChanged.connect(_update_add_btn)
        role_search.textChanged.connect(_update_add_btn)

        def _handle_add():
            if not add_btn.isEnabled():
                return  # Enter in either box bypasses the button; keep the same length gate
            artist_names = artist_search.split_names()
            role_names = role_search.split_names()
            if not artist_names or not role_names:
                return
            self.helper.add_artist_credit(
                artist_names,
                role_names,
                # matched_id names only a single typed entry; several are resolved by name.
                matched_artist_id=artist_search.matched_id() if len(artist_names) == 1 else None,
                matched_role_id=role_search.matched_id() if len(role_names) == 1 else None,
            )
            artist_search.reset()
            role_search.reset()

        add_btn.clicked.connect(_handle_add)
        role_search.returnPressed.connect(_handle_add)
        artist_search.returnPressed.connect(_handle_add)

        row_layout.addWidget(artist_search, 3)
        row_layout.addWidget(role_search, 2)
        row_layout.addWidget(add_btn)
        return row

    def _build_publishers_section(self):
        """Build the Publishers group with remove buttons and an Add button."""
        group = QGroupBox("Publishers")
        layout = QVBoxLayout(group)

        try:
            album_publishers = self.controller.get.get_all_entities("AlbumPublisher", album_id=self.album.album_id) or []
            rows = [(ap, ap.publisher) for ap in album_publishers if ap.publisher is not None]
        except SQLAlchemyError as e:
            logger.error(f"Error loading album publishers: {e}")
            rows = []

        if rows:
            for album_publisher, publisher in rows:
                widget = QWidget()
                widget_layout = QHBoxLayout(widget)
                widget_layout.setContentsMargins(0, 0, 0, 0)

                mb_badge = " \U0001f517" if publisher.MBID else ""
                name_label = QLabel(f"{publisher.publisher_name}{mb_badge}")
                if publisher.MBID:
                    name_label.setToolTip("Linked to MusicBrainz")
                widget_layout.addWidget(name_label)

                remove_btn = _remove_button("Remove", f"Remove publisher {publisher.publisher_name}")
                remove_btn.clicked.connect(lambda checked, ap=album_publisher: self.helper.remove_publisher(ap))
                widget_layout.addWidget(remove_btn)
                layout.addWidget(widget)
        else:
            layout.addWidget(_placeholder_label("No publishers associated"))

        add_btn = QPushButton("Add Publisher")
        add_btn.clicked.connect(self.helper.add_publisher)
        layout.addWidget(add_btn)

        return group

    def _build_places_section(self):
        """Build the album-level Place Associations group."""
        group = QGroupBox("Place Associations")
        layout = QVBoxLayout(group)

        try:
            rows = [(a, a.place) for a in self.view.get_album_place_associations() if a.place is not None]
        except SQLAlchemyError as e:
            logger.error(f"Error loading album place associations: {e}")
            rows = []

        if rows:
            for association, place in rows:
                widget = QWidget()
                widget_layout = QHBoxLayout(widget)
                widget_layout.setContentsMargins(0, 0, 0, 0)

                assoc_type_name = association.association_type.type_name if association.association_type else ""
                mb_badge = " \U0001f517" if place.MBID else ""
                place_text = f"{place.place_name} ({assoc_type_name}){mb_badge}"
                place_label = QLabel(place_text)
                if place.MBID:
                    place_label.setToolTip("Linked to MusicBrainz")
                widget_layout.addWidget(place_label)

                remove_btn = _remove_button("Remove", f"Remove place {place.place_name}")
                remove_btn.clicked.connect(lambda checked, a=association: self.helper.remove_place(a))
                widget_layout.addWidget(remove_btn)
                layout.addWidget(widget)
        else:
            layout.addWidget(_placeholder_label("No place associations"))

        add_btn = QPushButton("Add Place Association")
        add_btn.clicked.connect(self.helper.add_place)
        layout.addWidget(add_btn)

        return group

    def _build_track_places_section(self):
        """Build the group of places common to every track; edits apply to all tracks."""
        # Reuses the track PlacesTab across all tracks (entity_type="Track"), separate
        # from the album-level Place Associations (entity_type="Album") above.
        group = QGroupBox("Track Places (common to all tracks)")
        layout = QVBoxLayout(group)

        tracks = self.album.tracks or []
        if not tracks:
            layout.addWidget(_placeholder_label("This album has no tracks yet."))
            return group

        info = QLabel("Places common to every track on this album. Adding or removing a place here applies it to all of the album's tracks.")
        info.setWordWrap(True)
        layout.addWidget(info)

        places_widget = TrackPlacesTab(tracks, self.controller, parent=group)
        places_widget.load(tracks)
        layout.addWidget(places_widget)
        return group

    def _build_awards_list(self):
        """Build the list of the album's awards, or an empty-state label."""
        try:
            award_associations = self.controller.get.get_all_entities("AwardAssociation", entity_id=self.album.album_id, entity_type="Album") or []
            album_awards = [assoc.award for assoc in award_associations if assoc.award is not None]
        except SQLAlchemyError as e:
            logger.error(f"Error loading album awards: {e}")
            album_awards = []

        if not album_awards:
            label = _placeholder_label("No awards associated with this album.")
            label.setAlignment(Qt.AlignCenter)
            return label

        group = QGroupBox("Album Awards")
        layout = QVBoxLayout(group)

        for index, award in enumerate(album_awards):
            layout.addWidget(self._build_award_widget(award))
            if index < len(album_awards) - 1:
                sep = QFrame()
                sep.setFrameShape(QFrame.HLine)
                sep.setFrameShadow(QFrame.Sunken)
                layout.addWidget(sep)

        return group

    def _build_award_widget(self, award):
        """Build one award's name, details and remove button."""
        widget = QWidget()
        layout = QVBoxLayout(widget)

        try:
            award_name = getattr(award, "award_name", "Unknown Award")
            award_year = getattr(award, "award_year", None)
            award_category = getattr(award, "award_category", None)
            award_desc = getattr(award, "award_description", None)

            layout.addWidget(QLabel(f"<b>{html.escape(str(award_name))}</b>"))

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

            remove_btn = _remove_button("Remove Award", f"Remove award {award_name}")
            remove_btn.clicked.connect(lambda checked, a=award: self.helper.remove_album_award_association(a))
            layout.addWidget(remove_btn)

        except (AttributeError, TypeError) as e:
            logger.error(f"Error displaying award {award}: {e}")
            layout.addWidget(QLabel(f"Error displaying award: {e!s}"))

        return widget

    def _build_artists_list(self):
        """Build the album credits grouped by role, or an empty-state label."""
        if not getattr(self.album, "album_roles", None):
            return _placeholder_label("No album-level credits yet.")

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setSpacing(4)
        layout.setContentsMargins(0, 0, 0, 0)

        roles_by_type: dict[str, list] = {}
        for role_assoc in self.album.album_roles:
            role_name = role_assoc.role.role_name if role_assoc.role else "Unknown Role"
            roles_by_type.setdefault(role_name, [])
            artist_name = role_assoc.credited_name or "Unknown Artist"
            roles_by_type[role_name].append((artist_name, role_assoc))

        # One point smaller than the app font, so it still follows display scaling.
        small_font = QApplication.font()
        small_font.setPointSizeF(max(small_font.pointSizeF() - 1, 6))

        # sort_order is unique only within a role, so group order comes from
        # the role name instead; otherwise groups move whenever a credit changes.
        ordered_role_names = sorted(roles_by_type, key=_role_group_sort_key)

        for role_name in ordered_role_names:
            artist_tuples = roles_by_type[role_name]
            role_group = QGroupBox(esc_amp(role_name))
            role_group.setFont(small_font)
            # Hug the chips instead of spreading across the full tab width.
            role_group.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Preferred)
            role_layout = QVBoxLayout(role_group)
            role_layout.setContentsMargins(6, 4, 6, 4)

            flow_area = _CreditFlowArea()
            flow = FlowLayout(flow_area, margin=0, h_spacing=6, v_spacing=4)
            role_layout.addWidget(flow_area)

            # Already in credit (sort_order) order; do not re-sort.
            last_index = len(artist_tuples) - 1
            for index, (artist_name, role_assoc) in enumerate(artist_tuples):
                chip = self._build_artist_credit_chip(artist_name, role_assoc, index, last_index)
                flow.addWidget(chip)
                chip.show()  # force visible now so the layout pass below places it

            flow.activate()
            flow_area.refresh_height()
            layout.addWidget(role_group, 0, Qt.AlignLeft)

        layout.addStretch(1)

        return container

    def _build_artist_credit_chip(self, artist_name, role_assoc, index, last_index):
        """Build one compact credit chip (arrows, name, actions) for the flow."""
        chip = QWidget()
        chip.setObjectName("ArtistCreditChip")
        chip_layout = QHBoxLayout(chip)
        chip_layout.setContentsMargins(4, 2, 4, 2)
        chip_layout.setSpacing(4)

        up_btn = QPushButton("▲")
        up_btn.setFixedSize(20, 22)
        apply_scaled_style(up_btn, "font-size: 10px; padding: 0px;")
        up_btn.setEnabled(index > 0)
        up_btn.setToolTip(f"Move {artist_name} up")
        up_btn.setAccessibleName(f"Move {artist_name} up")
        up_btn.clicked.connect(lambda checked, ra=role_assoc: self.helper.move_artist_credit(ra, -1))
        chip_layout.addWidget(up_btn)

        down_btn = QPushButton("▼")
        down_btn.setFixedSize(20, 22)
        apply_scaled_style(down_btn, "font-size: 10px; padding: 0px;")
        down_btn.setEnabled(index < last_index)
        down_btn.setToolTip(f"Move {artist_name} down")
        down_btn.setAccessibleName(f"Move {artist_name} down")
        down_btn.clicked.connect(lambda checked, ra=role_assoc: self.helper.move_artist_credit(ra, 1))
        chip_layout.addWidget(down_btn)

        name_label = QLabel(artist_name)
        apply_scaled_style(name_label, "font-size: 11px;")
        chip_layout.addWidget(name_label)

        credit_btn = QPushButton("Credit as…")
        credit_btn.setFixedHeight(22)
        apply_scaled_style(credit_btn, "font-size: 10px; padding: 1px 6px;")
        credit_btn.setToolTip(f"Choose which name (canonical or alias) to credit {role_assoc.artist.artist_name if role_assoc.artist else artist_name} as here")
        credit_btn.clicked.connect(lambda checked, ra=role_assoc: self.helper.change_credited_alias(ra))
        chip_layout.addWidget(credit_btn)

        to_track_btn = QPushButton("→ Track")
        to_track_btn.setFixedHeight(22)
        apply_scaled_style(to_track_btn, "font-size: 10px; padding: 1px 6px;")
        to_track_btn.setToolTip("Move this credit to every track on the album (removes it from the album-level credit list)")
        to_track_btn.clicked.connect(lambda checked, ra=role_assoc: self.helper.convert_credit_to_per_track(ra))
        chip_layout.addWidget(to_track_btn)

        remove_btn = _remove_button("Remove", f"Remove credit for {artist_name}")
        remove_btn.setFixedHeight(22)
        apply_scaled_style(remove_btn, "font-size: 10px; padding: 1px 6px;")
        remove_btn.clicked.connect(lambda checked, ra=role_assoc: self.helper.remove_artist_credit(ra))
        chip_layout.addWidget(remove_btn)

        return chip
