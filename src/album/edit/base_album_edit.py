"""AlbumEditor: the tabbed dialog for editing one album and its relationships."""

from collections.abc import Callable
import html
import sqlite3
from typing import ClassVar
import webbrowser

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractScrollArea,
    QApplication,
    QComboBox,
    QCompleter,
    QDialog,
    QDialogButtonBox,
    QFrame,
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
from sqlalchemy.orm.exc import DetachedInstanceError

from src.album.edit.album_components import AlbumUIComponents
from src.album.edit.album_cover_art_mixin import AlbumCoverArtMixin
from src.album.edit.album_editing_relationship_helpers import RelationshipHelpers
from src.album.edit.album_tab import AlbumTabBuilder
from src.album.edit.base_album_edit_tabs import AdvancedTab, AliasesTab, ArtworkTab, DetailsTab, GenresTab, TrackCreditsTab, TracksTab
from src.album.musicbrainz.album_musicbrainz_mixin import AlbumMusicBrainzMixin
from src.album.release_type_utils import RELEASE_TYPE_SUGGESTIONS, normalize_release_type
from src.common.edit_dirty import value_changed
from src.common.widgets.layout_utils import clear_layout
from src.common.widgets.nullable_numeric_field import create_nullable_int_field
from src.db.db_mapping_albums import ALBUM_FIELDS
from src.foundation.config_setup import Config
from src.foundation.logger_config import logger
from src.metadata.writers.metadata_writer import MetadataWriter
from src.track.edit.track_edit_roles import RolesTab

# Fallback suggestions, merged with the values already in the library.
ALBUM_LANGUAGE_SUGGESTIONS = ["English", "French", "German", "Italian", "Spanish", "Portuguese", "Japanese", "Korean", "Chinese", "Russian", "Instrumental", "Multiple"]
STATUS_SUGGESTIONS = ["Official", "Promotional", "Bootleg", "Withdrawn", "Expunged", "Cancelled"]
MEDIA_FORMAT_SUGGESTIONS = ["CD", '12" Vinyl', '7" Vinyl', "Vinyl", "Digital Media", "Cassette", "SACD", "Hybrid SACD", "DVD-Video", "DVD-Audio", "Blu-ray"]
_SUGGESTION_FALLBACKS = {"album_language": ALBUM_LANGUAGE_SUGGESTIONS, "release_type": RELEASE_TYPE_SUGGESTIONS, "status": STATUS_SUGGESTIONS, "media_format": MEDIA_FORMAT_SUGGESTIONS}

# Nullable int fields edited as QLineEdits; ranges match the album table's CHECK constraints.
_NULLABLE_INT_RANGES = {"release_year": (1, 9999), "release_month": (1, 12), "release_day": (1, 31), "estimated_sales": (0, 999_999_999)}


