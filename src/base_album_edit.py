"""
base_album_edit.py

Unified Album Editor dialog.

Changes from the previous version
──────────────────────────────────
BUG FIXES
  • release_year spinbox no longer defaults to 99 — fix is in album_components.py,
    but we also explicitly pull the value from the album object here.
  • release_month and release_day are now shown in the header date row.
  • AutocompleteDialog.get_inputs called with correct signature (title= keyword).
  • update_entity used instead of the non-existent update_album method.
  • DiscManagementView no longer receives the unsupported editable=True kwarg.

NEW FEATURES
  • Description moved to the header as a tall QTextEdit.
  • album_language, album_subtitle, release_type, catalog_number, is_fixed,
    is_live, is_compilation, estimated_sales, MBID all wired up in the Details tab.
  • Album aliases tab added (add / remove, with alias_type).
  • Wikipedia search button: populates description, link, and lets you pick and save
    front cover, rear cover, or liner art from the Wikipedia images.
  • Wikipedia and MusicBrainz "open page" buttons shown in header when links exist.
  • Cover art picker remembers the last used directory (stored in Config).
  • Picked cover art is copied to ALBUM_ART_DIR/artist/album/ and the path is saved.
  • liner art picker and display added to the Artwork tab.
  • Dialog minimum size bumped; uses more horizontal space.
"""

import shutil
import webbrowser
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from src.album_components import AlbumUIComponents
from src.album_editing_relationship_helpers import RelationshipHelpers
from src.album_tab import AlbumTabBuilder
from src.asset_paths import ALBUM_ART_DIR
from src.config_setup import Config
from src.db_mapping_albums import ALBUM_FIELDS
from src.disc_view import DiscManagementView
from src.logger_config import logger

# Alias types that match the ArtistAlias convention (adapted for albums)
ALBUM_ALIAS_TYPES = [
    "",
    "Also Known As",
    "Localized Title",
    "Romanized Title",
    "Phonetic Title",
    "Working Title",
    "Subtitle Variant",
    "Other",
]


def _sanitize_filename(name: str) -> str:
    """Strip characters that are illegal in file/folder names."""
    if not name:
        return "Unknown"
    for ch in '<>:"/\\|?*':
        name = name.replace(ch, "_")
    name = name.strip(" .")
    return name[:100]


