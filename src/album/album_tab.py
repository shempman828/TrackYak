"""
album_tab.py  —  AlbumTabBuilder

Changes (this revision)
───────────────────────
• _build_artists_list: QGroupBox title font shrunk to 11 px; artist rows use
  compact margins so the tab doesn't feel overwhelmingly large.
• _build_artists_list: each role group now lays credits out as compact
  chips in a FlowLayout (same pattern as artist_edit_types.py's type chips)
  instead of one full-width row per credit, so multiple credits pack per
  line and wrap to fit the available width (#227).
• build_artists_tab: the credit list is now wrapped in a QScrollArea so
  albums with many credits don't blow out the dialog height (#227).
• _build_publishers_section / _build_places_section: after any Remove action
  the parent dialog's _on_subdialog_closed() is NOT called here (the helper
  already calls refresh_view which triggers _rebuild_current_tab).  No change
  needed here for the reload-on-close requirement.
"""

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from src.album.album_flowlayout import FlowLayout
from src.common.entity_completer_context import artist_context_map
from src.common.entity_completer_edit import build_entity_search_widget
from src.foundation.display_settings import apply_scaled_style
from src.foundation.logger_config import logger
from src.track.track_edit_places import PlacesTab as TrackPlacesTab

# Prefixes that mark a role as a variant of a base role, e.g. "Assistant
# Producer" / "Additional Producer" are both variants of "Producer".
_ROLE_VARIANT_PREFIXES = ("Additional ", "Assistant ")


def _role_group_sort_key(name: str) -> tuple:
    """Sort key grouping role variants (Additional/Assistant X) under their
    base role X, base-role groups ordered alphabetically, "Album Artist"
    pinned first."""
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


