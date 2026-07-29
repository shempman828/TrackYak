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

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCompleter,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.common.entity_completer_edit import build_entity_search_widget
from src.core.logger_config import logger


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
        layout.addWidget(self._build_artists_list())
        layout.addStretch()
        return tab

    # =========================================================================
    # Internal section builders
    # =========================================================================

    def _build_add_artist_credit_row(self):
        """Inline artist + role entry row, replacing the old popup dialog.

        Mirrors the search bar in track_edit_roles.py: a bare entity
        completer for the artist plus a plain (completer-assisted) text
        field for the role, with an Add button — no modal.
        """
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 8)
        row_layout.setSpacing(6)

        artist_search = build_entity_search_widget(
            self.controller, "Artist", "artist_name", "artist_id", "Search artists…"
        )

        role_edit = QLineEdit()
        role_edit.setPlaceholderText("Role (e.g. Performer, Composer…)")
        existing_roles = [
            r.role_name
            for r in (self.controller.get.get_all_entities("Role") or [])
            if r.role_name
        ]
        if existing_roles:
            completer = QCompleter(existing_roles, role_edit)
            completer.setCaseSensitivity(Qt.CaseInsensitive)
            completer.setFilterMode(Qt.MatchContains)
            role_edit.setCompleter(completer)

        add_btn = QPushButton("Add Artist Credit")
        add_btn.setEnabled(False)

        def _update_add_btn(*_args):
            add_btn.setEnabled(
                len(artist_search.text().strip()) >= 2
                and len(role_edit.text().strip()) >= 2
            )

        artist_search.textChanged.connect(_update_add_btn)
        role_edit.textChanged.connect(_update_add_btn)

        def _handle_add():
            artist_name = artist_search.text().strip()
            role_name = role_edit.text().strip()
            if not artist_name or not role_name:
                return
            self.helper.add_artist_credit(
                artist_name, role_name, matched_artist_id=artist_search.matched_id()
            )
            artist_search.reset()
            role_edit.clear()

        add_btn.clicked.connect(_handle_add)
        role_edit.returnPressed.connect(_handle_add)
        artist_search.returnPressed.connect(_handle_add)

        row_layout.addWidget(artist_search, 3)
        row_layout.addWidget(role_edit, 2)
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

                    assoc_type_name = (
                        association.association_type.type_name
                        if association.association_type
                        else ""
                    )
                    place_text = f"{place.place_name} ({assoc_type_name})"
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

        for role_name, artist_tuples in roles_by_type.items():
            role_group = QGroupBox(role_name)
            role_group.setFont(small_font)  # ← shrinks the group title
            role_layout = QVBoxLayout(role_group)
            role_layout.setContentsMargins(6, 4, 6, 4)
            role_layout.setSpacing(2)

            # artist_tuples is already in credit order (Album.album_roles is
            # ordered by sort_order) — no alphabetical re-sort here, since
            # that would defeat the whole point of letting it be reordered.
            last_index = len(artist_tuples) - 1
            for index, (artist_name, role_assoc) in enumerate(artist_tuples):
                artist_widget = QWidget()
                artist_layout = QHBoxLayout(artist_widget)
                artist_layout.setContentsMargins(0, 0, 0, 0)
                artist_layout.setSpacing(6)

                up_btn = QPushButton("▲")
                up_btn.setFixedSize(20, 22)
                up_btn.setStyleSheet("font-size: 10px; padding: 0px;")
                up_btn.setEnabled(index > 0)
                up_btn.setToolTip(f"Move {artist_name} up")
                up_btn.clicked.connect(
                    lambda checked, ra=role_assoc: self.helper.move_artist_credit(
                        ra, -1
                    )
                )
                artist_layout.addWidget(up_btn)

                down_btn = QPushButton("▼")
                down_btn.setFixedSize(20, 22)
                down_btn.setStyleSheet("font-size: 10px; padding: 0px;")
                down_btn.setEnabled(index < last_index)
                down_btn.setToolTip(f"Move {artist_name} down")
                down_btn.clicked.connect(
                    lambda checked, ra=role_assoc: self.helper.move_artist_credit(ra, 1)
                )
                artist_layout.addWidget(down_btn)

                name_label = QLabel(artist_name)
                name_label.setStyleSheet("font-size: 11px;")  # ← smaller text
                artist_layout.addWidget(name_label)

                credit_btn = QPushButton("Credit as…")
                credit_btn.setFixedHeight(22)
                credit_btn.setStyleSheet("font-size: 10px; padding: 1px 6px;")
                credit_btn.setToolTip(
                    f"Choose which name (canonical or alias) to credit "
                    f"{role_assoc.artist.artist_name if role_assoc.artist else artist_name} as here"
                )
                credit_btn.clicked.connect(
                    lambda checked, ra=role_assoc: self.helper.change_credited_alias(ra)
                )
                artist_layout.addWidget(credit_btn)

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