class AlbumEditor(QDialog):
    """
    Comprehensive album editor dialog with tabbed interface.

    Tabs
    ────
    Details      – core metadata fields (language, subtitle, type, catalog #, flags, sales, MBID)
    Tracks       – DiscManagementView for disc / track structure
    Artwork      – front cover, rear cover, liner art with pickers
    Aliases      – add / remove / type album aliases
    Artist Credits – relationship helpers
    Publishers & Places – relationship helpers
    Awards       – relationship helpers
    Advanced     – ReplayGain, Wikipedia link, status
    """

    def __init__(self, controller, album, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.album = album
        self._config = Config()

        # RelationshipHelpers and AlbumTabBuilder are passed self so they can
        # reach self.album, self.controller, self.helper, self.field_widgets.
        self.helper = RelationshipHelpers(controller, album, self.refresh_view)
        self.field_widgets = {}
        self.tab_builder = AlbumTabBuilder(self)

        self.setWindowTitle(f"Edit Album: {album.album_name}")
        # Larger default — more horizontal room so fields don't feel cramped
        self.setMinimumSize(1150, 750)
        self.resize(1250, 820)

        self.init_editable_widgets()
        self.init_ui()
        self.setup_connections()

    # =========================================================================
    # Widget initialisation
    # =========================================================================

    def init_editable_widgets(self):
        """Create one widget per editable ALBUM_FIELD, pre-filled from the album."""
        for field_name, field_config in ALBUM_FIELDS.items():
            if not field_config.editable:
                continue
            current_value = getattr(self.album, field_name, None)
            self.field_widgets[field_name] = AlbumUIComponents.create_editable_field(
                field_config, current_value
            )

    # =========================================================================
    # Main UI layout
    # =========================================================================

    def init_ui(self):
        """Build the full dialog layout."""
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(15, 15, 15, 15)
        main_layout.setSpacing(12)

        # Scrollable content area
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QScrollArea.NoFrame)

        content_widget = QWidget()
        content_layout = QVBoxLayout(content_widget)
        content_layout.setSpacing(16)

        # ── Header (cover + editable info) ───────────────────────────────────
        content_layout.addWidget(self._build_header_section())

        # ── Tab widget ───────────────────────────────────────────────────────
        tabs = QTabWidget()
        tabs.addTab(self._build_details_tab(), "Details")
        tabs.addTab(self._build_tracks_tab(), "Tracks")
        tabs.addTab(self._build_artwork_tab(), "Artwork")
        tabs.addTab(self._build_aliases_tab(), "Aliases")
        tabs.addTab(self.tab_builder.build_artists_tab(), "Artist Credits")
        tabs.addTab(self.tab_builder.build_relationships_tab(), "Publishers & Places")
        tabs.addTab(self.tab_builder.build_awards_tab(), "Awards")
        tabs.addTab(self._build_advanced_tab(), "Advanced")

        content_layout.addWidget(tabs)
        content_layout.addStretch()

        scroll_area.setWidget(content_widget)
        main_layout.addWidget(scroll_area)

        # ── Dialog buttons ───────────────────────────────────────────────────
        self._add_dialog_buttons(main_layout)

    # =========================================================================
    # Header section
    # =========================================================================

    def _build_header_section(self):
        """Cover image on the left; editable album info on the right."""
        header = QWidget()
        row = QHBoxLayout(header)
        row.setSpacing(20)

        row.addWidget(self._build_cover_section())
        row.addWidget(self._build_info_section(), 1)

        return header

    def _build_cover_section(self):
        """Cover thumbnail + Change Cover button."""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setAlignment(Qt.AlignTop)

        self.cover_label = QLabel()
        self.cover_label.setAlignment(Qt.AlignCenter)
        self.cover_label.setFixedSize(200, 200)
        self.cover_label.setStyleSheet("border: 1px solid #555; background: #2a2a2a;")
        self._load_album_cover()
        layout.addWidget(self.cover_label)

        change_btn = QPushButton("Change Cover")
        change_btn.clicked.connect(self.change_front_cover)
        layout.addWidget(change_btn)

        return widget

    def _build_info_section(self):
        """Right-hand side of the header: title, subtitle, artists, date, description, external links."""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setSpacing(8)

        # Album title (large, editable)
        title_widget = self.field_widgets.get("album_name")
        if title_widget:
            title_widget.setStyleSheet("font-size: 18px; font-weight: bold;")
            layout.addWidget(title_widget)

        # Subtitle (smaller)
        subtitle_widget = self.field_widgets.get("album_subtitle")
        if subtitle_widget:
            subtitle_widget.setPlaceholderText("Subtitle (optional)")
            layout.addWidget(subtitle_widget)

        # Artist names (read-only display)
        if hasattr(self.album, "album_artists") and self.album.album_artists:
            names = ", ".join(a.artist_name for a in self.album.album_artists[:4])
            if len(self.album.album_artists) > 4:
                names += "…"
            artist_label = QLabel(f"by {names}")
            artist_label.setStyleSheet("color: #aaa; font-size: 13px;")
            layout.addWidget(artist_label)

        # Release date row  (year / month / day side by side)
        date_row = QHBoxLayout()
        date_row.setSpacing(6)
        date_row.addWidget(QLabel("Released:"))
        for field in ("release_year", "release_month", "release_day"):
            w = self.field_widgets.get(field)
            if w:
                w.setFixedWidth(70)
                date_row.addWidget(w)
                date_row.addWidget(
                    QLabel(
                        {"release_year": "Y", "release_month": "M", "release_day": "D"}[
                            field
                        ]
                    )
                )
        date_row.addStretch()
        layout.addLayout(date_row)

        # Description — full-width, tall QTextEdit
        desc_label = QLabel("Description:")
        desc_label.setStyleSheet("font-weight: bold;")
        layout.addWidget(desc_label)

        self.desc_widget = self.field_widgets.get("album_description")
        if self.desc_widget is None:
            # Fallback: create one if ALBUM_FIELDS doesn't mark it editable
            self.desc_widget = QTextEdit()
            self.desc_widget.setPlainText(self.album.album_description or "")
        self.desc_widget.setMinimumHeight(80)
        self.desc_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        layout.addWidget(self.desc_widget)

        # External link buttons (Wikipedia / MusicBrainz) — only shown when set
        links_row = QHBoxLayout()
        links_row.setSpacing(8)

        wiki_link = getattr(self.album, "album_wikipedia_link", None)
        if wiki_link:
            wiki_btn = QPushButton("🌐 Wikipedia")
            wiki_btn.setToolTip(wiki_link)
            wiki_btn.clicked.connect(lambda: webbrowser.open(wiki_link))
            links_row.addWidget(wiki_btn)

        mbid = getattr(self.album, "MBID", None)
        if mbid:
            mb_url = f"https://musicbrainz.org/release/{mbid}"
            mb_btn = QPushButton("🎵 MusicBrainz")
            mb_btn.setToolTip(mb_url)
            mb_btn.clicked.connect(lambda: webbrowser.open(mb_url))
            links_row.addWidget(mb_btn)

        # Wikipedia search button — always visible
        wiki_search_btn = QPushButton("🔍 Search Wikipedia…")
        wiki_search_btn.clicked.connect(self._search_wikipedia)
        links_row.addWidget(wiki_search_btn)

        links_row.addStretch()
        layout.addLayout(links_row)

        return widget

    # =========================================================================
    # Details tab  (metadata fields not shown in header)
    # =========================================================================

    def _build_details_tab(self):
        """Core metadata: language, type, catalog #, flags, sales, MBID."""
        tab = QWidget()
        # Two-column layout for better horizontal space usage
        outer = QHBoxLayout(tab)
        outer.setSpacing(24)
        outer.setContentsMargins(12, 12, 12, 12)

        # ── Left column ──────────────────────────────────────────────────────
        left = QVBoxLayout()
        left.setSpacing(10)

        def _row(label_text, field_name):
            row = QHBoxLayout()
            lbl = QLabel(label_text)
            lbl.setFixedWidth(130)
            row.addWidget(lbl)
            w = self.field_widgets.get(field_name)
            if w:
                row.addWidget(w, 1)
            left.addLayout(row)

        _row("Language:", "album_language")
        _row("Release Type:", "release_type")
        _row("Catalog Number:", "catalog_number")
        _row("MBID:", "MBID")
        _row("Status:", "status")
        _row("Est. Sales:", "estimated_sales")
        left.addStretch()

        # ── Right column (checkboxes + gain) ─────────────────────────────────
        right = QVBoxLayout()
        right.setSpacing(10)

        for field_name in ("is_fixed", "is_live", "is_compilation"):
            w = self.field_widgets.get(field_name)
            if w:
                right.addWidget(w)

        right.addSpacing(12)
        right.addWidget(QLabel("ReplayGain"))

        for label, field_name in (
            ("Album Gain (dB):", "album_gain"),
            ("Album Peak:", "album_peak"),
        ):
            row = QHBoxLayout()
            lbl = QLabel(label)
            lbl.setFixedWidth(120)
            row.addWidget(lbl)
            w = self.field_widgets.get(field_name)
            if w:
                row.addWidget(w, 1)
            right.addLayout(row)

        right.addStretch()

        outer.addLayout(left, 1)
        outer.addLayout(right, 1)
        return tab

    # =========================================================================
    # Tracks tab
    # =========================================================================

    def _build_tracks_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(10, 10, 10, 10)

        try:
            # NOTE: DiscManagementView does NOT accept an `editable` keyword.
            # Passing it would raise a TypeError — removed here.
            self.disc_view = DiscManagementView(self.album, self.controller)
            layout.addWidget(self.disc_view)
        except Exception as e:
            logger.error(f"Error creating DiscManagementView: {e}")
            err = QLabel("Error loading track view — see logs for details.")
            err.setStyleSheet("color: red;")
            layout.addWidget(err)

        return tab

    # =========================================================================
    # Artwork tab
    # =========================================================================

    def _build_artwork_tab(self):
        """Front cover, rear cover, and liner art — each with a pick + clear button."""
        tab = QWidget()
        layout = QHBoxLayout(tab)  # Three panels side by side
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(16)

        for cover_type, attr, label in (
            ("front", "front_cover_path", "Front Cover"),
            ("rear", "rear_cover_path", "Rear Cover"),
            ("liner", "album_liner_path", "Liner Art"),
        ):
            group = QGroupBox(label)
            g_layout = QVBoxLayout(group)

            display = QLabel()
            display.setAlignment(Qt.AlignCenter)
            display.setFixedSize(250, 250)
            display.setStyleSheet("border: 1px solid #555; background: #2a2a2a;")
            display.setWordWrap(True)
            setattr(self, f"{cover_type}_cover_display", display)
            g_layout.addWidget(display)

            btn_row = QHBoxLayout()
            pick_btn = QPushButton("Choose…")
            pick_btn.clicked.connect(
                lambda checked=False, ct=cover_type: self._pick_cover(ct)
            )
            clear_btn = QPushButton("Clear")
            clear_btn.clicked.connect(
                lambda checked=False, ct=cover_type: self._clear_cover(ct)
            )
            btn_row.addWidget(pick_btn)
            btn_row.addWidget(clear_btn)
            g_layout.addLayout(btn_row)

            # Path display (read-only)
            path_label = QLabel()
            path_label.setWordWrap(True)
            path_label.setStyleSheet("color: #888; font-size: 10px;")
            setattr(self, f"{cover_type}_path_label", path_label)
            g_layout.addWidget(path_label)

            layout.addWidget(group)

        self._load_artwork_previews()
        return tab

    # =========================================================================
    # Aliases tab
    # =========================================================================

    def _build_aliases_tab(self):
        """List existing aliases; allow adding and removing them."""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        info = QLabel(
            "Aliases are alternative titles for this album "
            "(e.g. localized names, working titles)."
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        # Alias list container — rebuilt on refresh
        self.aliases_container = QWidget()
        self.aliases_layout = QVBoxLayout(self.aliases_container)
        self.aliases_layout.setSpacing(4)
        layout.addWidget(self.aliases_container)

        self._refresh_aliases_list()

        # Add new alias row
        add_group = QGroupBox("Add New Alias")
        add_row = QHBoxLayout(add_group)

        self.new_alias_name = QLineEdit()
        self.new_alias_name.setPlaceholderText("Alias name…")
        add_row.addWidget(self.new_alias_name, 2)

        self.new_alias_type = QComboBox()
        self.new_alias_type.addItems(ALBUM_ALIAS_TYPES)
        add_row.addWidget(self.new_alias_type, 1)

        add_btn = QPushButton("Add Alias")
        add_btn.clicked.connect(self._add_alias)
        add_row.addWidget(add_btn)

        layout.addWidget(add_group)
        layout.addStretch()
        return tab

    def _refresh_aliases_list(self):
        """Rebuild the alias list widget from the database."""
        # Clear old rows
        while self.aliases_layout.count():
            item = self.aliases_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        aliases = getattr(self.album, "album_aliases", []) or []
        if not aliases:
            self.aliases_layout.addWidget(QLabel("No aliases yet."))
            return

        for alias in aliases:
            row = QHBoxLayout()
            name_lbl = QLabel(f"<b>{alias.alias_name}</b>")
            type_lbl = QLabel(alias.alias_type or "—")
            type_lbl.setStyleSheet("color: #aaa;")
            remove_btn = QPushButton("✕ Remove")
            remove_btn.setFixedWidth(90)
            remove_btn.setStyleSheet("color: #cc4444;")
            remove_btn.clicked.connect(
                lambda checked=False, a=alias: self._remove_alias(a)
            )
            row.addWidget(name_lbl, 2)
            row.addWidget(type_lbl, 1)
            row.addWidget(remove_btn)

            row_widget = QWidget()
            row_widget.setLayout(row)
            self.aliases_layout.addWidget(row_widget)

    def _add_alias(self):
        alias_name = self.new_alias_name.text().strip()
        if not alias_name:
            QMessageBox.warning(self, "Missing Name", "Please enter an alias name.")
            return
        alias_type = self.new_alias_type.currentText() or None
        try:
            self.controller.add.add_entity(
                "AlbumAlias",
                album_id=self.album.album_id,
                alias_name=alias_name,
                alias_type=alias_type,
            )
            self.new_alias_name.clear()
            self.new_alias_type.setCurrentIndex(0)
            # Reload the album object so aliases list is fresh
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
            f"Remove alias '{alias.alias_name}'?",
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
    # Advanced tab
    # =========================================================================

    def _build_advanced_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        def _row(label_text, field_name):
            row = QHBoxLayout()
            lbl = QLabel(label_text)
            lbl.setFixedWidth(140)
            row.addWidget(lbl)
            w = self.field_widgets.get(field_name)
            if w:
                row.addWidget(w, 1)
            layout.addLayout(row)

        _row("Wikipedia Link:", "album_wikipedia_link")

        # Track count (read-only)
        track_count = len(self.album.tracks) if self.album.tracks else 0
        row = QHBoxLayout()
        row.addWidget(QLabel("Track Count:"))
        row.addWidget(QLabel(str(track_count)))
        row.addStretch()
        layout.addLayout(row)

        # RIAA cert (read-only derived)
        cert = getattr(self.album, "RIAA_certification", None)
        if cert:
            row = QHBoxLayout()
            row.addWidget(QLabel("RIAA Certification:"))
            row.addWidget(QLabel(cert))
            row.addStretch()
            layout.addLayout(row)

        layout.addStretch()
        return tab

    # =========================================================================
    # Dialog buttons
    # =========================================================================

    def _add_dialog_buttons(self, layout):
        button_box = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        button_box.accepted.connect(self.save_changes)
        button_box.rejected.connect(self.reject)

        refresh_btn = button_box.addButton(
            "Refresh from DB", QDialogButtonBox.ActionRole
        )
        refresh_btn.clicked.connect(self._refresh_from_database)

        layout.addWidget(button_box)

    def setup_connections(self):
        """Wire up any extra signal connections."""
        pass  # Extend as needed

    # =========================================================================
    # Cover art  — loading
    # =========================================================================

    def _load_album_cover(self):
        """Load the front cover thumbnail into the header label."""
        path = getattr(self.album, "front_cover_path", None)
        if path:
            px = QPixmap()
            loaded = px.loadFromData(path) if isinstance(path, bytes) else px.load(path)
            if loaded and not px.isNull():
                self.cover_label.setPixmap(
                    px.scaled(200, 200, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                )
                return
        self.cover_label.setText("No Cover\nImage")
        self.cover_label.setStyleSheet(
            "border: 1px solid #555; background: #2a2a2a; color: #666;"
        )

    def _load_artwork_previews(self):
        """Populate all three artwork displays from the current album object."""
        for cover_type, attr in (
            ("front", "front_cover_path"),
            ("rear", "rear_cover_path"),
            ("liner", "album_liner_path"),
        ):
            display = getattr(self, f"{cover_type}_cover_display")
            path_label = getattr(self, f"{cover_type}_path_label")
            path = getattr(self.album, attr, None)

            if path:
                px = QPixmap()
                loaded = (
                    px.loadFromData(path) if isinstance(path, bytes) else px.load(path)
                )
                if loaded and not px.isNull():
                    display.setPixmap(
                        px.scaled(250, 250, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                    )
                    path_label.setText(
                        str(path) if isinstance(path, str) else "(binary)"
                    )
                    continue
            display.setText(f"No {cover_type.title()} Cover")
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
    # Cover art  — picking & saving
    # =========================================================================

    def change_front_cover(self):
        self._pick_cover("front")

    def change_rear_cover(self):
        self._pick_cover("rear")

    def _pick_cover(self, cover_type: str):
        """Open a file dialog, copy the image to ALBUM_ART_DIR, update the album."""
        # Remember the last directory the user navigated to
        last_dir = self._config.config.get(
            "ui", "last_cover_dir", fallback=str(Path.home())
        )

        file_path, _ = QFileDialog.getOpenFileName(
            self,
            f"Select {cover_type.title()} Cover",
            last_dir,
            "Image Files (*.png *.jpg *.jpeg *.bmp *.gif *.webp);;All Files (*)",
        )
        if not file_path:
            return

        # Save the directory for next time
        try:
            if "ui" not in self._config.config:
                self._config.config["ui"] = {}
            self._config.config["ui"]["last_cover_dir"] = str(Path(file_path).parent)
            self._config.save()
        except Exception as e:
            logger.warning(f"Could not save last cover dir: {e}")

        try:
            dest_path = self._save_cover_file(file_path, cover_type)
        except Exception as e:
            logger.error(f"Failed to save cover image: {e}")
            QMessageBox.warning(self, "Error", f"Could not save image: {e}")
            return

        # Update the album object and the database
        attr_map = {
            "front": "front_cover_path",
            "rear": "rear_cover_path",
            "liner": "album_liner_path",
        }
        attr = attr_map[cover_type]
        setattr(self.album, attr, str(dest_path))

        try:
            self.controller.update.update_entity(
                "Album", self.album.album_id, **{attr: str(dest_path)}
            )
        except Exception as e:
            logger.error(f"DB update failed for cover: {e}")

        # Refresh displays
        display = getattr(self, f"{cover_type}_cover_display")
        path_label = getattr(self, f"{cover_type}_path_label")
        self._load_image_to_label(str(dest_path), display, 250)
        path_label.setText(str(dest_path))

        if cover_type == "front":
            self._load_album_cover()

    def _save_cover_file(self, source_path: str, cover_type: str) -> Path:
        """
        Copy *source_path* into ALBUM_ART_DIR/<artist>/<album>/<cover_type>.<ext>
        and return the destination Path.

        This mirrors the logic in library_import_album.py so covers end up in a
        predictable, consistent location.
        """
        source = Path(source_path)
        ext = source.suffix.lower() or ".jpg"

        # Determine artist name for the folder structure
        artist_name = "Unknown Artist"
        if hasattr(self.album, "album_artists") and self.album.album_artists:
            artist_name = self.album.album_artists[0].artist_name

        safe_artist = _sanitize_filename(artist_name)
        safe_album = _sanitize_filename(self.album.album_name or "Unknown Album")

        art_dir = ALBUM_ART_DIR / safe_artist / safe_album
        art_dir.mkdir(parents=True, exist_ok=True)

        filename_map = {
            "front": f"frontcover{ext}",
            "rear": f"rearcover{ext}",
            "liner": f"liner{ext}",
        }
        dest = art_dir / filename_map.get(cover_type, f"{cover_type}{ext}")

        shutil.copy2(source_path, dest)
        logger.info(f"Copied cover art: {source_path} → {dest}")
        return dest

    def _clear_cover(self, cover_type: str):
        attr_map = {
            "front": "front_cover_path",
            "rear": "rear_cover_path",
            "liner": "album_liner_path",
        }
        attr = attr_map[cover_type]
        setattr(self.album, attr, None)

        try:
            self.controller.update.update_entity(
                "Album", self.album.album_id, **{attr: None}
            )
        except Exception as e:
            logger.error(f"DB clear failed for cover: {e}")

        display = getattr(self, f"{cover_type}_cover_display")
        path_label = getattr(self, f"{cover_type}_path_label")
        display.clear()
        display.setText(f"No {cover_type.title()} Cover")
        path_label.setText("")

        if cover_type == "front":
            self.cover_label.setText("No Cover\nImage")

    # =========================================================================
    # Wikipedia search
    # =========================================================================

    def _search_wikipedia(self):
        """
        1. Open the shared Wikipedia search dialog to find the article.
        2. Open AlbumWikipediaImportDialog — shows description, link, AND all
           images at once so the user can approve/reject everything in one pass.
        3. Apply whatever the user selected.

        BUG FIX: previous code called self.desc_widget.setPlainText() but
        desc_widget can be a QLineEdit (which has no setPlainText).  Now we
        always use a helper that handles both widget types.
        """
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
            return  # User cancelled or closed the search dialog

        # ── Single comprehensive import dialog ───────────────────────────────
        try:
            from src.album_wikipedia import AlbumWikipediaImportDialog
        except ImportError as e:
            QMessageBox.critical(self, "Import Error", f"Import dialog not found: {e}")
            return

        dlg = AlbumWikipediaImportDialog(title, summary, link, images, parent=self)
        if dlg.exec() != QDialog.Accepted:
            return

        selected = dlg.get_selected_imports()

        # ── Apply description ────────────────────────────────────────────────
        if selected.get("description"):
            self._set_desc_widget(selected["description"])

        # ── Apply Wikipedia link ─────────────────────────────────────────────
        if selected.get("link"):
            w = self.field_widgets.get("album_wikipedia_link")
            if w and hasattr(w, "setText"):
                w.setText(selected["link"])

        # ── Download and save each assigned image ────────────────────────────
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
        """Set description text regardless of whether desc_widget is QTextEdit or QLineEdit."""
        if self.desc_widget is None:
            return
        if hasattr(self.desc_widget, "setPlainText"):
            self.desc_widget.setPlainText(text)
        elif hasattr(self.desc_widget, "setText"):
            self.desc_widget.setText(text)

    def _save_wikipedia_image(self, url: str, cover_type: str, download_fn):
        """Download *url* and save it as the given cover type."""
        image_bytes = download_fn(url)
        if not image_bytes:
            QMessageBox.warning(
                self,
                "Download Failed",
                f"Could not download image for {cover_type} cover:\n{url}",
            )
            return

        url_path = url.split("?")[0]
        ext = Path(url_path).suffix.lower()
        if ext not in (".jpg", ".jpeg", ".png", ".gif", ".webp"):
            ext = ".jpg"

        safe_artist = _sanitize_filename(
            self.album.album_artists[0].artist_name
            if getattr(self.album, "album_artists", None)
            else "Unknown Artist"
        )
        safe_album = _sanitize_filename(self.album.album_name or "Unknown Album")
        art_dir = ALBUM_ART_DIR / safe_artist / safe_album
        art_dir.mkdir(parents=True, exist_ok=True)

        filename_map = {
            "front": f"frontcover{ext}",
            "rear": f"rearcover{ext}",
            "liner": f"liner{ext}",
        }
        dest = art_dir / filename_map.get(cover_type, f"{cover_type}{ext}")

        with open(dest, "wb") as f:
            f.write(image_bytes)
        logger.info(f"Saved Wikipedia cover ({cover_type}): {dest}")

        attr_map = {
            "front": "front_cover_path",
            "rear": "rear_cover_path",
            "liner": "album_liner_path",
        }
        attr = attr_map[cover_type]
        setattr(self.album, attr, str(dest))

        try:
            self.controller.update.update_entity(
                "Album", self.album.album_id, **{attr: str(dest)}
            )
        except Exception as e:
            logger.error(f"DB update for Wikipedia cover failed: {e}")

        display = getattr(self, f"{cover_type}_cover_display", None)
        path_label = getattr(self, f"{cover_type}_path_label", None)
        if display:
            self._load_image_to_label(str(dest), display, 250)
        if path_label:
            path_label.setText(str(dest))

        if cover_type == "front":
            self._load_album_cover()

    # =========================================================================
    # Save / Refresh
    # =========================================================================

    def save_changes(self):
        """
        Collect all widget values and persist to the database.

        BUG FIX: The old code called self.controller.update.update_album(self.album)
        which does not exist.  The correct call is:
            self.controller.update.update_entity("Album", album_id, **kwargs)
        """
        try:
            kwargs = {}
            for field_name, widget in self.field_widgets.items():
                field_config = ALBUM_FIELDS.get(field_name)
                if field_config and field_config.editable:
                    value = AlbumUIComponents.get_field_value(widget, field_config.type)
                    kwargs[field_name] = value

            # Also capture the description from the header widget
            # (it may be the same widget as field_widgets["album_description"] — that's fine)
            if self.desc_widget is not None:
                if hasattr(self.desc_widget, "toPlainText"):
                    kwargs["album_description"] = (
                        self.desc_widget.toPlainText().strip() or None
                    )
                elif hasattr(self.desc_widget, "text"):
                    kwargs["album_description"] = (
                        self.desc_widget.text().strip() or None
                    )

            self.controller.update.update_entity("Album", self.album.album_id, **kwargs)

            QMessageBox.information(self, "Saved", "Album updated successfully!")
            self.accept()

        except Exception as e:
            logger.error(f"Error saving album changes: {e}")
            QMessageBox.critical(self, "Error", f"Failed to save changes: {e}")

    def refresh_view(self):
        """Called by RelationshipHelpers after any relationship change."""
        try:
            updated = self.controller.get.get_entity_object(
                "Album", album_id=self.album.album_id
            )
            if updated:
                self.album = updated
            logger.info("Album view refreshed")
        except Exception as e:
            logger.error(f"Error refreshing album view: {e}")

    def _refresh_from_database(self):
        """Reload the full dialog from the latest DB state."""
        try:
            self.album = self.controller.get.get_entity_object(
                "Album", album_id=self.album.album_id
            )
            self.init_editable_widgets()
            # Re-build the UI in place by deleting and re-adding
            # the central widget.  Simplest: close and reopen.
            QMessageBox.information(
                self,
                "Refreshed",
                "Data reloaded.  Close and re-open this dialog to see all changes.",
            )
        except Exception as e:
            logger.error(f"Error refreshing album from DB: {e}")
            QMessageBox.critical(self, "Error", f"Failed to refresh: {e}")

    # =========================================================================
    # Utility
    # =========================================================================

    @staticmethod
    def format_duration(seconds):
        if not seconds:
            return "0:00"
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = int(seconds % 60)
        return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"

    def get_album_place_associations(self):
        try:
            return (
                self.controller.get.get_all_entities(
                    "AlbumPlace", album_id=self.album.album_id
                )
                or []
            )
        except Exception as e:
            logger.error(f"Error loading album place associations: {e}")
            return []
