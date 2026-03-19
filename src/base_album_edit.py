"""
base_album_edit.py
"""

import shutil
import webbrowser
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from src.album_components import AlbumUIComponents
from src.album_editing_relationship_helpers import RelationshipHelpers
from src.album_tab import AlbumTabBuilder
from src.asset_paths import ALBUM_ART_DIR
from src.base_album_edit_tabs import (
    AliasesTab,
    ArtworkTab,
    DetailsTab,
    TracksTab,
    AdvancedTab,
)
from src.config_setup import Config
from src.db_mapping_albums import ALBUM_FIELDS
from src.logger_config import logger


def _sanitize_filename(name: str) -> str:
    """Strip characters that are illegal in file/folder names.

    Kept as a module-level helper so it can be imported by other modules
    (e.g. library_import_album.py) without instantiating AlbumEditor.
    AlbumEditor also exposes it as a @staticmethod for internal use.
    """
    if not name:
        return "Unknown"
    for ch in '<>:"/\\|?*':
        name = name.replace(ch, "_")
    name = name.strip(" .")
    return name[:100]


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
    Details          – core metadata (language, type, catalog #, flags, sales, MBID)
    Tracks           – DiscManagementView for disc / track structure
    Artwork          – front cover, rear cover, liner art with pickers
    Aliases          – add / remove / type album aliases
    Artist Credits   – relationship helpers (built by AlbumTabBuilder)
    Publishers & Places – relationship helpers (built by AlbumTabBuilder)
    Awards           – relationship helpers (built by AlbumTabBuilder)
    Advanced         – ReplayGain, Wikipedia link, library stats
    """

    def __init__(self, controller, album, parent=None):
        super().__init__(parent)

        # Float freely — not locked to the parent window's position.
        self.setWindowFlag(Qt.Window, True)

        self.controller = controller
        self.album = album
        self._config = Config()

        self.helper = RelationshipHelpers(controller, album, self.refresh_view)
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

    # =========================================================================
    # Main UI layout
    # =========================================================================

    def init_ui(self):
        """Build the full dialog layout."""
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(15, 15, 15, 15)
        main_layout.setSpacing(12)

        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QScrollArea.NoFrame)

        content_widget = QWidget()
        content_layout = QVBoxLayout(content_widget)
        content_layout.setSpacing(16)

        content_layout.addWidget(self._build_collapsible_header())

        self.tabs = QTabWidget()
        self._details_tab = DetailsTab(self)
        self._tracks_tab = TracksTab(self)
        self._artwork_tab = ArtworkTab(self)
        self._aliases_tab = AliasesTab(self)
        self._advanced_tab = AdvancedTab(self)

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

        content_layout.addWidget(self.tabs)
        content_layout.addStretch()

        scroll_area.setWidget(content_widget)
        main_layout.addWidget(scroll_area)

        self._add_dialog_buttons(main_layout)

    # =========================================================================
    # Header section  (cover thumbnail + editable info — collapsible)
    # =========================================================================

    def _build_collapsible_header(self) -> QWidget:
        """A slim toggle bar + the full header content underneath.

        The toggle bar is always visible and shows the album name so you always
        know what you are editing. Clicking the ▼/▶ arrow shows or hides the
        full header (cover image, description, links) so that tab space is not
        wasted while you are deep-editing tracks or relationships.
        """
        container = QWidget()
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.setSpacing(0)

        # ── Always-visible toggle bar ──────────────────────────────────────
        toggle_bar = QWidget()
        toggle_bar.setStyleSheet(
            "background: #1e1e2e; border-radius: 4px; padding: 2px 4px;"
        )
        bar_row = QHBoxLayout(toggle_bar)
        bar_row.setContentsMargins(8, 4, 8, 4)
        bar_row.setSpacing(10)

        self._header_toggle_btn = QPushButton("▼")
        self._header_toggle_btn.setFixedSize(24, 24)
        self._header_toggle_btn.setToolTip("Show / hide the album header panel")
        self._header_toggle_btn.setStyleSheet(
            "QPushButton { border: none; color: #aaa; font-size: 12px; }"
            "QPushButton:hover { color: #fff; }"
        )
        self._header_toggle_btn.clicked.connect(self._toggle_header)
        bar_row.addWidget(self._header_toggle_btn)

        album_name_lbl = QLabel(f"<b>{self.album.album_name}</b>")
        album_name_lbl.setStyleSheet("color: #ddd; font-size: 13px;")
        bar_row.addWidget(album_name_lbl, 1)

        container_layout.addWidget(toggle_bar)

        # ── Collapsible header content ─────────────────────────────────────
        self._header_content = self._build_header_section()
        container_layout.addWidget(self._header_content)

        return container

    def _toggle_header(self):
        """Show or hide the full header panel and flip the arrow."""
        visible = self._header_content.isVisible()
        self._header_content.setVisible(not visible)
        self._header_toggle_btn.setText("▶" if visible else "▼")

    def _build_header_section(self):
        """Cover image on the left; editable album info on the right."""
        header = QWidget()
        row = QHBoxLayout(header)
        row.setSpacing(20)

        row.addWidget(self._build_cover_widget())
        row.addWidget(self._build_info_section(), 1)
        return header

    def _build_cover_widget(self):
        """Cover thumbnail only — no Change Cover button (use Artwork tab)."""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setAlignment(Qt.AlignTop)

        self.cover_label = QLabel()
        self.cover_label.setAlignment(Qt.AlignCenter)
        self.cover_label.setFixedSize(200, 200)
        self.cover_label.setStyleSheet("border: 1px solid #555; background: #2a2a2a;")
        self._load_album_cover()
        layout.addWidget(self.cover_label)

        hint = QLabel("Change cover in\nArtwork tab ↓")
        hint.setAlignment(Qt.AlignCenter)
        hint.setStyleSheet("color: #666; font-size: 10px;")
        layout.addWidget(hint)

        return widget

    def _build_info_section(self):
        """Right-hand side of the header: title, subtitle, artists, date, description, links."""
        widget = QWidget()
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
            artist_label.setStyleSheet("color: #aaa; font-size: 11px;")
            all_names = ", ".join(a.artist_name for a in self.album.album_artists)
            artist_label.setToolTip(all_names)
            layout.addWidget(artist_label)

        # Release date row
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

        # Description
        desc_label = QLabel("Description:")
        desc_label.setStyleSheet("font-weight: bold;")
        layout.addWidget(desc_label)

        self.desc_widget = self.field_widgets.get("album_description")
        if self.desc_widget is None:
            self.desc_widget = QTextEdit()
            self.desc_widget.setPlainText(self.album.album_description or "")

        self.desc_widget.setMinimumHeight(60)
        self.desc_widget.setMaximumHeight(120)
        self.desc_widget.setStyleSheet("padding: 2px 4px;")
        self.desc_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        layout.addWidget(self.desc_widget)

        # External links
        self._links_row = QHBoxLayout()
        self._links_row.setSpacing(8)

        self._wiki_btn = None
        self._mb_btn = None

        self._rebuild_link_buttons()

        wiki_search_btn = QPushButton("🔍 Search Wikipedia…")
        wiki_search_btn.clicked.connect(self._search_wikipedia)
        self._links_row.addWidget(wiki_search_btn)
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
            mb_url = f"https://musicbrainz.org/release/{mbid}"
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
        """Save + Cancel + Refresh from DB.  No separate Close button."""
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
            if current != original:
                return True

        if self.desc_widget is not None:
            if hasattr(self.desc_widget, "toPlainText"):
                desc_val = self.desc_widget.toPlainText().strip() or None
            elif hasattr(self.desc_widget, "text"):
                desc_val = self.desc_widget.text().strip() or None
            else:
                desc_val = None
            if desc_val != (self.album.album_description or None):
                return True

        return False

    # =========================================================================
    # Cover art — loading helpers
    # =========================================================================

    def _load_album_cover(self):
        """Load the front cover thumbnail into the header label."""
        path = getattr(self.album, "front_cover_path", None)
        if path:
            px = QPixmap()
            loaded = (
                px.loadFromData(path) if isinstance(path, bytes) else px.load(str(path))
            )
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
            display = getattr(self, f"{cover_type}_cover_display", None)
            path_label = getattr(self, f"{cover_type}_path_label", None)
            if display is None:
                continue
            path = getattr(self.album, attr, None)
            if path:
                px = QPixmap()
                loaded = (
                    px.loadFromData(path)
                    if isinstance(path, bytes)
                    else px.load(str(path))
                )
                if loaded and not px.isNull():
                    display.setPixmap(
                        px.scaled(250, 250, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                    )
                    if path_label:
                        path_label.setText(
                            str(path) if isinstance(path, str) else "(binary)"
                        )
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
        """Open a file dialog, copy the image to ALBUM_ART_DIR, update the album."""
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
            dest = self._copy_cover_to_album_dir(path, cover_type)

            attr_map = {
                "front": "front_cover_path",
                "rear": "rear_cover_path",
                "liner": "album_liner_path",
            }
            attr = attr_map[cover_type]
            setattr(self.album, attr, str(dest))
            self.controller.update.update_entity(
                "Album", self.album.album_id, **{attr: str(dest)}
            )

            # Update the Artwork tab preview
            display = getattr(self, f"{cover_type}_cover_display", None)
            path_label = getattr(self, f"{cover_type}_path_label", None)
            if display:
                self._load_image_to_label(str(dest), display, 250)
            if path_label:
                path_label.setText(str(dest))

            # IMPORTANT: always refresh the header thumbnail when front cover changes
            if cover_type == "front":
                self._load_album_cover()

        except Exception as e:
            logger.error(f"Error saving {cover_type} cover: {e}")
            QMessageBox.critical(self, "Error", f"Could not save cover art:\n{e}")

    @staticmethod
    def _sanitize_filename(name: str) -> str:
        """Strip characters that are illegal in file/folder names."""
        return _sanitize_filename(name)

    def _copy_cover_to_album_dir(self, source_path: str, cover_type: str) -> Path:
        """Copy cover art into ALBUM_ART_DIR/artist/album/ and return the destination path."""
        source = Path(source_path)
        ext = source.suffix.lower() or ".jpg"

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
    # Save / refresh
    # =========================================================================

    def save_changes(self):
        try:
            kwargs = {}
            for field_name, widget in self.field_widgets.items():
                if field_name == "album_description":
                    continue
                field_config = ALBUM_FIELDS.get(field_name)
                if not (field_config and field_config.editable):
                    continue

                # NullableSpinBox has its own .value() that returns None when unchecked
                if isinstance(widget, NullableSpinBox):
                    kwargs[field_name] = widget.value()
                else:
                    kwargs[field_name] = AlbumUIComponents.get_field_value(
                        widget, field_config.type
                    )

            # Capture description from the header widget
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
            QMessageBox.information(self, "Refreshed", "Data reloaded from database.")
        except Exception as e:
            logger.error(f"Error refreshing from database: {e}")
            QMessageBox.critical(self, "Error", f"Could not refresh: {e}")

    # =========================================================================
    # Tab rebuild helpers
    # =========================================================================

    def _get_tab_rebuild_map(self) -> dict:
        """Return a mapping of tab title → builder callable."""
        return {
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
                    new_tab = builder()
                    self.tabs.removeTab(idx)
                    self.tabs.insertTab(idx, new_tab, title)
                    # Don't change the active tab — just silently update it
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