class _CreditFlowArea(QWidget):
    """Container for a role group's FlowLayout of credit chips.

    Same workaround as artist_edit_types.py's _ChipArea: a FlowLayout's real
    height depends on how many rows it wraps into at a given width, which
    Qt can't know ahead of time, so the enclosing group box would otherwise
    only ever reserve room for one row. Recomputing minimumHeight from
    heightForWidth() whenever the width changes keeps the reserved space
    accurate as credits wrap.
    """

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.refresh_height()

    def refresh_height(self):
        layout = self.layout()
        if layout is None:
            return
        height = layout.heightForWidth(self.width())
        if height != self.minimumHeight():
            self.setMinimumHeight(height)


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

    def build_relationships_tab(self):
        """Build tab for publishers and place associations"""
        tab = QWidget()
        layout = QVBoxLayout(tab)

        layout.addWidget(self._build_publishers_section())
        layout.addWidget(self._build_places_section())
        layout.addWidget(self._build_track_places_section())
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
        """Inline artist + role entry row, replacing the old popup dialog.

        Mirrors the search bar in track_edit_roles.py: entity completers for
        both artist and role, with an Add button — no modal.
        """
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 8)
        row_layout.setSpacing(6)

        artist_search = build_entity_search_widget(
            self.controller,
            "Artist",
            "artist_name",
            "artist_id",
            "Search artists…",
            context_builder=artist_context_map,
        )

        role_search = build_entity_search_widget(
            self.controller, "Role", "role_name", "role_id", "Role (e.g. Performer, Composer…)"
        )

        add_btn = QPushButton("Add Artist Credit")
        add_btn.setEnabled(False)

        def _update_add_btn(*_args):
            add_btn.setEnabled(
                len(artist_search.text().strip()) >= 2 and len(role_search.text().strip()) >= 2
            )

        artist_search.textChanged.connect(_update_add_btn)
        role_search.textChanged.connect(_update_add_btn)

        def _handle_add():
            artist_names = artist_search.split_names()
            role_names = role_search.split_names()
            if not artist_names or not role_names:
                return
            self.helper.add_artist_credit(
                artist_names,
                role_names,
                # matched_id only names a single typed artist/role -- with
                # several entered at once each is resolved by name instead.
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

                    mb_badge = " \U0001f517" if publisher.MBID else ""
                    name_label = QLabel(f"{publisher.publisher_name}{mb_badge}")
                    if publisher.MBID:
                        name_label.setToolTip("Linked to MusicBrainz")
                    widget_layout.addWidget(name_label)

                    remove_btn = QPushButton("Remove")
                    remove_btn.clicked.connect(
                        lambda checked, ap=album_publisher: self.helper.remove_publisher(ap)
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

                    assoc_type_name = (
                        association.association_type.type_name
                        if association.association_type
                        else ""
                    )
                    mb_badge = " \U0001f517" if place.MBID else ""
                    place_text = f"{place.place_name} ({assoc_type_name}){mb_badge}"
                    place_label = QLabel(place_text)
                    if place.MBID:
                        place_label.setToolTip("Linked to MusicBrainz")
                    widget_layout.addWidget(place_label)

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

    def _build_track_places_section(self):
        """Places common to every track on the album.

        Reuses the track editor's PlacesTab with the full set of the
        album's tracks, the same way base_album_edit_tabs.GenresTab reuses
        the track GenresTab: adding or removing a place here writes it to
        every track, so PlacesTab's existing multi-track intersection and
        write-to-all-tracks behavior double as the album-level view and the
        trickle-down edit mechanism.

        Independent of the album-level "Place Associations" section above --
        those are PlaceAssociation rows with entity_type="Album", these are
        entity_type="Track".
        """
        group = QGroupBox("Track Places (common to all tracks)")
        layout = QVBoxLayout(group)

        tracks = self.album.tracks or []
        if not tracks:
            layout.addWidget(QLabel("This album has no tracks yet."))
            return group

        info = QLabel(
            "Places common to every track on this album. Adding or removing "
            "a place here applies it to all of the album's tracks."
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        places_widget = TrackPlacesTab(tracks, self.controller, parent=group)
        places_widget.load(tracks)
        layout.addWidget(places_widget)
        return group

    def _build_awards_list(self):
        """Build the awards list content"""
        try:
            award_associations = (
                self.controller.get.get_all_entities(
                    "AwardAssociation", entity_id=self.album.album_id, entity_type="Album"
                )
                or []
            )
            album_awards = [assoc.award for assoc in award_associations if assoc.award is not None]
        except (AttributeError, TypeError) as e:
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

        except (AttributeError, TypeError) as e:
            logger.error(f"Error displaying award {award}: {e}")
            layout.addWidget(QLabel(f"Error displaying award: {e!s}"))

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
            artist_name = role_assoc.credited_name or "Unknown Artist"
            roles_by_type[role_name].append((artist_name, role_assoc))

        # Smaller font for the group-box title
        small_font = QFont()
        small_font.setPointSize(9)

        # Role-group display order must be stable and independent of
        # AlbumRoleAssociation.sort_order — that column is only unique
        # *within* a role's own credits (see add_artist_credit /
        # move_artist_credit), so the album's flat, globally-ordered
        # album_roles list interleaves different roles arbitrarily. Using
        # that list's first-appearance order as group order meant editing
        # any single credit could change which role's group renders first
        # (groups "jumping around"). Pin Album Artist to the top and sort
        # the rest alphabetically instead, so group order never shifts.
        # "Additional "/"Assistant " variants (e.g. "Assistant Producer")
        # are grouped under their base role ("Producer") rather than
        # sorting purely alphabetically, which would scatter them.
        ordered_role_names = sorted(roles_by_type, key=_role_group_sort_key)

        for role_name in ordered_role_names:
            artist_tuples = roles_by_type[role_name]
            role_group = QGroupBox(role_name)
            role_group.setFont(small_font)  # ← shrinks the group title
            # QVBoxLayout stretches child widgets to the full available width
            # by default, so with only a couple of roles each QGroupBox was
            # spreading across the whole tab instead of hugging its chips.
            # Cap the horizontal size to sizeHint so it collapses to its
            # content, and left-align it below.
            role_group.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Preferred)
            role_layout = QVBoxLayout(role_group)
            role_layout.setContentsMargins(6, 4, 6, 4)

            # Credit chips flow left-to-right, wrapping to the next line, so
            # multiple credits pack per row instead of one full-width row
            # each — see _CreditFlowArea for why the group box needs the
            # manual height recompute to fit the wrapped rows.
            flow_area = _CreditFlowArea()
            flow = FlowLayout(flow_area, margin=0, h_spacing=6, v_spacing=4)
            role_layout.addWidget(flow_area)

            # artist_tuples is already in credit order (Album.album_roles is
            # ordered by sort_order) — no alphabetical re-sort here, since
            # that would defeat the whole point of letting it be reordered.
            last_index = len(artist_tuples) - 1
            for index, (artist_name, role_assoc) in enumerate(artist_tuples):
                chip = self._build_artist_credit_chip(artist_name, role_assoc, index, last_index)
                flow.addWidget(chip)
                chip.show()  # force visible now so the layout pass below places it

            flow.activate()
            flow_area.refresh_height()
            layout.addWidget(role_group, 0, Qt.AlignLeft)

        # Keep the role groups clustered at the top instead of stretching
        # to fill any leftover vertical space in the scroll area.
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
        up_btn.clicked.connect(
            lambda checked, ra=role_assoc: self.helper.move_artist_credit(ra, -1)
        )
        chip_layout.addWidget(up_btn)

        down_btn = QPushButton("▼")
        down_btn.setFixedSize(20, 22)
        apply_scaled_style(down_btn, "font-size: 10px; padding: 0px;")
        down_btn.setEnabled(index < last_index)
        down_btn.setToolTip(f"Move {artist_name} down")
        down_btn.clicked.connect(
            lambda checked, ra=role_assoc: self.helper.move_artist_credit(ra, 1)
        )
        chip_layout.addWidget(down_btn)

        name_label = QLabel(artist_name)
        apply_scaled_style(name_label, "font-size: 11px;")  # ← smaller text
        chip_layout.addWidget(name_label)

        credit_btn = QPushButton("Credit as…")
        credit_btn.setFixedHeight(22)
        apply_scaled_style(credit_btn, "font-size: 10px; padding: 1px 6px;")
        credit_btn.setToolTip(
            f"Choose which name (canonical or alias) to credit "
            f"{role_assoc.artist.artist_name if role_assoc.artist else artist_name} as here"
        )
        credit_btn.clicked.connect(
            lambda checked, ra=role_assoc: self.helper.change_credited_alias(ra)
        )
        chip_layout.addWidget(credit_btn)

        to_track_btn = QPushButton("→ Track")
        to_track_btn.setFixedHeight(22)
        apply_scaled_style(to_track_btn, "font-size: 10px; padding: 1px 6px;")
        to_track_btn.setToolTip(
            "Move this credit to every track on the album (removes it "
            "from the album-level credit list)"
        )
        to_track_btn.clicked.connect(
            lambda checked, ra=role_assoc: self.helper.convert_credit_to_per_track(ra)
        )
        chip_layout.addWidget(to_track_btn)

        remove_btn = QPushButton("Remove")
        remove_btn.setFixedHeight(22)  # ← compact button
        apply_scaled_style(remove_btn, "font-size: 10px; padding: 1px 6px;")
        remove_btn.clicked.connect(
            lambda checked, ra=role_assoc: self.helper.remove_artist_credit(ra)
        )
        chip_layout.addWidget(remove_btn)

        return chip
