"""AlbumsTab: the track's primary album and its virtual appearances."""

from __future__ import annotations

import sqlite3
import webbrowser

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QMenu, QMessageBox, QPushButton, QScrollArea, QSpinBox, QToolButton, QVBoxLayout, QWidget
from sqlalchemy.exc import SQLAlchemyError

from src.award.award_series_import import import_awards_for_entity
from src.common.widgets.detail_card import DetailCard
from src.common.widgets.entity_completer_context import album_context_map
from src.common.widgets.entity_completer_edit import build_entity_search_widget, find_or_create_by_name, get_cached_entities, register_cached_entity
from src.common.widgets.style_utils import set_style_property
from src.foundation.logger_config import logger
from src.image.artwork_cache import get_artwork_cache
from src.musicbrainz.musicbrainz_artist import suggest_artist_names
from src.musicbrainz.musicbrainz_core import MusicBrainzLookupError
from src.musicbrainz.musicbrainz_match_dialog import MusicBrainzImportDialog, MusicBrainzMatchDialog
from src.musicbrainz.musicbrainz_recording import search_canonical_album_for_recording
from src.track.edit.canonical_album_resolver import CanonicalAlbumResolver
from src.track.edit.track_edit_basetab import _BaseTab

_ART_SIZE = 96
_ROW_ART_SIZE = 36


def _show_album_art(label: QLabel, album, size: int, fallback: str) -> None:
    """Show the album's front cover in `label` at `size`, else the `fallback` text."""
    pixmap = None
    try:
        cache = get_artwork_cache()
        if cache:
            pixmap = cache.get_pixmap(album, "front", bool(getattr(album, "art_is_explicit", False)))
    except (OSError, sqlite3.Error) as e:
        logger.warning(f"Failed to load album art for tab display: {e}")
        pixmap = None

    if pixmap and not pixmap.isNull():
        label.setText("")
        label.setPixmap(pixmap.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation))
    else:
        label.setPixmap(QPixmap())
        label.setText(fallback)


class _ClickableLabel(QLabel):
    """QLabel that emits `clicked` on a left click while it is marked clickable."""

    clicked = Signal()

    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self._clickable = False

    def set_clickable(self, clickable: bool, tooltip: str = "") -> None:
        """Turn the click, hand cursor, tooltip and the [clickable] QSS state on or off."""
        self._clickable = clickable
        self.setCursor(Qt.PointingHandCursor if clickable else Qt.ArrowCursor)
        self.setToolTip(tooltip if clickable else "")
        set_style_property(self, "clickable", clickable)

    def mouseReleaseEvent(self, event):
        if self._clickable and event.button() == Qt.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class _VirtualAppearanceRow(QFrame):
    """One virtual appearance: art thumbnail, album name, "Disc · Track · Side", and Open/Remove."""

    open_requested = Signal(int)  # album_id
    remove_requested = Signal(int)  # virtual_id

    def __init__(self, link, parent=None):
        super().__init__(parent)
        self.setObjectName("VirtualAppearanceRow")
        album = link.album

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(10)

        art = QLabel()
        art.setObjectName("VirtualAppearanceArt")
        art.setFixedSize(_ROW_ART_SIZE, _ROW_ART_SIZE)
        art.setAlignment(Qt.AlignCenter)
        _show_album_art(art, album, _ROW_ART_SIZE, "♪")
        layout.addWidget(art)

        name = _ClickableLabel(album.album_name or "Untitled")
        name.setObjectName("VirtualAppearanceName")
        name.setWordWrap(True)
        name.set_clickable(True, "Open the album editor")
        name.clicked.connect(lambda: self.open_requested.emit(album.album_id))
        layout.addWidget(name, stretch=1)

        bits = []
        if link.virtual_disc_number:
            bits.append(f"Disc {link.virtual_disc_number}")
        if link.virtual_track_number:
            bits.append(f"Track {link.virtual_track_number}")
        if link.virtual_side:
            bits.append(f"Side {link.virtual_side}")
        meta = QLabel(" · ".join(bits))
        meta.setObjectName("VirtualAppearanceMeta")
        layout.addWidget(meta)

        open_btn = QPushButton("Open")
        open_btn.setProperty("rowAction", True)
        open_btn.setToolTip("Open the album editor")
        open_btn.clicked.connect(lambda: self.open_requested.emit(album.album_id))
        layout.addWidget(open_btn)

        remove_btn = QPushButton("Remove")
        remove_btn.setProperty("rowAction", True)
        remove_btn.setProperty("danger", True)
        remove_btn.setToolTip("Remove this track from the album's virtual tracks")
        remove_btn.clicked.connect(lambda: self.remove_requested.emit(link.virtual_id))
        layout.addWidget(remove_btn)


