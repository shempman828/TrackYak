"""Artist management view handling both individuals and groups."""

from typing import ClassVar
import webbrowser

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import selectinload

from src.artist.artist_detail import ArtistDetailTab
from src.artist.artist_view_actions import ArtistActionsMixin
from src.artist.artist_view_dedup import ArtistDedupMixin
from src.artist.artist_view_tracks import ArtistViewTracksMixin
from src.core.asset_paths import icon
from src.core.config_setup import app_config
from src.core.logger_config import logger
from src.core.status_utility import show_status_message
from src.db.db_tables import Artist, TrackArtistRole


# -------------------------
# Main Artist View Widget
# -------------------------
class ArtistView(ArtistActionsMixin, ArtistViewTracksMixin, ArtistDedupMixin, QWidget):
    """Unified artist management view handling both individuals and groups.

    CRUD/action handlers (create/edit/convert/split/merge/delete, group
    membership, awards, places, influences, profile picture) live in
    ArtistActionsMixin (artist_view_actions.py). Track lookup lives in
    ArtistViewTracksMixin (artist_view_tracks.py). Orphan/duplicate
    scanning lives in ArtistDedupMixin (artist_view_dedup.py). This class
    owns UI setup, data loading/filtering/persistence, selection, and the
    context menu, and composes the other three.
    """

    # Sort options: (display label, sort key function)
    # None as the sort key signals a special-case sort handled in _apply_filters.
    _SORT_OPTIONS: ClassVar = [
        ("Name (A–Z)", lambda a: a.artist_name.lower()),
        ("Name (Z–A)", lambda a: a.artist_name.lower()),  # reversed below
        ("Earliest First", lambda a: getattr(a, "begin_year", None) or 9999),
        ("Latest First", lambda a: getattr(a, "begin_year", None) or 9999),  # reversed
        ("Most Tracks", None),  # special-case: requires track-count lookup
        ("Random", None),  # special-case: shuffled in _apply_filters
    ]
    _SORT_REVERSED: ClassVar = {"Name (Z–A)": True, "Latest First": True}
    _MODE_MAP: ClassVar = {"All": "all", "Individuals": "individuals", "Groups": "groups"}

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.all_artists = []
        self.current_mode = "all"
        # Set by _restore_filter_state() when a persisted type filter exists;
        # type_combo's options are rebuilt from the DB on every load_artists()
        # call, so the restored value can't be applied until after that
        # rebuild happens.
        self._pending_restore_type: str | None = None

        # Debounce timer for persisting filter state, so rapid changes (e.g.
        # typing in the search box) don't hit disk on every keystroke.
        self._filter_save_timer = QTimer(self)
        self._filter_save_timer.setSingleShot(True)
        self._filter_save_timer.setInterval(400)
        self._filter_save_timer.timeout.connect(self._save_filter_state)

        self._setup_ui()
        self._restore_filter_state()
        self.load_artists()

    # ----------------------------
    # UI Setup
    # ----------------------------

    def _setup_ui(self):
        """Build the main layout with filter bar, artist list, and detail panel."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # --- Single compact filter row ---
        filter_bar = QHBoxLayout()
        filter_bar.setSpacing(4)

        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["All", "Individuals", "Groups"])
        self.mode_combo.setToolTip("Show all artists, individuals only, or groups only")
        self.mode_combo.currentTextChanged.connect(self._on_mode_changed)
        filter_bar.addWidget(self.mode_combo)

        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("Search…")
        self.search_box.setClearButtonEnabled(True)
        self.search_box.textChanged.connect(self._apply_filters)
        filter_bar.addWidget(self.search_box, stretch=1)

        self.sort_combo = QComboBox()
        for label, _ in self._SORT_OPTIONS:
            self.sort_combo.addItem(label)
        self.sort_combo.setToolTip("Sort order")
        self.sort_combo.currentTextChanged.connect(self._apply_filters)
        filter_bar.addWidget(self.sort_combo)

        self.metadata_combo = QComboBox()
        self.metadata_combo.addItems(["Any", "Not Started", "First Pass", "Second Pass"])
        self.metadata_combo.setToolTip("Filter by the artist's metadata review tier")
        self.metadata_combo.currentTextChanged.connect(self._apply_filters)
        filter_bar.addWidget(self.metadata_combo)

        self.image_combo = QComboBox()
        self.image_combo.addItems(["Any Image", "Has Image", "No Image"])
        self.image_combo.setToolTip("Filter by profile image")
        self.image_combo.currentTextChanged.connect(self._apply_filters)
        filter_bar.addWidget(self.image_combo)

        self.type_combo = QComboBox()
        self.type_combo.addItem("Any Type")
        self.type_combo.setToolTip("Filter by artist type")
        self.type_combo.currentTextChanged.connect(self._apply_filters)
        filter_bar.addWidget(self.type_combo)

        # Count label — shows "Showing X of Y"
        self.count_label = QLabel()
        self.count_label.setProperty("textRole", "muted")
        filter_bar.addWidget(self.count_label)

        layout.addLayout(filter_bar)

        # --- Splitter: list on the left, detail panel on the right ---
        splitter = QSplitter(Qt.Horizontal)

        # Artist list with a minimum width so it never collapses too small,
        # but users can drag to make it wider.
        self.artist_list = QListWidget()
        self.artist_list.setMinimumWidth(180)
        self.artist_list.setIconSize(QSize(14, 14))
        self.artist_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.artist_list.customContextMenuRequested.connect(self._show_context_menu)
        self.artist_list.currentItemChanged.connect(self._on_artist_selected)
        splitter.addWidget(self.artist_list)

        # Detail panel: plain widget that swaps in an ArtistDetailTab directly —
        # no tab bar or tab names needed, since the detail header already shows
        # the artist name.
        self.detail_container = QWidget()
        self.detail_layout = QVBoxLayout(self.detail_container)
        self.detail_layout.setContentsMargins(0, 0, 0, 0)

        self._placeholder = QLabel("Select an artist to view details")
        self._placeholder.setAlignment(Qt.AlignCenter)
        self._placeholder.setProperty("textRole", "note")
        self.detail_layout.addWidget(self._placeholder)

        self._current_detail = None  # track which widget is currently shown

        splitter.addWidget(self.detail_container)

        # Give the list ~1 part and the detail ~3 parts of available space.
        # The initial pixel sizes respect the minimum and give a sensible default.
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)
        splitter.setSizes([220, 660])

        layout.addWidget(splitter, stretch=1)

    # ----------------------------
    # Data Loading
    # ----------------------------

    def load_artists(self):
        """Load all artists from DB, pre-filtered by individual/group mode."""
        try:
            all_artists = sorted(
                self.controller.get.get_all_entities(
                    "Artist", load_options=[selectinload(Artist.types)]
                ),
                key=lambda a: a.artist_name.lower(),
            )

            if self.current_mode == "individuals":
                artists = [a for a in all_artists if not getattr(a, "isgroup", 0)]
            elif self.current_mode == "groups":
                artists = [a for a in all_artists if getattr(a, "isgroup", 0)]
            else:
                artists = all_artists

            self.all_artists = artists

            # Rebuild type filter options from loaded artists' ArtistType assignments
            types = sorted(
                {t.type_name for a in artists for t in (getattr(a, "types", None) or [])}
            )
            if self._pending_restore_type is not None:
                current_type = self._pending_restore_type
                self._pending_restore_type = None
            else:
                current_type = self.type_combo.currentText()
            self.type_combo.blockSignals(True)
            self.type_combo.clear()
            self.type_combo.addItem("Any Type")
            for t in types:
                self.type_combo.addItem(t)
            idx = self.type_combo.findText(current_type)
            self.type_combo.setCurrentIndex(max(idx, 0))
            self.type_combo.blockSignals(False)

            self._apply_filters()

        except (SQLAlchemyError, AttributeError) as e:
            QMessageBox.critical(self, "Error", f"Failed to load artists: {e}")

    def _apply_filters(self):
        """Apply search text, metadata, image, and sort filters, then repopulate."""
        artists = list(self.all_artists)

        # --- Search text filter ---
        text = self.search_box.text().lower().strip()
        if text:
            artists = [a for a in artists if text in a.artist_name.lower()]

        # --- Metadata review-tier filter ---
        metadata_mode = self.metadata_combo.currentText()
        if metadata_mode == "Not Started":
            artists = [a for a in artists if not getattr(a, "first_pass", 0)]
        elif metadata_mode == "First Pass":
            artists = [
                a
                for a in artists
                if getattr(a, "first_pass", 0) and not getattr(a, "second_pass", 0)
            ]
        elif metadata_mode == "Second Pass":
            artists = [a for a in artists if getattr(a, "second_pass", 0)]

        # --- Profile image filter ---
        image_mode = self.image_combo.currentText()
        if image_mode == "Has Image":
            artists = [a for a in artists if getattr(a, "profile_pic_path", None)]
        elif image_mode == "No Image":
            artists = [a for a in artists if not getattr(a, "profile_pic_path", None)]

        # --- Artist type filter ---
        type_filter = self.type_combo.currentText()
        if type_filter and type_filter != "Any Type":
            artists = [
                a for a in artists if any(t.type_name == type_filter for t in (a.types or []))
            ]

        # --- Sort ---
        import random as _random

        sort_label = self.sort_combo.currentText()

        if sort_label == "Random":
            _random.shuffle(artists)
        elif sort_label == "Most Tracks":
            try:
                counts = dict(
                    self.controller.get.session.execute(
                        select(TrackArtistRole.artist_id, func.count()).group_by(
                            TrackArtistRole.artist_id
                        )
                    ).all()
                )
                artists = sorted(artists, key=lambda a: counts.get(a.artist_id, 0), reverse=True)
            except SQLAlchemyError as e:
                logger.warning(f"Track-count sort failed: {e}")
        else:
            sort_key = next(
                (
                    key
                    for label, key in self._SORT_OPTIONS
                    if label == sort_label and key is not None
                ),
                lambda a: a.artist_name.lower(),
            )
            reverse = self._SORT_REVERSED.get(sort_label, False)
            try:
                artists = sorted(artists, key=sort_key, reverse=reverse)
            except AttributeError as e:
                logger.warning(f"Sort failed: {e}")

        self._populate_list(artists)
        self._filter_save_timer.start()

    def _get_filter_state(self) -> dict:
        """Snapshot the filter widgets' values for persistence."""
        return {
            "mode_text": self.mode_combo.currentText(),
            "search": self.search_box.text(),
            "sort": self.sort_combo.currentText(),
            "metadata": self.metadata_combo.currentText(),
            "image": self.image_combo.currentText(),
            "type": self.type_combo.currentText(),
        }

    def _save_filter_state(self):
        app_config.set_artist_view_filters(self._get_filter_state())
        app_config.save()

    def _set_combo_text(self, combo: QComboBox, text: str | None):
        if not text:
            return
        idx = combo.findText(text)
        if idx >= 0:
            combo.blockSignals(True)
            combo.setCurrentIndex(idx)
            combo.blockSignals(False)

    def _restore_filter_state(self):
        """Restore filter widget values persisted from the previous session."""
        state = app_config.get_artist_view_filters()
        if not state:
            return

        mode_text = state.get("mode_text")
        if mode_text:
            idx = self.mode_combo.findText(mode_text)
            if idx >= 0:
                self.mode_combo.blockSignals(True)
                self.mode_combo.setCurrentIndex(idx)
                self.mode_combo.blockSignals(False)
                self.current_mode = self._MODE_MAP.get(mode_text, "all")

        self.search_box.blockSignals(True)
        self.search_box.setText(state.get("search", ""))
        self.search_box.blockSignals(False)

        self._set_combo_text(self.sort_combo, state.get("sort"))
        self._set_combo_text(self.metadata_combo, state.get("metadata"))
        self._set_combo_text(self.image_combo, state.get("image"))

        # type_combo only has "Any Type" until load_artists() rebuilds it from
        # the DB, so stash the target for it to pick up at that point instead.
        self._pending_restore_type = state.get("type")

    def _populate_list(self, artists):
        """Fill the list widget from a filtered/sorted list of artist objects."""
        self.artist_list.clear()

        for artist in artists:
            display_name = artist.artist_name or "(no name)"
            if getattr(artist, "isgroup", 0):
                display_name = f"👥 {display_name}"

            if getattr(artist, "MBID", None):
                display_name = f"{display_name} \U0001f517"

            item = QListWidgetItem(display_name)
            item.setData(Qt.UserRole, artist.artist_id)

            # Small icon badge for the metadata review tier
            if getattr(artist, "second_pass", 0):
                item.setIcon(icon("checkmark_green.svg"))
            elif getattr(artist, "first_pass", 0):
                item.setIcon(icon("checkmark.svg"))
            if getattr(artist, "MBID", None):
                item.setToolTip("Linked to MusicBrainz")
            self.artist_list.addItem(item)

        # Update count label
        total = len(self.all_artists)
        showing = len(artists)
        if showing == total:
            self.count_label.setText(f"{total} artist{'s' if total != 1 else ''}")
        else:
            self.count_label.setText(f"{showing} of {total} artists")

    # ----------------------------
    # Event Handlers
    # ----------------------------

    def _on_mode_changed(self, mode_text: str):
        """Handle mode changes between all/individuals/groups."""
        self.current_mode = self._MODE_MAP.get(mode_text, "all")
        self.load_artists()

    def _on_artist_selected(self):
        """Swap the detail panel to show the selected artist — no tabs needed."""
        selected = self.artist_list.currentItem()
        if not selected:
            return

        artist_id = selected.data(Qt.UserRole)
        artist = self.controller.get.get_entity_object("Artist", artist_id=artist_id)
        if not artist:
            show_status_message(self, f"No artist found with ID {artist_id}")
            return

        # Remove whatever is currently in the detail panel
        if self._current_detail is not None:
            self.detail_layout.removeWidget(self._current_detail)
            self._current_detail.setParent(None)
            self._current_detail.deleteLater()
            self._current_detail = None

        # Hide the placeholder and insert the new detail widget
        self._placeholder.hide()
        detail = ArtistDetailTab(artist, self.controller)
        self.detail_layout.addWidget(detail)
        self._current_detail = detail

    # ----------------------------
    # Context Menu
    # ----------------------------

    def _show_context_menu(self, position):
        """Enhanced context menu with group-specific actions."""
        menu = QMenu(self)
        selected = self.artist_list.currentItem()

        if selected:
            artist_id = selected.data(Qt.UserRole)
            artist = self.controller.get.get_entity_object("Artist", artist_id=artist_id)
            if not artist:
                return

            is_group = getattr(artist, "isgroup", 0)

            # ---- Track browsing ----
            view_tracks_action = menu.addAction("🎵 View Artist Tracks")
            view_tracks_action.triggered.connect(lambda: self._view_artist_tracks(artist))

            menu.addSeparator()

            # ---- Common artist actions ----
            edit_action = menu.addAction("✏️ Edit Artist")
            edit_action.triggered.connect(lambda: self._edit_artist(artist))

            merge_action = menu.addAction("🔄 Merge Artist")
            merge_action.triggered.connect(lambda: self._merge_artist(artist))

            split_action = menu.addAction("🔀 Split Artist")
            split_action.triggered.connect(lambda: self._split_artist(artist))

            menu.addSeparator()

            # ---- Group-specific actions ----
            if is_group:
                add_member_action = menu.addAction("➕ Add Member")
                add_member_action.triggered.connect(lambda: self._add_member(artist))
            else:
                add_to_group_action = menu.addAction("👥 Add to Group")
                add_to_group_action.triggered.connect(lambda: self._add_to_group(artist))

            # ---- Common extras ----
            add_award_action = menu.addAction("🏆 Add Award")
            add_award_action.triggered.connect(lambda: self._add_award(artist))

            add_place_action = menu.addAction("📍 Add Place")
            add_place_action.triggered.connect(lambda: self._add_place(artist))

            menu.addSeparator()

            # ---- Convert group status ----
            if is_group:
                convert_action = menu.addAction("👤 Convert to Individual")
                convert_action.triggered.connect(lambda: self._convert_to_individual(artist))
            else:
                convert_action = menu.addAction("👥 Convert to Group")
                convert_action.triggered.connect(lambda: self._convert_to_group(artist))

            menu.addSeparator()

            # Wikipedia: open stored link if available, always offer a search
            wiki_link = getattr(artist, "wikipedia_link", None)
            if wiki_link:
                open_wiki_action = menu.addAction("🌐 Open Wikipedia Page")
                open_wiki_action.triggered.connect(
                    lambda checked=False, url=wiki_link: webbrowser.open(url)
                )

            influences_action = menu.addAction("🔗 Edit Influences")
            influences_action.triggered.connect(self.edit_influences)

            pic_action = menu.addAction("🖼️ Add Artist Image")
            pic_action.triggered.connect(self.add_profile_picture)

            menu.addSeparator()

            delete_action = menu.addAction("🗑️ Delete Artist")
            delete_action.triggered.connect(lambda: self._delete_artist(artist))

        # ---- Always-visible add actions ----
        menu.addSeparator()
        add_action = menu.addAction("➕ Add Artist")
        add_action.triggered.connect(self.add_new_artist)

        add_group_action = menu.addAction("👥 Add Group")
        add_group_action.triggered.connect(self.add_new_group)

        fuzzy_action = menu.addAction("🔎 Find Duplicate Artists…")
        fuzzy_action.triggered.connect(self.find_fuzzy_matches)

        orphan_action = menu.addAction("🧹 Delete Unused Artists…")
        orphan_action.triggered.connect(self.find_orphan_artists)

        menu.exec_(self.artist_list.mapToGlobal(position))
