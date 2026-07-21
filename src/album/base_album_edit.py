"""
base_album_edit.py
"""

import webbrowser
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QCompleter,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from src.album.album_components import AlbumUIComponents
from src.album.album_editing_relationship_helpers import RelationshipHelpers
from src.album.album_tab import AlbumTabBuilder
from src.album.base_album_edit_tabs import (
    AdvancedTab,
    AliasesTab,
    ArtworkTab,
    DetailsTab,
    TracksTab,
)
from src.common.edit_dirty import value_changed
from src.core.config_setup import Config
from src.db.db_mapping_albums import ALBUM_FIELDS
from src.core.logger_config import logger
from src.image.artwork_cache import get_artwork_cache
from src.metadata.metadata_artwork import ArtworkExtractor
from src.metadata.metadata_writer import MetadataWriter
from src.musicbrainz.musicbrainz_client import search_release_groups
from src.musicbrainz.musicbrainz_match_dialog import MusicBrainzMatchDialog


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
RELEASE_TYPE_SUGGESTIONS = [
    "Album",
    "Single",
    "EP",
    "Compilation",
    "Soundtrack",
    "Live",
    "Remix",
    "Mixtape",
    "Bootleg",
    "Broadcast",
    "Demo",
]


# =============================================================================
# NullableSpinBox — a SpinBox that can be explicitly cleared to None
# =============================================================================


class NullableSpinBox(QWidget):
    """A QSpinBox paired with a 'Set' checkbox.

    When the checkbox is unchecked the value is treated as NULL on save.
    When checked the spin-box value is used.

    This solves the problem of not being able to clear a QSpinBox back to NULL
    once a value has been entered.
    """

    def __init__(
        self, min_val: int = 0, max_val: int = 9999, current_value=None, parent=None
    ):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self._spin = QSpinBox()
        self._spin.setRange(min_val, max_val)

        self._check = QCheckBox("Set")
        self._check.setToolTip("Uncheck to save this field as empty (no value).")

        if current_value is not None:
            self._spin.setValue(int(current_value))
            self._check.setChecked(True)
        else:
            self._spin.setValue(min_val)
            self._check.setChecked(False)
            self._spin.setEnabled(False)

        self._check.toggled.connect(self._spin.setEnabled)

        layout.addWidget(self._check)
        layout.addWidget(self._spin)
        layout.addStretch()

    def value(self):
        """Return the int value, or None if the checkbox is unchecked."""
        return self._spin.value() if self._check.isChecked() else None


# =============================================================================
# AlbumEditor — main dialog
# =============================================================================