class AlbumsTab(_BaseTab):
    """Show, change or remove the primary album, and manage virtual appearances."""

    saves_immediately = True  # add/remove write to the DB at once

    def __init__(self, tracks: list, controller, parent=None, dialog=None):
        super().__init__(tracks, controller, parent)
        self._dialog = dialog
        self._wiki_link = ""
        self._mb_link = ""
        self._build_ui()

    def _live_track_name(self) -> str:
        """Return the track title, with an unsaved Basic tab edit if there is one."""
        if self._dialog is not None:
            return self._dialog.get_live_track_name()
        return self.track.track_name

    # ── UI ────────────────────────────────────────────────────────────────

    def _build_ui(self):
        """Build the current-album and virtual-appearance cards."""
        # Same scroll area and margins as FieldFormTab, so all tabs line up.
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setObjectName("FieldFormScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        body = QWidget()
        body.setProperty("bgTransparent", True)
        layout = QVBoxLayout(body)
        layout.setContentsMargins(0, 0, 6, 0)
        layout.setSpacing(12)
        scroll.setWidget(body)
        outer.addWidget(scroll)

        layout.addWidget(self._build_current_card())
        layout.addWidget(self._build_virtual_card())
        layout.addStretch(1)

    def _build_current_card(self) -> DetailCard:
        """Build the Current Album card: art, name, links, and the view/change action rows."""
        card = DetailCard("Current Album")

        # ── Header: external links and the rare actions menu ──
        self._wiki_open_btn = self._link_button("Wikipedia", "Open the album's Wikipedia page in your browser", self._open_wiki_link)
        card.add_header_action(self._wiki_open_btn)
        self._mb_open_btn = self._link_button("MusicBrainz", "Open the album's MusicBrainz page in your browser", self._open_mb_link)
        card.add_header_action(self._mb_open_btn)

        self._more_btn = QToolButton()
        self._more_btn.setObjectName("AlbumTabMore")
        self._more_btn.setText("⋯")
        self._more_btn.setToolTip("More album actions")
        self._more_btn.setPopupMode(QToolButton.InstantPopup)
        more_menu = QMenu(self._more_btn)
        more_menu.setToolTipsVisible(True)
        self._find_canonical_action = more_menu.addAction("Find Canonical Album (MusicBrainz)…")
        self._find_canonical_action.triggered.connect(self._find_canonical_album)
        if self.is_multi:
            self._find_canonical_action.setEnabled(False)
            self._find_canonical_action.setToolTip("Works on one track at a time")
        else:
            self._find_canonical_action.setToolTip("Search MusicBrainz for the earliest release of this recording by its primary artist, and link or create the matching local album")
        self._more_btn.setMenu(more_menu)
        card.add_header_action(self._more_btn)

        # ── Album identity: art + name + "artist · year" ──
        info_row = QHBoxLayout()
        info_row.setSpacing(14)

        self._art_label = _ClickableLabel()
        self._art_label.setObjectName("AlbumTabArt")
        self._art_label.setFixedSize(_ART_SIZE, _ART_SIZE)
        self._art_label.setAlignment(Qt.AlignCenter)
        self._art_label.setProperty("textRole", "note")
        self._art_label.clicked.connect(self._open_primary_album)
        info_row.addWidget(self._art_label, alignment=Qt.AlignTop)

        text_col = QVBoxLayout()
        text_col.setSpacing(4)

        self._primary_label = _ClickableLabel("—")
        self._primary_label.setObjectName("AlbumTabTitle")
        self._primary_label.setWordWrap(True)
        self._primary_label.clicked.connect(self._open_primary_album)
        text_col.addWidget(self._primary_label)

        self._primary_meta_label = QLabel()
        self._primary_meta_label.setObjectName("AlbumTabMeta")
        self._primary_meta_label.setWordWrap(True)
        text_col.addWidget(self._primary_meta_label)
        text_col.addStretch(1)
        info_row.addLayout(text_col, stretch=1)
        card.body.addLayout(info_row)

        # ── View mode: Open / Change … Remove ──
        self._view_row = QWidget()
        self._view_row.setProperty("bgTransparent", True)
        view_layout = QHBoxLayout(self._view_row)
        view_layout.setContentsMargins(0, 4, 0, 0)

        self._open_primary_btn = QPushButton("Open Album")
        self._open_primary_btn.setEnabled(False)
        self._open_primary_btn.clicked.connect(self._open_primary_album)
        view_layout.addWidget(self._open_primary_btn)

        self._change_album_btn = QPushButton("Change…")
        self._change_album_btn.setToolTip("Search for a different album, or create a new one")
        self._change_album_btn.clicked.connect(lambda: self._set_change_mode(True))
        view_layout.addWidget(self._change_album_btn)
        view_layout.addStretch(1)

        self._remove_primary_btn = QPushButton("Remove from Album")
        self._remove_primary_btn.setProperty("danger", True)
        self._remove_primary_btn.setEnabled(False)
        self._remove_primary_btn.setToolTip("Detaches the track from its album (the track stays in the library)")
        self._remove_primary_btn.clicked.connect(self._remove_primary_album)
        view_layout.addWidget(self._remove_primary_btn)
        card.body.addWidget(self._view_row)

        # ── Change mode: search + Set / Cancel (replaces the view row) ──
        self._change_row = QWidget()
        self._change_row.setProperty("bgTransparent", True)
        change_layout = QHBoxLayout(self._change_row)
        change_layout.setContentsMargins(0, 4, 0, 0)

        self._album_search = build_entity_search_widget(
            self.controller, "Album", "album_name", "album_id", "Search albums, or type a new name to create one…", context_builder=album_context_map, allow_create_new=True
        )
        self._album_search.textChanged.connect(self._on_album_search_changed)
        self._album_search.returnPressed.connect(self._set_primary_album)
        change_layout.addWidget(self._album_search, stretch=1)

        self._set_primary_btn = QPushButton("Set Album")
        self._set_primary_btn.setProperty("class", "primary")
        self._set_primary_btn.setEnabled(False)
        self._set_primary_btn.clicked.connect(self._set_primary_album)
        change_layout.addWidget(self._set_primary_btn)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(lambda: self._set_change_mode(False))
        change_layout.addWidget(cancel_btn)
        self._cancel_shortcut(self._album_search, lambda: self._set_change_mode(False))
        self._change_row.setVisible(False)
        card.body.addWidget(self._change_row)

        self._album_error = self._error_label()
        card.body.addWidget(self._album_error)
        return card

    def _build_virtual_card(self) -> DetailCard:
        """Build the Virtual Appearances card: rich rows, empty state, and the inline add form."""
        card = DetailCard("Virtual Appearances")
        self._virtual_group = card

        self._virt_show_add_btn = QPushButton("+ Add")
        self._virt_show_add_btn.setProperty("rowAction", True)
        self._virt_show_add_btn.setToolTip("Add this track to another album as a virtual appearance")
        self._virt_show_add_btn.clicked.connect(lambda: self._set_virtual_add_mode(True))
        self._virt_show_add_btn.setEnabled(not self.is_multi)
        card.add_header_action(self._virt_show_add_btn)

        card.body.addWidget(self._note_label("Other albums that borrow this track."))

        self._virtual_rows = QVBoxLayout()
        self._virtual_rows.setSpacing(6)
        card.body.addLayout(self._virtual_rows)

        self._virtual_empty = QLabel()
        self._virtual_empty.setObjectName("VirtualAppearanceEmpty")
        self._virtual_empty.setWordWrap(True)
        card.body.addWidget(self._virtual_empty)

        # ── Inline add form (revealed by "+ Add") ──
        self._virt_add_row = QWidget()
        self._virt_add_row.setProperty("bgTransparent", True)
        add_layout = QHBoxLayout(self._virt_add_row)
        add_layout.setContentsMargins(0, 4, 0, 0)

        self._virt_search = build_entity_search_widget(self.controller, "Album", "album_name", "album_id", "Search albums…", context_builder=album_context_map, allow_create_new=True)
        self._virt_search.textChanged.connect(self._on_virt_search_changed)
        self._virt_search.returnPressed.connect(self._add_virtual)
        add_layout.addWidget(self._virt_search, stretch=1)

        self._virt_disc_num = QSpinBox()
        self._virt_disc_num.setRange(0, 99)
        self._virt_disc_num.setSpecialValueText("Disc")
        self._virt_disc_num.setToolTip("Disc number in this virtual appearance")
        add_layout.addWidget(self._virt_disc_num)

        self._virt_track_num = QSpinBox()
        self._virt_track_num.setRange(0, 999)
        self._virt_track_num.setSpecialValueText("Track")
        self._virt_track_num.setToolTip("Track number in this virtual appearance")
        add_layout.addWidget(self._virt_track_num)

        self._virt_side = QLineEdit()
        self._virt_side.setPlaceholderText("Side")
        self._virt_side.setMaxLength(4)
        self._virt_side.setFixedWidth(56)
        self._virt_side.setToolTip("Vinyl side in this virtual appearance, e.g. A")
        self._virt_side.returnPressed.connect(self._add_virtual)
        add_layout.addWidget(self._virt_side)

        self._virt_add_btn = QPushButton("Add")
        self._virt_add_btn.setProperty("class", "primary")
        self._virt_add_btn.setEnabled(False)
        self._virt_add_btn.clicked.connect(self._add_virtual)
        add_layout.addWidget(self._virt_add_btn)

        virt_cancel_btn = QPushButton("Cancel")
        virt_cancel_btn.clicked.connect(lambda: self._set_virtual_add_mode(False))
        add_layout.addWidget(virt_cancel_btn)
        for widget in (self._virt_search, self._virt_side):
            self._cancel_shortcut(widget, lambda: self._set_virtual_add_mode(False))
        self._virt_add_row.setVisible(False)
        card.body.addWidget(self._virt_add_row)

        self._virt_error = self._error_label()
        card.body.addWidget(self._virt_error)
        return card

    @staticmethod
    def _note_label(text: str) -> QLabel:
        """Return a muted, word-wrapped hint label for a card body."""
        label = QLabel(text)
        label.setProperty("textRole", "note")
        label.setWordWrap(True)
        return label

    @staticmethod
    def _error_label() -> QLabel:
        """Return a hidden inline error label for a card body."""
        label = QLabel()
        label.setProperty("textRole", "error")
        label.setWordWrap(True)
        label.setVisible(False)
        return label

    @staticmethod
    def _link_button(text: str, tooltip: str, slot) -> QPushButton:
        """Return a small, hidden link-style header button."""
        btn = QPushButton(text)
        btn.setObjectName("AlbumTabLink")
        btn.setCursor(Qt.PointingHandCursor)
        btn.setToolTip(tooltip)
        btn.clicked.connect(slot)
        btn.setVisible(False)
        return btn

    @staticmethod
    def _cancel_shortcut(widget: QWidget, slot) -> None:
        """Run `slot` when Esc is pressed in `widget` (so Esc closes the form, not the dialog)."""
        QShortcut(QKeySequence.Cancel, widget, slot, context=Qt.WidgetShortcut)

    @staticmethod
    def _show_error(label: QLabel, text: str = "") -> None:
        """Show `text` in an inline error label, or hide the label when `text` is empty."""
        label.setText(text)
        label.setVisible(bool(text))

    def _set_change_mode(self, active: bool) -> None:
        """Swap the view row for the album search row, or back."""
        self._view_row.setVisible(not active)
        self._change_row.setVisible(active)
        self._show_error(self._album_error)
        if active:
            self._album_search.setFocus()
        else:
            self._album_search.reset()
            self._change_album_btn.setFocus()

    def _set_virtual_add_mode(self, active: bool) -> None:
        """Show or hide the inline add-virtual-appearance form."""
        self._virt_add_row.setVisible(active)
        self._virt_show_add_btn.setVisible(not active)
        self._show_error(self._virt_error)
        if active:
            self._virt_search.setFocus()
        else:
            self._virt_search.reset()
            self._virt_disc_num.setValue(0)
            self._virt_track_num.setValue(0)
            self._virt_side.clear()

    # ── Loading ───────────────────────────────────────────────────────────

    def load(self, tracks: list) -> None:
        """Show the primary album and the virtual appearances of `tracks`."""
        self.tracks = tracks

        if self.is_multi:
            track_albums = [getattr(t, "album", None) for t in self.tracks]
            album_ids = {a.album_id if a else None for a in track_albums}
            if len(album_ids) == 1:
                album = track_albums[0]
                self._set_primary_display(album)
                self._remove_primary_btn.setEnabled(bool(album))
            else:
                self._set_mixed_display()
                self._remove_primary_btn.setEnabled(True)
            # The album editor reloads a single track, so opening is single-track only.
            self._set_open_enabled(False)
            self._set_primary_btn.setEnabled(bool(self._album_search.text().strip()))
            self._set_virtual_rows([])
            return

        # Primary album
        album = getattr(self.track, "album", None)
        self._set_primary_display(album)
        self._set_open_enabled(bool(album))
        self._remove_primary_btn.setEnabled(bool(album))

        links = [link for link in getattr(self.track, "virtual_appearances", []) if getattr(link, "album", None)]
        self._set_virtual_rows(links)

    def _set_open_enabled(self, enabled: bool) -> None:
        """Enable or disable every way to open the primary album (button, name, art)."""
        self._open_primary_btn.setEnabled(enabled)
        self._primary_label.set_clickable(enabled, "Open the album editor")
        self._art_label.set_clickable(enabled, "Open the album editor")

    def _set_title_mixed(self, mixed: bool) -> None:
        """Toggle the muted "mixed albums" style of the album title."""
        set_style_property(self._primary_label, "mixed", mixed)

    def _clear_links(self) -> None:
        self._wiki_link = ""
        self._mb_link = ""
        self._wiki_open_btn.setVisible(False)
        self._mb_open_btn.setVisible(False)

    def _set_mixed_display(self) -> None:
        """Show the header for a multi-track selection whose tracks are on different albums."""
        self._set_title_mixed(True)
        self._primary_label.setText("Multiple albums")
        self._primary_meta_label.setText(f"The {len(self.tracks)} selected tracks are on different albums.")
        self._art_label.setPixmap(QPixmap())
        self._art_label.setText("—")
        self._clear_links()

    def _set_primary_display(self, album) -> None:
        """Populate the Current Album header (art, name, artist · year, links)."""
        if not album:
            self._set_title_mixed(True)
            self._primary_label.setText("No album")
            self._primary_meta_label.setText("Use Change… to put this track on an album.")
            self._art_label.setPixmap(QPixmap())
            self._art_label.setText("—")
            self._clear_links()
            return

        self._set_title_mixed(False)
        self._primary_label.setText(album.album_name or "Untitled")
        artist = getattr(album, "album_artist_names", None) or "Unknown Artist"
        year = getattr(album, "release_year", None)
        self._primary_meta_label.setText(f"{artist} · {year}" if year else artist)
        _show_album_art(self._art_label, album, _ART_SIZE, "No Art")

        self._wiki_link = getattr(album, "album_wikipedia_link", None) or ""
        self._wiki_open_btn.setVisible(bool(self._wiki_link))

        mbid = getattr(album, "MBID", None)
        self._mb_link = f"https://musicbrainz.org/release/{mbid}" if mbid else ""
        self._mb_open_btn.setVisible(bool(self._mb_link))

    def _set_virtual_rows(self, links: list) -> None:
        """Rebuild the virtual-appearance rows, the count badge and the empty state."""
        while self._virtual_rows.count():
            item = self._virtual_rows.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for link in links:
            row = _VirtualAppearanceRow(link)
            row.open_requested.connect(lambda aid: self._open_album_by_id(aid, self._virt_error))
            row.remove_requested.connect(self._remove_virtual)
            self._virtual_rows.addWidget(row)

        self._virtual_group.set_count(len(links), "appearance")
        if self.is_multi:
            self._virtual_empty.setText("Edit virtual appearances one track at a time.")
        else:
            self._virtual_empty.setText("This track is not on any other album.")
        self._virtual_empty.setVisible(not links)

    # ── Primary album search / set / remove ──────────────────────────────

    def _on_album_search_changed(self, text: str):
        """Enable "Set Album" when the search has text."""
        self._set_primary_btn.setEnabled(bool(text.strip()))

    def _known_albums(self, widget) -> list:
        """Return the albums to check for a case-insensitive duplicate name."""
        # The full cached table when small enough, else the widget's last query.
        cached = get_cached_entities(self.controller, "Album")
        if cached is not None:
            return cached
        known_matches = getattr(widget, "known_matches", None)
        return known_matches() if known_matches is not None else []

    def _resolve_album(self, widget):
        """Return the album named in `widget`: the completer pick, a new album, or find-or-create."""
        # "Create new" always makes a new album: same-name albums are legitimate (reissues).
        matched_id = widget.matched_id()
        if matched_id is not None:
            return self.controller.get.get_entity_object("Album", album_id=matched_id)
        name = widget.text().strip()
        if not name:
            return None
        known = self._known_albums(widget)
        album = self.controller.add.add_entity("Album", album_name=name) if widget.wants_new_entity() else find_or_create_by_name(self.controller, "Album", "album_name", name, known)
        if album is not None and album not in known:
            # Deferred: this can run nested inside the completer's own
            # keyPressEvent (Enter -> returnPressed), and add_to_index()
            # rebuilds the QCompleter in place -- doing that mid key-dispatch
            # corrupts its internals. See track_edit_places.py _add().
            aid, aname = album.album_id, album.album_name
            QTimer.singleShot(0, lambda: widget.add_to_index(aname, aid))
            register_cached_entity("Album", album)
        return album

    def _disc_link_is_stale(self, track, new_album_id) -> bool:
        """Return True if the track's disc belongs to an album other than `new_album_id`."""
        # A stale disc_id keeps the track reachable through Album.discs -> Disc.tracks.
        if getattr(track, "disc_id", None) is None:
            return False
        disc = getattr(track, "disc", None)
        if disc is None:
            return False
        return disc.album_id != new_album_id

    def _apply_primary_album(self, new_album_id) -> bool:
        """Set or clear (None) the primary album of every track; True if all writes succeed."""
        keep_disc, drop_disc = [], []
        for track in self.tracks:
            bucket = drop_disc if self._disc_link_is_stale(track, new_album_id) else keep_disc
            bucket.append(track.track_id)
        ok = True
        if keep_disc:
            ok = self.controller.update.update_entities("Track", keep_disc, album_id=new_album_id) and ok
        if drop_disc:
            ok = self.controller.update.update_entities("Track", drop_disc, album_id=new_album_id, disc_id=None) and ok
        return ok

    def _set_primary_album(self):
        """Make the searched album the primary album of every track."""
        self._show_error(self._album_error)
        album = self._resolve_album(self._album_search)
        if not album:
            if self._album_search.text().strip():
                self._show_error(self._album_error, "Could not find or create that album.")
            return

        if self.is_multi:
            confirm = QMessageBox.question(self, "Set Primary Album", f"Set '{album.album_name}' as the primary album for all {len(self.tracks)} selected tracks?", QMessageBox.Yes | QMessageBox.No)
            if confirm != QMessageBox.Yes:
                return

        if not self._apply_primary_album(album.album_id):
            self._show_error(self._album_error, "Could not set the album. See the log for details.")
            return

        self._set_change_mode(False)
        self._refresh_tracks()
        self.load(self.tracks)

    def _remove_primary_album(self):
        """Detach every track from its primary album after confirmation."""
        if self.is_multi:
            question = f"Detach all {len(self.tracks)} selected tracks from their primary album?\nThe tracks will remain in the library but will have no album."
        else:
            question = "Detach this track from its primary album?\nThe track will remain in the library but will have no album."
        confirm = QMessageBox.question(self, "Remove Album Relationship", question, QMessageBox.Yes | QMessageBox.No)
        if confirm != QMessageBox.Yes:
            return

        if not self._apply_primary_album(None):
            self._show_error(self._album_error, "Could not remove the album. See the log for details.")
            return
        self._show_error(self._album_error)

        self._refresh_tracks()
        self.load(self.tracks)

    def _refresh_tracks(self):
        """Reload the tracks with fresh album and disc relationships."""
        # expire_on_commit=False: the cached relationships survive a commit, so expire them.
        session = self.controller.get.session
        updated_tracks = []
        for track in self.tracks:
            session.expire(track, ["album", "disc"])
            updated = self.controller.get.get_entity_object("Track", track_id=track.track_id)
            updated_tracks.append(updated if updated else track)
        self.tracks = updated_tracks

    def _open_primary_album(self):
        """Open the album editor for the primary album."""
        album = getattr(self.track, "album", None)
        if album:
            self._open_album_by_id(album.album_id, self._album_error)

    def _open_wiki_link(self):
        """Open the album's Wikipedia page."""
        if self._wiki_link:
            webbrowser.open(self._wiki_link)

    def _open_mb_link(self):
        """Open the album's MusicBrainz page."""
        if self._mb_link:
            webbrowser.open(self._mb_link)

    # ── Find canonical album (MusicBrainz) ─────────────────────────────────

    def _find_canonical_album(self):
        """Find the earliest release of this recording on MusicBrainz and link it."""
        track_name = self._live_track_name()
        artist_name = self.track.primary_artist_names
        if artist_name == "Unknown Artist":
            artist_name = None

        dialog = MusicBrainzMatchDialog(
            entity_label=f"canonical album for '{track_name}'", search_call=lambda: search_canonical_album_for_recording(track_name, artist_name, recording_mbid=self.track.MBID), parent=self
        )
        if dialog.exec() != QDialog.Accepted:
            if dialog.candidate_count() == 0:
                self._offer_artist_name_suggestions(artist_name)
            return

        picked = dialog.result_candidate()
        if picked is None:
            return

        # Awards enrichment for each linked entity runs on a worker at the end (see _import_award_data).
        award_jobs: list[tuple[str, int, str]] = []

        album = self._canonical_album_resolver().resolve_or_create_album(picked.enrichment, award_jobs)
        if album is None:
            return

        # Also stamp the picked recording's MBID onto the track (fill-blank only).
        update_kwargs = {"album_id": album.album_id}
        recording_mbid = picked.enrichment.get("recording_mbid")
        if recording_mbid and not self.track.MBID:
            update_kwargs["MBID"] = recording_mbid

        # update_entity catches its own DB errors and returns False.
        if not self.controller.update.update_entity("Track", self.track.track_id, **update_kwargs):
            logger.error(f"Failed to set canonical album {album.album_id} on track {self.track.track_id}")
            self._show_error(self._album_error, "Could not set the album on this track. See the log for details.")
            return
        self._show_error(self._album_error)

        if "MBID" in update_kwargs:
            award_jobs.append(("Track", self.track.track_id, update_kwargs["MBID"]))

        self._import_award_data(award_jobs)

        self._refresh_tracks()
        self.load(self.tracks)

    def _import_award_data(self, award_jobs: list[tuple[str, int, str]]) -> None:
        """Import award data for the linked entities on a worker thread, with a spinner."""
        # Each job is a slow MusicBrainz lookup; the worker uses its own scoped session.
        if not award_jobs:
            return

        def _run():
            session = self.controller.get.session
            for entity_type, entity_id, mbid in award_jobs:
                import_awards_for_entity(session, entity_type, entity_id, mbid)
            return len(award_jobs)

        MusicBrainzImportDialog(entity_label="award data", fetch_call=_run, parent=self).exec()

    def _offer_artist_name_suggestions(self, artist_name: str | None) -> None:
        """Show similar MusicBrainz artist names after a search with no matches."""
        if not artist_name:
            return
        try:
            suggestions = suggest_artist_names(artist_name)
        except MusicBrainzLookupError as e:
            logger.warning(f"Could not fetch artist name suggestions: {e}")
            return
        if not suggestions:
            return
        QMessageBox.information(
            self, "No Matches Found", f"No MusicBrainz releases were found for artist '{artist_name}'.\n\nSimilar artists on MusicBrainz:\n" + "\n".join(f"• {s}" for s in suggestions)
        )

    def _canonical_album_resolver(self) -> CanonicalAlbumResolver:
        """Build the MusicBrainz album resolver, with this tab's dialogs as its callbacks."""
        return CanonicalAlbumResolver(self.controller, confirm_create=self._confirm_create_album, warn=lambda title, text: QMessageBox.warning(self, title, text))

    def _confirm_create_album(self, details: str) -> bool:
        """Ask the user to create a new album from the MusicBrainz match details."""
        confirm = QMessageBox.question(self, "Create New Album", "No matching album found locally. Create a new album with these details?\n\n" + details, QMessageBox.Yes | QMessageBox.No)
        return confirm == QMessageBox.Yes

    # ── Virtual appearance search / add / remove ──────────────────────────

    def _on_virt_search_changed(self, text: str):
        """Enable "Add" when the search has text (single track only)."""
        self._virt_add_btn.setEnabled(bool(text.strip()) and not self.is_multi)

    def _add_virtual(self):
        """Add the searched album as a virtual appearance of this track."""
        if self.is_multi:
            return  # a virtual appearance names one track
        self._show_error(self._virt_error)
        album = self._resolve_album(self._virt_search)
        if not album:
            if self._virt_search.text().strip():
                self._show_error(self._virt_error, "Could not find or create that album.")
            return
        track_num = self._virt_track_num.value() or None
        disc_num = self._virt_disc_num.value() or None
        side = self._virt_side.text().strip() or None
        link = self.controller.add.add_entity(
            "AlbumVirtualTrack", album_id=album.album_id, track_id=self.track.track_id, virtual_track_number=track_num, virtual_disc_number=disc_num, virtual_side=side
        )
        if link is None:
            logger.error(f"Failed to add virtual appearance on album {album.album_id} for track {self.track.track_id}")
            self._show_error(self._virt_error, "Could not add the virtual appearance. See the log for details.")
            return
        self._set_virtual_add_mode(False)
        self._reload_virtual_appearances()

    def _remove_virtual(self, virtual_id: int):
        """Remove one virtual appearance after confirmation."""
        question = "Remove this track from the album's virtual tracks?\nThe album and the track stay in the library."
        confirm = QMessageBox.question(self, "Remove Virtual Appearance", question, QMessageBox.Yes | QMessageBox.No)
        if confirm != QMessageBox.Yes:
            return
        if not self.controller.delete.delete_entity("AlbumVirtualTrack", virtual_id=virtual_id):
            logger.error(f"Failed to remove virtual appearance {virtual_id}")
            self._show_error(self._virt_error, "Could not remove the virtual appearance. See the log for details.")
            return
        self._show_error(self._virt_error)
        self._reload_virtual_appearances()

    def _reload_virtual_appearances(self) -> None:
        """Reload the track with a fresh virtual_appearances relationship and redisplay."""
        # The relationship is cached on the Track instance; expire it before the re-fetch.
        self.controller.get.session.expire(self.track, ["virtual_appearances"])
        updated = self.controller.get.get_entity_object("Track", track_id=self.track.track_id)
        if updated:
            self.tracks = [updated]
        self.load(self.tracks)

    # ── Open album editor ─────────────────────────────────────────────────

    def _open_album_by_id(self, album_id: int, error_label: QLabel):
        """Open the album editor, then reload the track; show a failure in `error_label`."""
        try:
            from src.album.edit.base_album_edit import AlbumEditor

            album = self.controller.get.get_entity_object("Album", album_id=album_id)
            if album:
                dlg = AlbumEditor(self.controller, album, self)
                dlg.exec()
                # Refresh track data after album edit closes; expire the
                # cached album relationship first (see _refresh_tracks).
                self.controller.get.session.expire(self.track, ["album"])
                updated = self.controller.get.get_entity_object("Track", track_id=self.track.track_id)
                if updated:
                    self.tracks = [updated]
                self.load(self.tracks)
        except (SQLAlchemyError, RuntimeError) as e:
            logger.error(f"Failed to open album editor: {e}")
            self._show_error(error_label, f"Could not open the album editor: {e}")