class AlbumEditor(AlbumCoverArtMixin, AlbumMusicBrainzMixin, QDialog):
    """Tabbed album editor: metadata, tracks, artwork, aliases, genres, credits, relationships and stats."""

    # Shared by every editor so the full album scan for completer values runs once per session.
    _suggestion_cache: ClassVar[dict[str, list[str]] | None] = None

    # Tabs whose data another tab can change (credits move between Track Credits and
    # Album credit; Genres/Track Credits snapshot album.tracks), so refresh_view marks them stale.
    _ALWAYS_REFRESHED_TABS: ClassVar[tuple[str, ...]] = ("Publishers && Places", "Album credit", "Track Credits", "Genres")

    def __init__(self, controller, album, parent=None):
        super().__init__(parent)

        # Float freely — not locked to the parent window's position.
        self.setWindowFlag(Qt.Window, True)

        self.controller = controller
        self._config = Config()
        self._metadata_writer = MetadataWriter(controller)

        # Reload on open so a reopened editor never shows stale cover paths.
        try:
            fresh = controller.get.get_entity_object("Album", album_id=album.album_id)
            self.album = fresh if fresh is not None else album
        except SQLAlchemyError as e:
            logger.warning(f"Failed to reload album {album.album_id} on editor open: {e}")
            self.album = album

        self.helper = RelationshipHelpers(controller, self.album, self.refresh_view, widget=self)
        self.field_widgets: dict = {}
        self.tab_builder = AlbumTabBuilder(self)
        self._stale_tabs: set[str] = set()

        self.setWindowTitle(f"Edit Album: {self.album.album_name}")
        self.setMinimumSize(1100, 750)

        self.init_editable_widgets()
        self.init_ui()

        # Stop any in-flight cover embed on every exit path so its signals never reach a closed dialog.
        self.finished.connect(lambda _result: self._cleanup_cover_embed())

        self._fit_to_screen()

    # =========================================================================
    # Widget initialisation
    # =========================================================================

    def init_editable_widgets(self):
        """Create one widget per editable ALBUM_FIELD, pre-filled from the album."""
        for field_name, field_config in ALBUM_FIELDS.items():
            if not field_config.editable:
                continue
            current_value = getattr(self.album, field_name, None)

            if field_config.type is int and field_name in _NULLABLE_INT_RANGES:
                # A QLineEdit (not a QSpinBox) so the value can be cleared back to NULL.
                min_val, max_val = _NULLABLE_INT_RANGES[field_name]
                widget = create_nullable_int_field(min_val=min_val, max_val=max_val, current_value=current_value, group_separator=(field_name == "estimated_sales"))
            else:
                widget = AlbumUIComponents.create_editable_field(field_config, current_value)
            self.field_widgets[field_name] = widget

        suggestions = self._get_field_suggestions()
        self._attach_completer("album_language", suggestions["album_language"])
        self._attach_completer("release_type", suggestions["release_type"])
        self._attach_completer("media_format", suggestions["media_format"])
        self._setup_status_combo(suggestions["status"])

    def _setup_status_combo(self, suggestions):
        """Replace the plain status field with a dropdown of known statuses."""
        field_config = ALBUM_FIELDS.get("status")
        if not field_config or not field_config.editable:
            return
        current_value = getattr(self.album, "status", None)
        combo = QComboBox()
        combo.setEditable(True)
        combo.setInsertPolicy(QComboBox.NoInsert)
        combo.addItem("")  # allows clearing the field back to NULL
        combo.addItems(suggestions)
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

    def _get_field_suggestions(self) -> dict[str, list[str]]:
        """Return the cached completer values for every suggestion field, loading them on first use."""
        if AlbumEditor._suggestion_cache is None:
            AlbumEditor._suggestion_cache = self._fetch_field_suggestions()
        return AlbumEditor._suggestion_cache

    def _fetch_field_suggestions(self) -> dict[str, list[str]]:
        """Merge each field's fallback list with the distinct values used across the library."""
        # Case-insensitive dedupe ("Album" and "album" are one entry); first casing seen wins.
        merged = {field: {value.lower(): value for value in fallback} for field, fallback in _SUGGESTION_FALLBACKS.items()}
        try:
            for album in self.controller.get.get_all_entities("Album") or []:
                for field, values in merged.items():
                    value = (getattr(album, field, None) or "").strip()
                    if value:
                        values.setdefault(value.lower(), value)
        except SQLAlchemyError:
            logger.debug("Could not fetch album field suggestions", exc_info=True)
        return {field: sorted(values.values(), key=str.lower) for field, values in merged.items()}

    # =========================================================================
    # Main UI layout
    # =========================================================================

    def _tab_builders(self) -> dict[str, Callable[[], QWidget]]:
        """Return tab title → builder callable, in display order."""
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

    def init_ui(self):
        """Build the full dialog layout."""
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(15, 15, 15, 15)
        main_layout.setSpacing(12)

        self.tabs = QTabWidget()
        self.tabs.setTabPosition(QTabWidget.North)
        for title, builder in self._tab_builders().items():
            self.tabs.addTab(builder(), title)
        self.tabs.currentChanged.connect(self._on_tab_changed)

        main_layout.addWidget(self.tabs)

        self._add_dialog_buttons(main_layout)

        wiki_widget = self.field_widgets.get("album_wikipedia_link")
        if isinstance(wiki_widget, QLineEdit):
            wiki_widget.editingFinished.connect(self._rebuild_link_buttons)

    # =========================================================================
    # Header section  (cover thumbnail + editable info)
    # =========================================================================

    def _build_header_section(self):
        """Build the Overview tab: cover on the left, editable album info on the right, pinned to the top."""
        container = QWidget()
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.setSpacing(0)

        header = QWidget()
        header.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        row = QHBoxLayout(header)
        row.setSpacing(20)

        row.addWidget(self._build_cover_widget())
        row.addWidget(self._build_info_section(), 1)

        container_layout.addWidget(header, 0, Qt.AlignTop)
        container_layout.addStretch()

        return container

    def _build_cover_widget(self):
        """Cover thumbnail only — no Change Cover button (use Artwork tab)."""
        widget = QWidget()
        widget.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)  # keep the cover from stretching sideways
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
        widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)  # keep the info column from stretching vertically
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

        # Release date — labelled fields framed as one chip so the three parts read as one control.
        date_frame = QFrame()
        date_frame.setObjectName("DateChipGroup")
        date_group = QHBoxLayout(date_frame)
        date_group.setContentsMargins(10, 6, 10, 6)
        date_group.setSpacing(12)
        released_lbl = QLabel("Released:")
        released_lbl.setProperty("textRole", "muted")
        date_group.addWidget(released_lbl)
        for field, label_text in (("release_year", "Year"), ("release_month", "Month"), ("release_day", "Day")):
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
        date_row = QHBoxLayout()
        date_row.addWidget(date_frame)
        date_row.addStretch()
        layout.addLayout(date_row)

        desc_label = QLabel("Description")
        desc_label.setProperty("title", True)
        layout.addWidget(desc_label)

        self.desc_widget = self.field_widgets.get("album_description")
        if self.desc_widget is None:
            self.desc_widget = QTextEdit()
            self.desc_widget.setPlainText(self.album.album_description or "")

        self.desc_widget.setMinimumHeight(100)
        self.desc_widget.setMaximumHeight(200)
        self.desc_widget.setObjectName("AlbumDescriptionEdit")
        self.desc_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        layout.addWidget(self.desc_widget)

        self._links_row = QHBoxLayout()
        self._links_row.setSpacing(8)

        self.lookup_button = QPushButton("🎵 Look Up on MusicBrainz")
        self.lookup_button.setObjectName("PrimaryButton")
        self.lookup_button.setToolTip(
            "Search MusicBrainz releases by album title/artist and fill in blank fields "
            "(MBID, release type, live/compilation, release date) from the selected match. "
            "Never overwrites fields you've already filled in."
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
    # Link buttons
    # =========================================================================

    def _rebuild_link_buttons(self):
        """Add or refresh the Wikipedia, MusicBrainz and Reimport buttons in the header links row."""
        for btn_attr in ("_wiki_btn", "_mb_btn", "_reimport_btn"):
            btn = getattr(self, btn_attr, None)
            if btn is not None:
                self._links_row.removeWidget(btn)
                btn.hide()
                btn.deleteLater()
                setattr(self, btn_attr, None)

        # The live field value wins so an edited (or cleared) link shows at once.
        wiki_widget = self.field_widgets.get("album_wikipedia_link")
        wiki_link = wiki_widget.text().strip() if isinstance(wiki_widget, QLineEdit) else getattr(self.album, "album_wikipedia_link", None)

        if wiki_link:
            self._wiki_btn = QPushButton("🌐 Wikipedia")
            self._wiki_btn.setToolTip(wiki_link)
            self._wiki_btn.clicked.connect(lambda: webbrowser.open(wiki_link))
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
            self._reimport_btn.setToolTip("Re-fetch this release from MusicBrainz and check for metadata changes since the last import.")
            self._reimport_btn.clicked.connect(self._reimport_musicbrainz)
            self._links_row.insertWidget(insert_idx + 1, self._reimport_btn)

    # =========================================================================
    # Alias helpers  (called by AliasesTab)
    # =========================================================================

    def _refresh_aliases_list(self):
        """Rebuild the alias list widget from the current album object."""
        clear_layout(self.aliases_layout)

        aliases = getattr(self.album, "album_aliases", []) or []
        if not aliases:
            empty_lbl = QLabel("No aliases yet.")
            empty_lbl.setProperty("textRole", "placeholder")
            self.aliases_layout.addWidget(empty_lbl)
            return

        for alias in aliases:
            row_widget = QFrame()
            row_widget.setObjectName("AliasRow")
            row = QHBoxLayout(row_widget)
            row.setContentsMargins(12, 8, 10, 8)
            name_lbl = QLabel(f"<b>{html.escape(alias.alias_name or '')}</b>")
            type_lbl = QLabel(alias.alias_type or "—")
            type_lbl.setProperty("textRole", "muted")
            remove_btn = QPushButton("✕ Remove")
            remove_btn.setFixedWidth(90)
            remove_btn.setProperty("danger", True)
            remove_btn.setAccessibleName(f"Remove alias {alias.alias_name}")
            remove_btn.clicked.connect(lambda checked=False, a=alias: self._remove_alias(a))
            row.addWidget(name_lbl, 2)
            row.addWidget(type_lbl, 1)
            row.addWidget(remove_btn)
            self.aliases_layout.addWidget(row_widget)

    def _add_alias(self):
        """Add the alias typed in the Aliases tab and refresh the list."""
        alias_name = self.new_alias_name.text().strip()
        if not alias_name:
            QMessageBox.warning(self, "Missing Name", "Please enter an alias name.")
            return
        alias_type = self.new_alias_type.text().strip() or None
        try:
            if self.controller.add.add_entity("AlbumAlias", album_id=self.album.album_id, alias_name=alias_name, alias_type=alias_type) is None:
                QMessageBox.critical(self, "Error", "Could not add alias.")
                return
            self.new_alias_name.clear()
            self.new_alias_type.clear()
            self._reload_album()
            self._refresh_aliases_list()
        except SQLAlchemyError as e:
            logger.exception("Failed to add album alias")
            QMessageBox.critical(self, "Error", f"Could not add alias: {e}")

    def _remove_alias(self, alias):
        """Ask for confirmation, then delete the alias and refresh the list."""
        confirm = QMessageBox.question(self, "Remove Alias", f"Remove alias '{getattr(alias, 'alias_name', alias)}'?", QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if confirm != QMessageBox.Yes:
            return
        try:
            if not self.controller.delete.delete_entity("AlbumAlias", alias.alias_id):
                QMessageBox.critical(self, "Error", "Could not remove alias.")
                return
            self._reload_album()
            self._refresh_aliases_list()
        except SQLAlchemyError as e:
            logger.exception("Failed to remove album alias")
            QMessageBox.critical(self, "Error", f"Could not remove alias: {e}")

    # =========================================================================
    # Dialog buttons
    # =========================================================================

    def _add_dialog_buttons(self, layout):
        """Add the Save and Cancel buttons."""
        button_box = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        button_box.accepted.connect(self.save_changes)
        button_box.rejected.connect(self.reject)

        # Disabled during a cover embed (see AlbumCoverArtMixin._set_cover_controls_enabled).
        self._dialog_button_box = button_box

        layout.addWidget(button_box)

    # =========================================================================
    # Unsaved-changes guard
    # =========================================================================

    def reject(self):
        """Close without saving, unless a cover embed is running or the user keeps unsaved changes."""
        # Cancel, Esc and the window close button (via QDialog.closeEvent) all land here.
        worker = getattr(self, "_cover_embed_worker", None)
        if worker is not None and worker.isRunning():
            QMessageBox.information(self, "Cover Art In Progress", "Wait for the cover art to finish updating before you close the editor.")
            return
        try:
            has_changes = self._has_unsaved_changes()
        except DetachedInstanceError:
            # App shutdown closes the DB session before this dialog; nothing to compare then.
            logger.info("Album is detached from its session; closing without unsaved-changes check")
            has_changes = False
        if has_changes:
            reply = QMessageBox.question(self, "Unsaved Changes", "You have unsaved changes. Close without saving?", QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if reply == QMessageBox.No:
                return
        super().reject()

    def _has_unsaved_changes(self) -> bool:
        """Return True when any editable field differs from the loaded album."""
        return bool(self._collect_changed_fields())

    def _collect_changed_fields(self) -> dict:
        """Return {field: new value} for each editable field whose widget differs from the album."""
        changes = {}
        for field_name, widget in self.field_widgets.items():
            field_config = ALBUM_FIELDS.get(field_name)
            if not (field_config and field_config.editable):
                continue
            current = AlbumUIComponents.get_field_value(widget, field_config.type)
            if field_name == "release_type":
                current = normalize_release_type(current)
            original = getattr(self.album, field_name, None)
            if value_changed(original, current):
                changes[field_name] = current
        return changes

    def _validation_error(self) -> str | None:
        """Return a message for the first field that cannot be saved, or None when all are valid."""
        name_widget = self.field_widgets.get("album_name")
        if isinstance(name_widget, QLineEdit) and not name_widget.text().strip():
            return "The album name cannot be empty."
        for field_name, (min_val, max_val) in _NULLABLE_INT_RANGES.items():
            widget = self.field_widgets.get(field_name)
            # An "intermediate" value (e.g. month 0) passes the validator while typing but fails the DB CHECK.
            if isinstance(widget, QLineEdit) and widget.text().strip() and not widget.hasAcceptableInput():
                friendly = ALBUM_FIELDS[field_name].friendly or field_name
                return f"{friendly} must be between {min_val:,} and {max_val:,}, or empty."
        return None

    # =========================================================================
    # Save / refresh
    # =========================================================================

    def save_changes(self):
        """Validate and write the changed fields, then close; stay open and warn on failure."""
        error = self._validation_error()
        if error:
            QMessageBox.warning(self, "Cannot Save", error)
            return
        try:
            kwargs = self._collect_changed_fields()
            # update_entity returns False (never raises) when the write fails.
            if kwargs and not self.controller.update.update_entity("Album", self.album.album_id, **kwargs):
                QMessageBox.critical(self, "Error", "Failed to save changes. Your edits are still in the editor.")
                return
            self.accept()
        except SQLAlchemyError as e:
            logger.error(f"Error saving album changes: {e}")
            QMessageBox.critical(self, "Error", f"Failed to save changes: {e}")

    def _reload_album(self):
        """Re-fetch and expire the album so relationship collections reload from the DB."""
        updated = self.controller.get.get_entity_object("Album", album_id=self.album.album_id)
        if not updated:
            return
        # The identity map returns the same instance and expire_on_commit=False keeps its
        # cached collections, so writes made through the DB layer stay invisible without this.
        self.controller.get.session.expire(updated)
        self.album = updated
        self.helper.album = updated
        self.tab_builder.album = updated

    def refresh_view(self):
        """Reload the album after a relationship change; rebuild the current tab and mark dependent tabs stale."""
        try:
            self._reload_album()
            self._rebuild_link_buttons()

            current_title = self.tabs.tabText(self.tabs.currentIndex())
            self._stale_tabs.update(title for title in self._ALWAYS_REFRESHED_TABS if title != current_title)
            self._stale_tabs.discard(current_title)
            self._rebuild_tab_by_title(current_title)

            logger.info("Album view refreshed")
        except SQLAlchemyError as e:
            logger.error(f"Error refreshing album view: {e}")

    def _on_tab_changed(self, idx: int):
        """Rebuild a stale tab when the user switches to it."""
        title = self.tabs.tabText(idx)
        if title in self._stale_tabs:
            self._stale_tabs.discard(title)
            self._rebuild_tab_by_title(title)

    # =========================================================================
    # Tab rebuild helpers
    # =========================================================================

    @staticmethod
    def _capture_scroll_positions(widget: QWidget) -> list[int]:
        """Return the vertical scroll value of every scroll area in widget, in traversal order."""
        return [area.verticalScrollBar().value() for area in widget.findChildren(QAbstractScrollArea)]

    @staticmethod
    def _restore_scroll_positions(widget: QWidget, positions: list[int]):
        """Reapply captured scroll values to the matching scroll areas of a rebuilt tab."""

        def _apply():
            areas = widget.findChildren(QAbstractScrollArea)
            for area, value in zip(areas, positions, strict=False):
                area.verticalScrollBar().setValue(value)

        # Deferred: a new scroll area's range is not valid until its first layout pass.
        # widget is the context object, so Qt drops the call if the tab is deleted first.
        QTimer.singleShot(0, widget, _apply)

    def _refresh_track_credits_tab(self, idx: int) -> bool:
        """Reload the existing Track Credits RolesTab in place; return False when there is none to reuse."""
        # A new RolesTab loads async, so a full rebuild would restore scroll against an empty table.
        tab_widget = self.tabs.widget(idx)
        roles_widget = tab_widget.findChild(RolesTab) if tab_widget else None
        if roles_widget is None:
            return False
        roles_widget.load(self.album.tracks or [])
        return True

    def _replace_tab(self, idx: int, title: str, builder: Callable[[], QWidget]):
        """Swap the tab at idx for a freshly built one, keeping selection and scroll position."""
        old_tab = self.tabs.widget(idx)
        scroll_positions = self._capture_scroll_positions(old_tab)
        new_tab = builder()
        was_current = self.tabs.currentIndex() == idx
        # Block currentChanged: removeTab moves the selection, which would rebuild another stale tab.
        self.tabs.blockSignals(True)
        try:
            self.tabs.removeTab(idx)
            self.tabs.insertTab(idx, new_tab, title)
            if was_current:
                self.tabs.setCurrentIndex(idx)
        finally:
            self.tabs.blockSignals(False)
        # removeTab does not delete the page; stop RolesTab loader threads before deleting it.
        for roles_widget in old_tab.findChildren(RolesTab):
            roles_widget.cleanup()
        old_tab.deleteLater()
        self._restore_scroll_positions(new_tab, scroll_positions)

    def _rebuild_current_tab(self):
        """Replace the currently visible tab with a freshly built version."""
        self._rebuild_tab_by_title(self.tabs.tabText(self.tabs.currentIndex()))

    def _rebuild_tab_by_title(self, title: str):
        """Find a tab by its title and rebuild it."""
        try:
            builder = self._tab_builders().get(title)
            if not builder:
                return
            for idx in range(self.tabs.count()):
                if self.tabs.tabText(idx) == title:
                    if title == "Track Credits" and self._refresh_track_credits_tab(idx):
                        return
                    self._replace_tab(idx, title, builder)
                    return
        except (SQLAlchemyError, sqlite3.Error) as e:
            logger.error(f"Error rebuilding tab '{title}': {e}")

    # =========================================================================
    # Sub-dialog close hook
    # =========================================================================

    def _on_subdialog_closed(self, result=None):
        """Reload the album and rebuild the current tab after a sub-dialog closes."""
        try:
            self._reload_album()
            self._rebuild_current_tab()
        except SQLAlchemyError as e:
            logger.error(f"Error refreshing after sub-dialog close: {e}")

    # =========================================================================
    # Place associations helper (called by album_tab.py)
    # =========================================================================

    def get_album_place_associations(self):
        """Return place associations for the current album."""
        try:
            return self.controller.get.get_all_entities("PlaceAssociation", entity_id=self.album.album_id, entity_type="Album") or []
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
