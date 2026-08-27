"""
base_album_edit.py
"""

import sqlite3
import webbrowser
from typing import ClassVar

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractScrollArea,
    QApplication,
    QComboBox,
    QCompleter,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from sqlalchemy.exc import SQLAlchemyError

from src.album.album_components import AlbumUIComponents
from src.album.album_cover_art_mixin import AlbumCoverArtMixin
from src.album.album_editing_relationship_helpers import RelationshipHelpers
from src.album.album_musicbrainz_mixin import AlbumMusicBrainzMixin
from src.album.album_tab import AlbumTabBuilder
from src.album.base_album_edit_tabs import (
    AdvancedTab,
    AliasesTab,
    ArtworkTab,
    DetailsTab,
    GenresTab,
    TrackCreditsTab,
    TracksTab,
)
from src.album.release_type_utils import (
    RELEASE_TYPE_SUGGESTIONS,
    normalize_release_type,
)
from src.common.edit_dirty import value_changed
from src.common.layout_utils import clear_layout
from src.common.nullable_numeric_field import create_nullable_int_field
from src.core.config_setup import Config
from src.core.logger_config import logger
from src.db.db_mapping_albums import ALBUM_FIELDS
from src.metadata.metadata_writer import MetadataWriter
from src.track.track_edit_roles import RolesTab

# Fallback suggestions used if the controller can't supply distinct values
# already present in the database.
ALBUM_LANGUAGE_SUGGESTIONS = [
    "English",
    "French",
    "German",
    "Italian",
    "Spanish",
    "Portuguese",
    "Japanese",
    "Korean",
    "Chinese",
    "Russian",
    "Instrumental",
    "Multiple",
]
# RELEASE_TYPE_SUGGESTIONS lives in release_type_utils.py alongside the
# normalization helper, since both need the same canonical casing.
STATUS_SUGGESTIONS = [
    "Official",
    "Promotional",
    "Bootleg",
    "Withdrawn",
    "Expunged",
    "Cancelled",
]


# =============================================================================
# AlbumEditor — main dialog
# =============================================================================