class AlbumEditor(QDialog):
    """
    Comprehensive album editor dialog with tabbed interface.

    Tabs
    ────
    Details          – core metadata (language, type, catalog #, live/compilation flags, sales, MBID)
    Tracks           – DiscManagementView for disc / track structure
    Artwork          – front cover, rear cover, liner art with pickers
    Aliases          – add / remove / type album aliases
    Artist Credits   – relationship helpers (built by AlbumTabBuilder)
    Publishers & Places – relationship helpers (built by AlbumTabBuilder)
    Awards           – relationship helpers (built by AlbumTabBuilder)
    Advanced         – metadata-complete flag, ReplayGain, Wikipedia link, library stats
    """

    # Caches shared across all AlbumEditor instances/openings so the (expensive)
    # full album-table scan for completer suggestions only happens once per
    # app session, not once per album edit dialog.
    _album_language_cache = None
    _release_type_cache = None

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
        except Exception:
            self.album = album

        self.helper = RelationshipHelpers(controller, album, self.refresh_view, widget=self)
        self.field_widgets: dict = {}
        self.tab_builder = AlbumTabBuilder(self)

        self.setWindowTitle(f"Edit Album: {album.album_name}")
        self.setMinimumSize(1100, 750)

        self.init_editable_widgets()
        self.init_ui()
        self.setup_connections()

        self._fit_to_screen()

    # =========================================================================
    # Widget initialisation
    # =========================================================================

    def init_editable_widgets(self):
        """Create one widget per editable ALBUM_FIELD, pre-filled from the album.

        For integer fields that allow NULL we use NullableSpinBox instead of a
        plain QSpinBox so the user can clear the value back to NULL.
        """
        NULLABLE_INT_FIELDS = {
            "recording_day",
            "recording_month",
            "recording_year",
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
                widget = NullableSpinBox(
                    min_val=int(min_val),
                    max_val=int(max_val),
                    current_value=current_value,
                )
            else:
                widget = AlbumUIComponents.create_editable_field(
                    field_config, current_value
                )
            self.field_widgets[field_name] = widget

        self._attach_completer("album_language", self._get_album_language_suggestions())
        self._attach_completer("release_type", self._get_release_type_suggestions())

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

    def _fetch_field_suggestions(self, field_name, fallback):
        """Distinct values already used for `field_name` across the library,
        merged with a small generic fallback list in case lookup fails."""
        suggestions = set(fallback)
        try:
            albums = self.controller.get.get_all_entities("Album") or []
            for a in albums:
                value = (getattr(a, field_name, None) or "").strip()
                if value:
                    suggestions.add(value)
        except Exception:
            pass
        return sorted(suggestions, key=str.lower)

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
        self._advanced_tab = AdvancedTab(self)

        self.tabs.addTab(self._build_header_section(), "Overview")
        self.tabs.addTab(self._details_tab.build(), "Details")
        self.tabs.addTab(self._tracks_tab.build(), "Tracks")
        self.tabs.addTab(self._artwork_tab.build(), "Artwork")
        self.tabs.addTab(self._aliases_tab.build(), "Aliases")
        self.tabs.addTab(self.tab_builder.build_artists_tab(), "Artist Credits")
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
        self.cover_label.setFixedSize(150, 150)  # FIX: smaller, was 200
        self.cover_label.setProperty("coverPlaceholder", True)
        self._load_album_cover()
        layout.addWidget(self.cover_label)

        return widget

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
            title_widget.setStyleSheet("font-size: 18px; font-weight: bold;")
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
                w.setFixedWidth(70)
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
        self.desc_widget.setStyleSheet("padding: 2px 4px;")
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
        for btn_attr in ("_wiki_btn", "_mb_btn"):
            btn = getattr(self, btn_attr, None)
            if btn is not None:
                self._links_row.removeWidget(btn)
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
            mb_url = f"https://musicbrainz.org/release-group/{mbid}"
            self._mb_btn = QPushButton("🎵 MusicBrainz")
            self._mb_btn.setToolTip(mb_url)
            self._mb_btn.clicked.connect(lambda: webbrowser.open(mb_url))
            insert_idx = 1 if self._wiki_btn is not None else 0
            self._links_row.insertWidget(insert_idx, self._mb_btn)

    # =========================================================================
    # Alias helpers  (called by AliasesTab, kept on the editor for
    # easy access from _rebuild_current_tab)
    # =========================================================================

    def _refresh_aliases_list(self):
        """Rebuild the alias list widget from the current album object."""
        while self.aliases_layout.count():
            item = self.aliases_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

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
        except Exception as e:
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
        except Exception as e:
            logger.exception("Failed to remove album alias")
            QMessageBox.critical(self, "Error", f"Could not remove alias: {e}")

    # =========================================================================
    # Dialog buttons
    # =========================================================================

    def _add_dialog_buttons(self, layout):
        """Save + Cancel."""
        button_box = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        button_box.accepted.connect(self.save_changes)
        button_box.rejected.connect(self.reject)

        layout.addWidget(button_box)

    def setup_connections(self):
        """Wire up any extra signal connections."""
        pass  # Extend as needed

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
            # NullableSpinBox
            if isinstance(widget, NullableSpinBox):
                current = widget.value()
            else:
                current = AlbumUIComponents.get_field_value(widget, field_config.type)
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
    # Cover art — loading helpers
    # =========================================================================

    def _load_album_cover(self):
        """Load the front cover thumbnail into the header label."""
        cache = get_artwork_cache()
        is_explicit = bool(getattr(self.album, "art_is_explicit", False))
        px = cache.get_pixmap(self.album, "front", is_explicit) if cache else None
        if px and not px.isNull():
            self.cover_label.setPixmap(
                px.scaled(200, 200, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            )
            return
        self.cover_label.setText("No Cover\nImage")

    def _load_artwork_previews(self):
        """Populate all three artwork displays from the current album object."""
        cache = get_artwork_cache()
        is_explicit = bool(getattr(self.album, "art_is_explicit", False))
        for cover_type in ("front", "rear", "liner"):
            display = getattr(self, f"{cover_type}_cover_display", None)
            path_label = getattr(self, f"{cover_type}_path_label", None)
            if display is None:
                continue
            px = cache.get_pixmap(self.album, cover_type, is_explicit) if cache else None
            if px and not px.isNull():
                display.setPixmap(
                    px.scaled(250, 250, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                )
                if path_label:
                    dims = cache.get_dimensions(self.album, cover_type) if cache else None
                    info_parts = ["Embedded in track file(s)"]
                    if dims:
                        info_parts.append(f"{dims[0]} × {dims[1]} px")
                    path_label.setText("  |  ".join(info_parts))
                continue
            display.setText(f"No {cover_type.title()} Cover")
            if path_label:
                path_label.setText("")

    def _load_image_to_label(self, source, label, size=250):
        """Generic helper: load a file path or bytes into a QLabel."""
        px = QPixmap()
        if isinstance(source, bytes):
            px.loadFromData(source)
        else:
            px.load(str(source))

        if not px.isNull():
            label.setPixmap(
                px.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            )
        else:
            label.setText("Invalid Image")

    # =========================================================================
    # Cover art — picking & saving
    # =========================================================================

    def change_front_cover(self):
        self._pick_cover("front")

    def change_rear_cover(self):
        self._pick_cover("rear")

    def _pick_cover(self, cover_type: str):
        """Open a file dialog and embed the picked image into every track."""
        try:
            last_dir = self._config.get_last_art_dir()
        except AttributeError:
            last_dir = str(Path.home())

        path, _ = QFileDialog.getOpenFileName(
            self,
            f"Select {cover_type.title()} Cover",
            last_dir,
            "Images (*.png *.jpg *.jpeg *.webp *.bmp *.gif)",
        )
        if not path:
            return

        try:
            self._config.set_last_art_dir(str(Path(path).parent))
            self._config.save()
        except AttributeError:
            pass

        try:
            image_bytes = Path(path).read_bytes()

            failed = self._embed_cover_to_tracks(cover_type, image_bytes)
            self._warn_if_embed_failures(cover_type, failed)

            cache = get_artwork_cache()
            if cache:
                cache.store(self.album, cover_type, image_bytes)

            # Update the Artwork tab preview
            display = getattr(self, f"{cover_type}_cover_display", None)
            path_label = getattr(self, f"{cover_type}_path_label", None)
            if display:
                self._load_image_to_label(image_bytes, display, 250)
            if path_label:
                dims = cache.get_dimensions(self.album, cover_type) if cache else None
                info_parts = ["Embedded in track file(s)"]
                if dims:
                    info_parts.append(f"{dims[0]} × {dims[1]} px")
                path_label.setText("  |  ".join(info_parts))

            # IMPORTANT: always refresh the header thumbnail when front cover changes
            if cover_type == "front":
                self._load_album_cover()

        except Exception as e:
            logger.error(f"Error saving {cover_type} cover: {e}")
            QMessageBox.critical(self, "Error", f"Could not save cover art:\n{e}")

    _EMBEDDABLE_EXTENSIONS = ArtworkExtractor.SUPPORTED_EXTENSIONS

    def _embed_cover_to_tracks(self, cover_type: str, image_bytes):
        """Embed (image_bytes given) or strip (image_bytes=None) the given
        cover role into every FLAC/MP3 track of this album. Returns the
        list of track file paths that failed, so callers can surface one
        warning."""
        failed = []
        for track in getattr(self.album, "tracks", None) or []:
            file_path = getattr(track, "track_file_path", None)
            if not file_path or Path(file_path).suffix.lower() not in self._EMBEDDABLE_EXTENSIONS:
                continue
            try:
                success = self._metadata_writer.write_artwork_to_file(
                    file_path, cover_type, image_bytes
                )
            except Exception as e:
                logger.error(f"Error embedding {cover_type} cover into {file_path}: {e}")
                success = False
            if not success:
                failed.append(file_path)
        return failed

    def _warn_if_embed_failures(self, cover_type: str, failed_paths):
        if not failed_paths:
            return
        preview = "\n".join(failed_paths[:10])
        if len(failed_paths) > 10:
            preview += f"\n… and {len(failed_paths) - 10} more"
        QMessageBox.warning(
            self,
            "Some Files Not Updated",
            f"The {cover_type} cover was saved, but could not be embedded into "
            f"{len(failed_paths)} track file(s):\n\n{preview}",
        )

    def _clear_cover(self, cover_type: str):
        failed = self._embed_cover_to_tracks(cover_type, None)
        self._warn_if_embed_failures(cover_type, failed)

        cache = get_artwork_cache()
        if cache:
            cache.store(self.album, cover_type, None)

        display = getattr(self, f"{cover_type}_cover_display", None)
        path_label = getattr(self, f"{cover_type}_path_label", None)
        if display:
            display.clear()
            display.setText(f"No {cover_type.title()} Cover")
        if path_label:
            path_label.setText("")

        if cover_type == "front":
            self.cover_label.setText("No Cover\nImage")

    # =========================================================================
    # Wikipedia search
    # =========================================================================

    def _search_wikipedia(self):
        try:
            from src.wikipedia_seach import download_wikipedia_image, search_wikipedia
        except ImportError as e:
            QMessageBox.critical(
                self, "Import Error", f"Wikipedia module not found: {e}"
            )
            return

        query = self.album.album_name or ""
        title, summary, _full, link, images = search_wikipedia(query, self)

        if not title:
            return

        try:
            from src.album_wikipedia import AlbumWikipediaImportDialog
        except ImportError as e:
            QMessageBox.critical(self, "Import Error", f"Import dialog not found: {e}")
            return

        dlg = AlbumWikipediaImportDialog(title, summary, link, images, parent=self)
        if dlg.exec() != QDialog.Accepted:
            return

        selected = dlg.get_selected_imports()

        if selected.get("description"):
            self._set_desc_widget(selected["description"])

        if selected.get("link"):
            w = self.field_widgets.get("album_wikipedia_link")
            if w is not None and hasattr(w, "setText"):
                w.setText(selected["link"])
            # Update the album object so _rebuild_link_buttons picks up the new URL
            self.album.album_wikipedia_link = selected["link"]
            self._rebuild_link_buttons()

        role_to_cover = {
            "Front Cover": "front",
            "Rear Cover": "rear",
            "Liner Art": "liner",
        }
        for img_info in selected.get("images", []):
            url = img_info["url"]
            role = img_info["role"]
            cover_type = role_to_cover.get(role)
            if not cover_type:
                continue
            self._save_wikipedia_image(url, cover_type, download_wikipedia_image)

    def _set_desc_widget(self, text: str):
        if self.desc_widget is None:
            return
        if hasattr(self.desc_widget, "setPlainText"):
            self.desc_widget.setPlainText(text)
        elif hasattr(self.desc_widget, "setText"):
            self.desc_widget.setText(text)

    def _save_wikipedia_image(self, url: str, cover_type: str, download_fn):
        """Download url and save it as the given cover type."""
        image_bytes = download_fn(url)
        if not image_bytes:
            QMessageBox.warning(
                self,
                "Download Failed",
                f"Could not download image for {cover_type} cover:\n{url}",
            )
            return

        failed = self._embed_cover_to_tracks(cover_type, image_bytes)
        self._warn_if_embed_failures(cover_type, failed)

        cache = get_artwork_cache()
        if cache:
            cache.store(self.album, cover_type, image_bytes)

        display = getattr(self, f"{cover_type}_cover_display", None)
        path_label = getattr(self, f"{cover_type}_path_label", None)
        if display:
            self._load_image_to_label(image_bytes, display, 250)
        if path_label:
            dims = cache.get_dimensions(self.album, cover_type) if cache else None
            info_parts = ["Embedded in track file(s)"]
            if dims:
                info_parts.append(f"{dims[0]} × {dims[1]} px")
            path_label.setText("  |  ".join(info_parts))

        if cover_type == "front":
            self._load_album_cover()

    # =========================================================================
    # MusicBrainz lookup
    # =========================================================================

    def _lookup_musicbrainz(self):
        title_widget = self.field_widgets.get("album_name")
        album_name = (
            title_widget.text().strip()
            if isinstance(title_widget, QLineEdit)
            else (self.album.album_name or "")
        ).strip()
        if not album_name:
            QMessageBox.warning(
                self, "MusicBrainz Lookup", "Enter an album title before looking it up."
            )
            return

        artist_names = getattr(self.album, "album_artist_names", None)
        if artist_names in (None, "Unknown Artist"):
            artist_names = None

        dialog = MusicBrainzMatchDialog(
            entity_label=f"album '{album_name}'",
            search_call=lambda: search_release_groups(album_name, artist_names),
            parent=self,
        )
        if dialog.exec() != QDialog.Accepted:
            return

        enrichment = dialog.result_enrichment()
        if enrichment:
            self._apply_musicbrainz_enrichment(enrichment)

    def _apply_musicbrainz_enrichment(self, enrichment: dict):
        """Fill field widgets from a MusicBrainz enrichment dict, but only
        where the widget is still at its blank/default state -- never
        overwrites something the user already filled in or typed moments ago.

        QSpinBox fields with no nullable wrapper (release_year/month/day)
        default to their minimum when unset, per init_editable_widgets()'s
        own convention -- so "still at minimum" is this codebase's existing
        signal for "blank" on those fields.

        QCheckBox fields (is_live/is_compilation) have no blank state at
        all, so they fall back to the originally-loaded album's value being
        None, combined with the widget still being unchecked -- applied
        only when both hold, so a deliberate manual uncheck just before the
        lookup is never clobbered.
        """
        for field_name, value in enrichment.items():
            widget = self.field_widgets.get(field_name)
            if widget is None:
                continue
            if isinstance(widget, QLineEdit):
                if not widget.text().strip():
                    widget.setText(str(value))
            elif isinstance(widget, QSpinBox):
                if widget.value() == widget.minimum():
                    widget.setValue(int(value))
            elif isinstance(widget, QCheckBox):
                if (
                    getattr(self.album, field_name, None) is None
                    and not widget.isChecked()
                ):
                    widget.setChecked(bool(value))

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

        except Exception as e:
            logger.error(f"Error saving album changes: {e}")
            QMessageBox.critical(self, "Error", f"Failed to save changes: {e}")

    def refresh_view(self):
        """Called by RelationshipHelpers after any relationship change.

        Always rebuilds the Publishers & Places tab so adding/removing a
        publisher is immediately visible without switching tabs.
        Also rebuilds the currently active tab if it is a different tab.
        """
        try:
            updated = self.controller.get.get_entity_object(
                "Album", album_id=self.album.album_id
            )
            if updated:
                self.album = updated
                self.helper.album = updated
                self.tab_builder.album = updated

            # Always refresh Publishers & Places
            self._rebuild_tab_by_title("Publishers && Places")

            # Only rebuild the current tab if it is a different tab — avoids a
            # double-rebuild (and the flash of the tab switching) when the user
            # is already on the Publishers & Places tab.
            current_title = self.tabs.tabText(self.tabs.currentIndex())
            if current_title != "Publishers && Places":
                self._rebuild_current_tab()

            logger.info("Album view refreshed")
        except Exception as e:
            logger.error(f"Error refreshing album view: {e}")

    def _refresh_from_database(self):
        """Reload all editable widgets from the latest DB state."""
        try:
            updated = self.controller.get.get_entity_object(
                "Album", album_id=self.album.album_id
            )
            if updated:
                self.album = updated
                self.helper.album = updated
                self.tab_builder.album = updated
            self.init_editable_widgets()
            self._rebuild_current_tab()
        except Exception as e:
            logger.error(f"Error refreshing from database: {e}")
            QMessageBox.critical(self, "Error", f"Could not refresh: {e}")

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
            "Artist Credits": self.tab_builder.build_artists_tab,
            "Publishers && Places": self.tab_builder.build_relationships_tab,
            "Awards": self.tab_builder.build_awards_tab,
            "Advanced": lambda: AdvancedTab(self).build(),
        }

    def _rebuild_current_tab(self):
        """Replace the currently visible tab with a freshly built version."""
        try:
            idx = self.tabs.currentIndex()
            tab_title = self.tabs.tabText(idx)
            builder = self._get_tab_rebuild_map().get(tab_title)
            if builder:
                new_tab = builder()
                self.tabs.removeTab(idx)
                self.tabs.insertTab(idx, new_tab, tab_title)
                self.tabs.setCurrentIndex(idx)
        except Exception as e:
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
                    was_current = self.tabs.currentIndex() == idx
                    new_tab = builder()
                    self.tabs.removeTab(idx)
                    self.tabs.insertTab(idx, new_tab, title)
                    # Don't change the active tab — just silently update it.
                    # removeTab() auto-advances the selection if the removed
                    # tab was the current one, so restore it explicitly.
                    if was_current:
                        self.tabs.setCurrentIndex(idx)
                    return
        except Exception as e:
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
                self.album = updated
                self.helper.album = updated
                self.tab_builder.album = updated
            self._rebuild_current_tab()
        except Exception as e:
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
        except Exception as e:
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