class AlbumEditor(AlbumCoverArtMixin, AlbumMusicBrainzMixin, QDialog):
    """
    Comprehensive album editor dialog with tabbed interface.

    Tabs
    ────
    Details          – core metadata (language, type, catalog #, live/compilation flags, sales, MBID, Wikipedia link)
    Tracks           – DiscManagementView for disc / track structure
    Artwork          – front cover, rear cover, liner art with pickers
    Aliases          – add / remove / type album aliases
    Genres           – genres common to all album tracks; edits trickle down to every track
    Track Credits    – artist/role credits common to all album tracks; edits trickle down
                       to every track; a credit can be converted to an album-level credit
    Album credit     – relationship helpers (built by AlbumTabBuilder)
    Publishers & Places – relationship helpers (built by AlbumTabBuilder)
    Awards           – relationship helpers (built by AlbumTabBuilder)
    Advanced         – metadata-complete flag, ReplayGain, library stats
    """

    # Caches shared across all AlbumEditor instances/openings so the (expensive)
    # full album-table scan for completer suggestions only happens once per
    # app session, not once per album edit dialog.
    _album_language_cache = None
    _release_type_cache = None
    _status_cache = None

    def __init__(self, controller, album, parent=None):
        super().__init__(parent)

        # Float freely — not locked to the parent window's position.
        self.setWindowFlag(Qt.Window, True)

        self.controller = controller
        self._config = Config()
        self._metadata_writer = MetadataWriter(controller)

        # Always reload the album from DB on open — avoids stale cover paths
        # when the editor is reopened after a previous cover change.
        try:
            fresh = controller.get.get_entity_object("Album", album_id=album.album_id)
            self.album = fresh if fresh is not None else album
        except SQLAlchemyError as e:
            logger.warning(
                f"Failed to reload album {album.album_id} on editor open: {e}"
            )
            self.album = album

        self.helper = RelationshipHelpers(
            controller, album, self.refresh_view, widget=self
        )
        self.field_widgets: dict = {}
        self.tab_builder = AlbumTabBuilder(self)

        self.setWindowTitle(f"Edit Album: {album.album_name}")
        self.setMinimumSize(1100, 750)

        self.init_editable_widgets()
        self.init_ui()
        self.setup_connections()

        # Stop any in-flight background cover embed on every exit path
        # (OK / Cancel / window close) so its signals never reach a
        # torn-down dialog. See AlbumCoverArtMixin._cleanup_cover_embed.
        self.finished.connect(lambda _result: self._cleanup_cover_embed())

        self._fit_to_screen()

    # =========================================================================
    # Widget initialisation
    # =========================================================================

    def init_editable_widgets(self):
        """Create one widget per editable ALBUM_FIELD, pre-filled from the album.

        For integer fields that allow NULL we use a nullable QLineEdit
        instead of a plain QSpinBox so the user can clear the value back to
        NULL by clearing the text.
        """
        NULLABLE_INT_FIELDS = {
            "release_day",
            "release_month",
            "release_year",
            "estimated_sales",
        }
        for field_name, field_config in ALBUM_FIELDS.items():
            if not field_config.editable:
                continue
            current_value = getattr(self.album, field_name, None)

            if field_config.type is int and field_name in NULLABLE_INT_FIELDS:
                min_val = field_config.min if field_config.min is not None else 0
                max_val = (
                    field_config.max if field_config.max is not None else 9_999_999
                )
                widget = create_nullable_int_field(
                    min_val=int(min_val),
                    max_val=int(max_val),
                    current_value=current_value,
                    group_separator=(field_name == "estimated_sales"),
                )
            else:
                widget = AlbumUIComponents.create_editable_field(
                    field_config, current_value
                )
            self.field_widgets[field_name] = widget

        self._attach_completer("album_language", self._get_album_language_suggestions())
        self._attach_completer("release_type", self._get_release_type_suggestions())
        self._setup_status_combo()

    def _setup_status_combo(self):
        """Replace the plain status field with a dropdown of known statuses."""
        field_config = ALBUM_FIELDS.get("status")
        if not field_config or not field_config.editable:
            return
        current_value = getattr(self.album, "status", None)
        combo = QComboBox()
        combo.setEditable(True)
        combo.setInsertPolicy(QComboBox.NoInsert)
        combo.addItem("")  # allows clearing the field back to NULL
        combo.addItems(self._get_status_suggestions())
        if current_value:
            idx = combo.findText(current_value, Qt.MatchFixedString)
            if idx >= 0:
                combo.setCurrentIndex(idx)
            else:
                combo.setEditText(current_value)
        else:
            combo.setCurrentIndex(0)
        self.field_widgets["status"] = combo

    def _attach_completer(self, field_name, suggestions):
        """Wire a popup QCompleter onto a QLineEdit field widget."""
        widget = self.field_widgets.get(field_name)
        if not isinstance(widget, QLineEdit):
            return
        completer = QCompleter(suggestions, self)
        completer.setCaseSensitivity(Qt.CaseInsensitive)
        completer.setCompletionMode(QCompleter.PopupCompletion)
        widget.setCompleter(completer)

    def _get_album_language_suggestions(self):
        if AlbumEditor._album_language_cache is None:
            AlbumEditor._album_language_cache = self._fetch_field_suggestions(
                "album_language", ALBUM_LANGUAGE_SUGGESTIONS
            )
        return AlbumEditor._album_language_cache

    def _get_release_type_suggestions(self):
        if AlbumEditor._release_type_cache is None:
            AlbumEditor._release_type_cache = self._fetch_field_suggestions(
                "release_type", RELEASE_TYPE_SUGGESTIONS
            )
        return AlbumEditor._release_type_cache

    def _get_status_suggestions(self):
        if AlbumEditor._status_cache is None:
            AlbumEditor._status_cache = self._fetch_field_suggestions(
                "status", STATUS_SUGGESTIONS
            )
        return AlbumEditor._status_cache

    def _fetch_field_suggestions(self, field_name, fallback):
        """Distinct values already used for `field_name` across the library,
        merged with a small generic fallback list in case lookup fails.
        Deduplicated case-insensitively so e.g. "Album" and "album" don't
        show up as two separate suggestions; the first casing seen wins."""
        suggestions = {}
        for value in fallback:
            suggestions.setdefault(value.lower(), value)
        try:
            albums = self.controller.get.get_all_entities("Album") or []
            for a in albums:
                value = (getattr(a, field_name, None) or "").strip()
                if value:
                    suggestions.setdefault(value.lower(), value)
        except SQLAlchemyError:
            logger.debug(
                f"Could not fetch suggestions for {field_name!r}", exc_info=True
            )
        return sorted(suggestions.values(), key=str.lower)

    # =========================================================================
    # Main UI layout
    # =========================================================================

    def init_ui(self):
        """Build the full dialog layout."""
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(15, 15, 15, 15)
        main_layout.setSpacing(12)

        self.tabs = QTabWidget()
        self.tabs.setTabPosition(QTabWidget.North)
        self._details_tab = DetailsTab(self)
        self._tracks_tab = TracksTab(self)
        self._artwork_tab = ArtworkTab(self)
        self._aliases_tab = AliasesTab(self)
        self._genres_tab = GenresTab(self)
        self._track_credits_tab = TrackCreditsTab(self)
        self._advanced_tab = AdvancedTab(self)

        self.tabs.addTab(self._build_header_section(), "Overview")
        self.tabs.addTab(self._details_tab.build(), "Details")
        self.tabs.addTab(self._tracks_tab.build(), "Tracks")
        self.tabs.addTab(self._artwork_tab.build(), "Artwork")
        self.tabs.addTab(self._aliases_tab.build(), "Aliases")
        self.tabs.addTab(self._genres_tab.build(), "Genres")
        self.tabs.addTab(self._track_credits_tab.build(), "Track Credits")
        self.tabs.addTab(self.tab_builder.build_artists_tab(), "Album credit")
        self.tabs.addTab(
            self.tab_builder.build_relationships_tab(), "Publishers && Places"
        )
        self.tabs.addTab(self.tab_builder.build_awards_tab(), "Awards")
        self.tabs.addTab(self._advanced_tab.build(), "Advanced")

        main_layout.addWidget(self.tabs)

        self._add_dialog_buttons(main_layout)

    # =========================================================================
    # Header section  (cover thumbnail + editable info — collapsible)
    # =========================================================================

    def _build_header_section(self):
        """Cover image on the left; editable album info on the right. Pinned to top."""
        # Create a container that fills the tab and pushes content up
        container = QWidget()
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.setSpacing(0)

        # Your original header content (unchanged)
        header = QWidget()
        header.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        row = QHBoxLayout(header)
        row.setSpacing(20)

        row.addWidget(self._build_cover_widget())
        row.addWidget(self._build_info_section(), 1)

        # Add the header at the top, then a stretch to push it up
        container_layout.addWidget(header, 0, Qt.AlignTop)
        container_layout.addStretch()

        return container

    def _build_cover_widget(self):
        """Cover thumbnail only — no Change Cover button (use Artwork tab)."""
        widget = QWidget()
        widget.setSizePolicy(
            QSizePolicy.Fixed, QSizePolicy.Fixed
        )  # FIX: prevent horizontal stretch
        layout = QVBoxLayout(widget)
        layout.setAlignment(Qt.AlignTop)

        self.cover_label = QLabel()
        self.cover_label.setAlignment(Qt.AlignCenter)
        self._cover_size = self._compute_cover_size()
        self.cover_label.setFixedSize(self._cover_size, self._cover_size)
        self.cover_label.setProperty("coverPlaceholder", True)
        self._load_album_cover()
        layout.addWidget(self.cover_label)

        return widget

    @staticmethod
    def _compute_cover_size():
        """Stay compact on typical displays, scale up modestly on larger ones."""
        screen = QApplication.primaryScreen()
        available_height = screen.availableGeometry().height() if screen else 1080
        return max(150, min(220, round(available_height * 0.15)))

    def _build_info_section(self):
        """Right-hand side of the header: title, subtitle, artists, date, description, links."""
        widget = QWidget()
        widget.setSizePolicy(
            QSizePolicy.Expanding, QSizePolicy.Fixed
        )  # FIX: prevent vertical stretch
        layout = QVBoxLayout(widget)
        layout.setSpacing(6)

        title_widget = self.field_widgets.get("album_name")
        if title_widget:
            title_widget.setObjectName("AlbumTitleField")
            layout.addWidget(title_widget)

        subtitle_widget = self.field_widgets.get("album_subtitle")
        if subtitle_widget:
            subtitle_widget.setPlaceholderText("Subtitle (optional)")
            layout.addWidget(subtitle_widget)

        if hasattr(self.album, "album_artists") and self.album.album_artists:
            names = ", ".join(a.artist_name for a in self.album.album_artists[:4])
            if len(self.album.album_artists) > 4:
                names += "…"
            artist_label = QLabel(f"by {names}")
            artist_label.setObjectName("albumByline")
            all_names = ", ".join(a.artist_name for a in self.album.album_artists)
            artist_label.setToolTip(all_names)
            layout.addWidget(artist_label)

        # Release date — labelled spinboxes stacked as a form group
        date_group = QHBoxLayout()
        date_group.setSpacing(12)
        date_group.addWidget(QLabel("Released:"))
        for field, label_text in (
            ("release_year", "Year"),
            ("release_month", "Month"),
            ("release_day", "Day"),
        ):
            w = self.field_widgets.get(field)
            if w:
                col = QVBoxLayout()
                col.setSpacing(2)
                lbl = QLabel(label_text)
                lbl.setProperty("textRole", "muted")
                w.setFixedWidth(90)
                col.addWidget(lbl)
                col.addWidget(w)
                date_group.addLayout(col)
        date_group.addStretch()
        layout.addLayout(date_group)

        # Description
        desc_label = QLabel("Description:")
        desc_label.setStyleSheet("font-weight: bold;")
        layout.addWidget(desc_label)

        self.desc_widget = self.field_widgets.get("album_description")
        if self.desc_widget is None:
            self.desc_widget = QTextEdit()
            self.desc_widget.setPlainText(self.album.album_description or "")

        self.desc_widget.setMinimumHeight(100)  # FIX: taller (was 60)
        self.desc_widget.setMaximumHeight(200)  # FIX: taller (was 120)
        self.desc_widget.setObjectName("AlbumDescriptionEdit")
        self.desc_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        layout.addWidget(self.desc_widget)

        # External links (buttons remain here, unchanged)
        self._links_row = QHBoxLayout()
        self._links_row.setSpacing(8)

        self.lookup_button = QPushButton("🎵 Look Up on MusicBrainz")
        self.lookup_button.setToolTip(
            "Search MusicBrainz release groups by album title/artist and fill "
            "in blank fields (MBID, release type, live/compilation, release "
            "date) from the selected match. Never overwrites fields you've "
            "already filled in."
        )
        self.lookup_button.clicked.connect(self._lookup_musicbrainz)
        self._links_row.addWidget(self.lookup_button)

        self._wiki_btn = None
        self._mb_btn = None
        self._reimport_btn = None

        self._rebuild_link_buttons()

        self._links_row.addStretch()
        layout.addLayout(self._links_row)

        return widget

    # =========================================================================
    # Link button helpers  (called after Wikipedia search saves a link)
    # =========================================================================

    def _rebuild_link_buttons(self):
        """Add / refresh the Wikipedia and MusicBrainz buttons in the header links row.

        Called once during construction and again any time a Wikipedia link is
        saved so the button appears immediately without reopening the editor.
        """
        # Remove old buttons if they exist
        for btn_attr in ("_wiki_btn", "_mb_btn", "_reimport_btn"):
            btn = getattr(self, btn_attr, None)
            if btn is not None:
                self._links_row.removeWidget(btn)
                btn.hide()
                btn.deleteLater()
                setattr(self, btn_attr, None)

        wiki_link = getattr(self.album, "album_wikipedia_link", None)
        # Also check the live widget value in case the user typed it in Advanced tab
        if not wiki_link:
            w = self.field_widgets.get("album_wikipedia_link")
            if w is not None and hasattr(w, "text"):
                wiki_link = w.text().strip() or None

        if wiki_link:
            self._wiki_btn = QPushButton("🌐 Wikipedia")
            self._wiki_btn.setToolTip(wiki_link)
            _url = wiki_link  # capture for lambda
            self._wiki_btn.clicked.connect(lambda: webbrowser.open(_url))
            self._links_row.insertWidget(0, self._wiki_btn)

        mbid = getattr(self.album, "MBID", None)
        if mbid:
            mb_url = f"https://musicbrainz.org/release/{mbid}"
            self._mb_btn = QPushButton("🎵 MusicBrainz")
            self._mb_btn.setToolTip(mb_url)
            self._mb_btn.clicked.connect(lambda: webbrowser.open(mb_url))
            insert_idx = 1 if self._wiki_btn is not None else 0
            self._links_row.insertWidget(insert_idx, self._mb_btn)

            self._reimport_btn = QPushButton("🔄 Reimport from MusicBrainz")
            self._reimport_btn.setToolTip(
                "Re-fetch this release from MusicBrainz and check for metadata "
                "changes since the last import."
            )
            self._reimport_btn.clicked.connect(self._reimport_musicbrainz)
            self._links_row.insertWidget(insert_idx + 1, self._reimport_btn)

    # =========================================================================
    # Alias helpers  (called by AliasesTab, kept on the editor for
    # easy access from _rebuild_current_tab)
    # =========================================================================

    def _refresh_aliases_list(self):
        """Rebuild the alias list widget from the current album object."""
        clear_layout(self.aliases_layout)

        aliases = getattr(self.album, "album_aliases", []) or []
        if not aliases:
            self.aliases_layout.addWidget(QLabel("No aliases yet."))
            return

        for alias in aliases:
            row_widget = QWidget()
            row = QHBoxLayout(row_widget)
            row.setContentsMargins(0, 0, 0, 0)
            name_lbl = QLabel(f"<b>{alias.alias_name}</b>")
            type_lbl = QLabel(alias.alias_type or "—")
            type_lbl.setProperty("textRole", "muted")
            remove_btn = QPushButton("✕ Remove")
            remove_btn.setFixedWidth(90)
            remove_btn.setProperty("danger", True)
            remove_btn.clicked.connect(
                lambda checked=False, a=alias: self._remove_alias(a)
            )
            row.addWidget(name_lbl, 2)
            row.addWidget(type_lbl, 1)
            row.addWidget(remove_btn)
            self.aliases_layout.addWidget(row_widget)

    def _add_alias(self):
        alias_name = self.new_alias_name.text().strip()
        if not alias_name:
            QMessageBox.warning(self, "Missing Name", "Please enter an alias name.")
            return
        alias_type = self.new_alias_type.text().strip() or None
        try:
            self.controller.add.add_entity(
                "AlbumAlias",
                album_id=self.album.album_id,
                alias_name=alias_name,
                alias_type=alias_type,
            )
            self.new_alias_name.clear()
            self.new_alias_type.clear()
            self.album = self.controller.get.get_entity_object(
                "Album", album_id=self.album.album_id
            )
            self._refresh_aliases_list()
        except SQLAlchemyError as e:
            logger.exception("Failed to add album alias")
            QMessageBox.critical(self, "Error", f"Could not add alias: {e}")

    def _remove_alias(self, alias):
        confirm = QMessageBox.question(
            self,
            "Remove Alias",
            f"Remove alias '{getattr(alias, 'alias_name', alias)}'?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return
        try:
            self.controller.delete.delete_entity("AlbumAlias", alias.alias_id)
            self.album = self.controller.get.get_entity_object(
                "Album", album_id=self.album.album_id
            )
            self._refresh_aliases_list()
        except SQLAlchemyError as e:
            logger.exception("Failed to remove album alias")
            QMessageBox.critical(self, "Error", f"Could not remove alias: {e}")

    # =========================================================================
    # Dialog buttons
    # =========================================================================

    def _add_dialog_buttons(self, layout):
        """Save + Cancel."""
        button_box = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        button_box.accepted.connect(self.save_changes)
        button_box.rejected.connect(self.close)

        # Disabled while a background cover embed is running so the dialog
        # can't be dismissed mid-rewrite, leaving some tracks embedded and
        # others not (see AlbumCoverArtMixin._set_cover_controls_enabled).
        self._dialog_button_box = button_box

        layout.addWidget(button_box)

    def setup_connections(self):
        """Wire up any extra signal connections."""

    # =========================================================================
    # Unsaved-changes guard
    # =========================================================================

    def closeEvent(self, event):
        if self._has_unsaved_changes():
            reply = QMessageBox.question(
                self,
                "Unsaved Changes",
                "You have unsaved changes. Close without saving?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if reply == QMessageBox.No:
                event.ignore()
                return
        event.accept()

    def _has_unsaved_changes(self) -> bool:
        return bool(self._collect_changed_fields())

    def _collect_changed_fields(self) -> dict:
        """Compare each editable widget's current value against the value
        loaded from the album and return only the fields that actually
        differ — untouched fields are omitted so save doesn't rewrite them."""
        changes = {}
        for field_name, widget in self.field_widgets.items():
            if field_name == "album_description":
                continue
            field_config = ALBUM_FIELDS.get(field_name)
            if not (field_config and field_config.editable):
                continue
            current = AlbumUIComponents.get_field_value(widget, field_config.type)
            if field_name == "release_type":
                current = normalize_release_type(current)
            original = getattr(self.album, field_name, None)
            if value_changed(original, current):
                changes[field_name] = current

        if self.desc_widget is not None:
            if hasattr(self.desc_widget, "toPlainText"):
                desc_val = self.desc_widget.toPlainText().strip() or None
            elif hasattr(self.desc_widget, "text"):
                desc_val = self.desc_widget.text().strip() or None
            else:
                desc_val = None
            if value_changed(self.album.album_description, desc_val):
                changes["album_description"] = desc_val

        return changes

    # =========================================================================
    # Save / refresh
    # =========================================================================

    def save_changes(self):
        try:
            kwargs = self._collect_changed_fields()
            if kwargs:
                self.controller.update.update_entity(
                    "Album", self.album.album_id, **kwargs
                )
            self.accept()

        except SQLAlchemyError as e:
            logger.error(f"Error saving album changes: {e}")
            QMessageBox.critical(self, "Error", f"Failed to save changes: {e}")

    # Tabs whose data can be mutated from more than one place -- e.g. an
    # artist credit can move between Track Credits and Album credit via
    # the convert-to-album/convert-to-per-track buttons -- so both are kept
    # in sync unconditionally, the same way Publishers & Places already was.
    # Genres and Track Credits also both snapshot self.album.tracks at
    # build time, so a track added/removed on the Tracks tab (see
    # DiscManagementView.tracks_changed) makes them stale the same way.
    _ALWAYS_REFRESHED_TABS: ClassVar[tuple[str, ...]] = (
        "Publishers && Places",
        "Album credit",
        "Track Credits",
        "Genres",
    )

    def refresh_view(self):
        """Called by RelationshipHelpers after any relationship change.

        Always rebuilds the tabs listed in _ALWAYS_REFRESHED_TABS so a
        change made from one of them is immediately visible on the others
        without switching tabs. Also rebuilds the currently active tab if
        it isn't already one of those.
        """
        try:
            updated = self.controller.get.get_entity_object(
                "Album", album_id=self.album.album_id
            )
            if updated:
                # get_entity_object returns the SAME identity-mapped Album
                # instance (this session never expunges it), and every write
                # that lands here -- RelationshipHelpers' add/remove/move on
                # AlbumRoleAssociation, AlbumPublisher, PlaceAssociation,
                # AwardAssociation, a track add/delete, etc. -- goes through
                # the DB layer directly rather than mutating `updated`'s own
                # relationship collections (album_roles, publisher_associations,
                # places, awards, tracks, discs, ...). The session is opened
                # with expire_on_commit=False (src/db/db_engine.py), so the
                # commit() those writes already did doesn't invalidate an
                # already-cached collection either -- without this, a tab
                # rebuilt below (even one in _ALWAYS_REFRESHED_TABS) would
                # just redisplay the same pre-edit list. Expiring forces the
                # next access of any of `updated`'s attributes to reload from
                # the DB actually just written to.
                self.controller.get.session.expire(updated)
                self.album = updated
                self.helper.album = updated
                self.tab_builder.album = updated

            for title in self._ALWAYS_REFRESHED_TABS:
                self._rebuild_tab_by_title(title)

            # Only rebuild the current tab if it wasn't already refreshed
            # above — avoids a double-rebuild (and the flash of the tab
            # switching) when the user is already on one of those tabs.
            current_title = self.tabs.tabText(self.tabs.currentIndex())
            if current_title not in self._ALWAYS_REFRESHED_TABS:
                self._rebuild_current_tab()

            logger.info("Album view refreshed")
        except SQLAlchemyError as e:
            logger.error(f"Error refreshing album view: {e}")

    # =========================================================================
    # Tab rebuild helpers
    # =========================================================================

    def _get_tab_rebuild_map(self) -> dict:
        """Return a mapping of tab title → builder callable."""
        return {
            "Overview": self._build_header_section,
            "Details": lambda: DetailsTab(self).build(),
            "Tracks": lambda: TracksTab(self).build(),
            "Artwork": lambda: ArtworkTab(self).build(),
            "Aliases": lambda: AliasesTab(self).build(),
            "Genres": lambda: GenresTab(self).build(),
            "Track Credits": lambda: TrackCreditsTab(self).build(),
            "Album credit": self.tab_builder.build_artists_tab,
            "Publishers && Places": self.tab_builder.build_relationships_tab,
            "Awards": self.tab_builder.build_awards_tab,
            "Advanced": lambda: AdvancedTab(self).build(),
        }

    @staticmethod
    def _capture_scroll_positions(widget: QWidget) -> list[int]:
        """Return the vertical scroll value of every scroll area in widget,
        in traversal order, so it can be reapplied to a rebuilt replacement."""
        return [
            area.verticalScrollBar().value()
            for area in widget.findChildren(QAbstractScrollArea)
        ]

    @staticmethod
    def _restore_scroll_positions(widget: QWidget, positions: list[int]):
        """Reapply values captured by _capture_scroll_positions to the
        corresponding scroll areas of a rebuilt tab widget.

        Deferred via singleShot(0, ...): a just-inserted QScrollArea hasn't
        had its resize/layout event processed yet, so its scrollbar range
        (and therefore setValue's clamp) isn't valid until the event loop
        catches up.
        """

        def _apply():
            areas = widget.findChildren(QAbstractScrollArea)
            for area, value in zip(areas, positions):
                area.verticalScrollBar().setValue(value)

        QTimer.singleShot(0, _apply)

    def _refresh_track_credits_tab(self, idx: int) -> bool:
        """Reload the Track Credits tab's RolesTab in place rather than
        tearing down and reconstructing the whole tab (as the generic
        rebuild path below does).

        RolesTab.load() runs its row fetch on a background QThread and
        already preserves scroll position correctly around that (see
        RolesTab._on_roles_loaded). Replacing the tab widget wholesale on
        every refresh_view() call raced that: the generic capture/restore
        below fires via QTimer.singleShot(0, ...) before the async load on
        the brand-new RolesTab instance finishes, so it always found an
        empty (0-row, 0-max-scroll) table and clamped the restore to 0 --
        which RolesTab's own restore then just reconfirmed. Reusing the
        existing instance sidesteps that race entirely.

        Returns False (caller should fall back to a full rebuild) if
        there's no existing RolesTab to reuse -- e.g. the tab was last
        built with no tracks and is showing its placeholder label instead.
        """
        tab_widget = self.tabs.widget(idx)
        roles_widget = tab_widget.findChild(RolesTab) if tab_widget else None
        if roles_widget is None:
            return False
        roles_widget.load(self.album.tracks or [])
        return True

    def _rebuild_current_tab(self):
        """Replace the currently visible tab with a freshly built version."""
        try:
            idx = self.tabs.currentIndex()
            tab_title = self.tabs.tabText(idx)
            if tab_title == "Track Credits" and self._refresh_track_credits_tab(idx):
                return
            builder = self._get_tab_rebuild_map().get(tab_title)
            if builder:
                old_tab = self.tabs.widget(idx)
                scroll_positions = self._capture_scroll_positions(old_tab)
                new_tab = builder()
                self.tabs.removeTab(idx)
                self.tabs.insertTab(idx, new_tab, tab_title)
                self.tabs.setCurrentIndex(idx)
                self._restore_scroll_positions(new_tab, scroll_positions)
        except (SQLAlchemyError, sqlite3.Error) as e:
            logger.error(f"Error rebuilding tab: {e}")

    def _rebuild_tab_by_title(self, title: str):
        """Find a tab by its title and rebuild it (used for background refreshes)."""
        try:
            rebuild_map = self._get_tab_rebuild_map()
            builder = rebuild_map.get(title)
            if not builder:
                return
            for idx in range(self.tabs.count()):
                if self.tabs.tabText(idx) == title:
                    if title == "Track Credits" and self._refresh_track_credits_tab(
                        idx
                    ):
                        return
                    was_current = self.tabs.currentIndex() == idx
                    old_tab = self.tabs.widget(idx)
                    scroll_positions = self._capture_scroll_positions(old_tab)
                    new_tab = builder()
                    self.tabs.removeTab(idx)
                    self.tabs.insertTab(idx, new_tab, title)
                    # Don't change the active tab — just silently update it.
                    # removeTab() auto-advances the selection if the removed
                    # tab was the current one, so restore it explicitly.
                    if was_current:
                        self.tabs.setCurrentIndex(idx)
                    self._restore_scroll_positions(new_tab, scroll_positions)
                    return
        except (SQLAlchemyError, sqlite3.Error) as e:
            logger.error(f"Error rebuilding tab '{title}': {e}")

    # =========================================================================
    # Sub-dialog close hook
    # =========================================================================

    def _on_subdialog_closed(self, result=None):
        """Called whenever a sub-dialog (aliases, etc.) closes."""
        try:
            updated = self.controller.get.get_entity_object(
                "Album", album_id=self.album.album_id
            )
            if updated:
                # Same staleness hazard as refresh_view() above -- expire so
                # whatever the sub-dialog changed (aliases, etc.) is actually
                # visible instead of a cached pre-edit relationship collection.
                self.controller.get.session.expire(updated)
                self.album = updated
                self.helper.album = updated
                self.tab_builder.album = updated
            self._rebuild_current_tab()
        except SQLAlchemyError as e:
            logger.error(f"Error refreshing after sub-dialog close: {e}")

    # =========================================================================
    # Place associations helper (called by album_tab.py)
    # =========================================================================

    def get_album_place_associations(self):
        """Return place associations for the current album."""
        try:
            return (
                self.controller.get.get_all_entities(
                    "PlaceAssociation",
                    entity_id=self.album.album_id,
                    entity_type="Album",
                )
                or []
            )
        except SQLAlchemyError as e:
            logger.error(f"Error loading place associations: {e}")
            return []

    # =========================================================================
    # Sizing helper
    # =========================================================================

    def _fit_to_screen(self):
        """Resize the dialog to fit contents, capped at 90% of the screen, then center."""
        self.adjustSize()
        screen = QApplication.primaryScreen()
        if screen:
            available = screen.availableGeometry()
            max_w = int(available.width() * 0.90)
            max_h = int(available.height() * 0.90)
            w = max(min(self.sizeHint().width(), max_w), 1100)
            h = max(min(self.sizeHint().height(), max_h), 750)
            self.resize(w, h)
            x = available.x() + (available.width() - w) // 2
            y = available.y() + (available.height() - h) // 2
            self.move(x, y)
