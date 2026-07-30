# ══════════════════════════════════════════════════════════════════════════════
# Tab: Discography
# ══════════════════════════════════════════════════════════════════════════════

import sqlite3

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QCursor, QFontMetrics, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from src.album.album_flowlayout import FlowLayout
from src.common.style_utils import set_style_property
from src.core.logger_config import logger
from src.image.artwork_cache import get_artwork_cache

_CHIP_ART_SIZE = 48
_CHIP_WIDTH = 260
_CHIP_MARGINS = (6, 6, 8, 6)  # left, top, right, bottom
_CHIP_SPACING = 8
# Text column gets whatever width is left after the margins, the art thumb,
# and the spacing between them — used to elide long titles to one line.
_CHIP_TEXT_WIDTH = (
    _CHIP_WIDTH - _CHIP_MARGINS[0] - _CHIP_MARGINS[2] - _CHIP_SPACING - _CHIP_ART_SIZE
)


def _make_table(headers, editable=False):
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.horizontalHeader().setStretchLastSection(True)
    t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
    t.horizontalHeader().setSectionResizeMode(len(headers) - 1, QHeaderView.Stretch)
    t.setSelectionBehavior(QAbstractItemView.SelectRows)
    t.verticalHeader().setVisible(False)
    t.setAlternatingRowColors(True)
    if not editable:
        t.setEditTriggers(QAbstractItemView.NoEditTriggers)
    return t


def _set_item(table, row, col, text):
    item = QTableWidgetItem(str(text) if text is not None else "")
    table.setItem(row, col, item)


def _append_row(table, values):
    row = table.rowCount()
    table.insertRow(row)
    for col, val in enumerate(values):
        _set_item(table, row, col, val)
    return row


class DiscographyTab(QWidget):
    """Read-only summary of album and track credits with role filtering.

    Albums and tracks live in separate subtabs. Albums are shown as a
    compact flow of chips (name / year / album artist) with the full role
    list on hover, since a table row-per-role was noisy and album counts
    are usually small. Clicking a chip drills the track table down to that
    album and switches to the Tracks subtab to show the result; clicking
    it again (or the "show all" link) clears the drill-down.
    """

    def __init__(self, controller, artist, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.artist = artist
        self._album_groups: dict = {}  # album_id -> {id, name, year, album_artist, roles}
        self._track_rows: list[tuple] = []  # (role_name, album_id, values_tuple)
        self._selected_album_id = None
        self._build_ui()

    # ------------------------------------------------------------------ UI

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(4)

        self._sub_tabs = QTabWidget()

        # ── Albums subtab ────────────────────────────────────────────────
        albums_widget = QWidget()
        al_layout = QVBoxLayout(albums_widget)
        al_layout.setContentsMargins(4, 4, 4, 4)
        al_layout.setSpacing(2)

        al_header = QHBoxLayout()
        self._albums_title = QLabel("<b>Album Credits</b>")
        al_header.addWidget(self._albums_title)
        al_header.addStretch()
        al_layout.addLayout(al_header)

        self._album_filter_bar = _FilterBar()
        self._album_filter_bar.changed.connect(self._apply_filters)
        al_layout.addWidget(self._album_filter_bar)

        self._album_flow = _AlbumFlowArea()
        self._album_flow.album_clicked.connect(self._on_album_clicked)
        al_layout.addWidget(self._album_flow)
        self._sub_tabs.addTab(albums_widget, "Albums")

        # ── Tracks subtab ────────────────────────────────────────────────
        tracks_widget = QWidget()
        tr_layout = QVBoxLayout(tracks_widget)
        tr_layout.setContentsMargins(4, 4, 4, 4)
        tr_layout.setSpacing(2)

        tr_header = QHBoxLayout()
        self._tracks_title = QLabel("<b>Track Credits</b>")
        self._tracks_title.setTextFormat(Qt.RichText)
        self._tracks_title.setOpenExternalLinks(False)
        self._tracks_title.linkActivated.connect(self._clear_album_selection)
        tr_header.addWidget(self._tracks_title)
        tr_header.addStretch()
        tr_layout.addLayout(tr_header)

        self._track_filter_bar = _FilterBar()
        self._track_filter_bar.changed.connect(self._apply_filters)
        tr_layout.addWidget(self._track_filter_bar)

        self.tracks_table = _make_table(
            ["Track", "Role", "Album", "Album Artist", "Year"]
        )
        tr_layout.addWidget(self.tracks_table)
        self._sub_tabs.addTab(tracks_widget, "Tracks")

        root.addWidget(self._sub_tabs)

    # ------------------------------------------------------------------ Load

    def load(self, artist):
        self.artist = artist
        self._album_groups = {}
        self._track_rows.clear()
        self._selected_album_id = None

        # Album-artist IDs for redundancy suppression
        album_artist_ids = {
            assoc.album_id
            for assoc in getattr(artist, "album_roles", [])
            if assoc.role and getattr(assoc.role, "role_name", "") == "Album Artist"
        }

        # ── Collect album groups (one chip per album, roles merged) ────────
        for assoc in getattr(artist, "album_roles", []):
            if assoc.album is None:
                continue
            role_name = assoc.role.role_name if assoc.role else ""
            if role_name == "Primary Artist" and assoc.album_id in album_artist_ids:
                continue
            group = self._album_groups.setdefault(
                assoc.album_id,
                {
                    "id": assoc.album_id,
                    "album": assoc.album,
                    "name": assoc.album.album_name,
                    "year": assoc.album.release_year,
                    "album_artist": assoc.album.album_artist_names,
                    "roles": set(),
                },
            )
            group["roles"].add(role_name)

        # ── Collect track rows ────────────────────────────────────────────
        for assoc in getattr(artist, "track_roles", []):
            if assoc.track is None:
                continue
            role_name = assoc.role.role_name if assoc.role else ""
            track = assoc.track
            album = track.album
            album_name = album.album_name if album else ""
            album_artist = album.album_artist_names if album else ""
            year = (album.release_year or "") if album else ""
            album_id = album.album_id if album else None
            if role_name == "Primary Artist" and album_id in album_artist_ids:
                continue
            self._track_rows.append(
                (
                    role_name,
                    album_id,
                    (track.track_name, role_name, album_name, album_artist, year),
                )
            )

        # Rebuild filter bars with discovered roles
        album_roles = sorted(
            {r for g in self._album_groups.values() for r in g["roles"]}
        )
        track_roles = sorted({r for r, _, _ in self._track_rows})
        self._album_filter_bar.set_roles(album_roles)
        self._track_filter_bar.set_roles(track_roles)

        album_list = sorted(
            self._album_groups.values(),
            key=lambda g: (-(g["year"] or 0), g["name"] or ""),
        )
        self._album_flow.set_albums(album_list)
        logger.debug(
            f"Loaded discography for artist_id={getattr(artist, 'artist_id', '?')}: "
            f"{len(self._album_groups)} album(s), {len(self._track_rows)} track credit(s)"
        )

        self._apply_filters()

    # ------------------------------------------------------------------ Interaction

    def _on_album_clicked(self, album_id):
        self._selected_album_id = (
            None if self._selected_album_id == album_id else album_id
        )
        self._album_flow.set_selected(self._selected_album_id)
        self._apply_filters()
        if self._selected_album_id is not None:
            self._sub_tabs.setCurrentIndex(1)  # jump to Tracks to show the drill-down

    def _clear_album_selection(self, *_):
        self._selected_album_id = None
        self._album_flow.set_selected(None)
        self._apply_filters()

    # ------------------------------------------------------------------ Filtering

    def _apply_filters(self):
        album_active = self._album_filter_bar.active_roles()
        self._album_flow.apply_role_filter(album_active)

        visible_albums = [
            g for g in self._album_groups.values() if g["roles"] & album_active
        ]
        album_counts: dict[str, int] = {}
        for g in visible_albums:
            for role in g["roles"] & album_active:
                album_counts[role] = album_counts.get(role, 0) + 1
        self._album_filter_bar.update_counts(album_counts)
        n = len(visible_albums)
        self._albums_title.setText(
            f"<b>Album Credits</b> — {n} album{'s' if n != 1 else ''}"
        )

        # Tracks
        track_active = self._track_filter_bar.active_roles()
        self.tracks_table.setRowCount(0)
        tr_counts: dict[str, int] = {}
        for role_name, album_id, values in self._track_rows:
            if role_name not in track_active:
                continue
            if (
                self._selected_album_id is not None
                and album_id != self._selected_album_id
            ):
                continue
            _append_row(self.tracks_table, values)
            tr_counts[role_name] = tr_counts.get(role_name, 0) + 1
        total_tr = sum(tr_counts.values())
        self._track_filter_bar.update_counts(tr_counts)

        if self._selected_album_id is not None:
            album_name = self._album_groups.get(self._selected_album_id, {}).get(
                "name", ""
            )
            self._tracks_title.setText(
                f"<b>Track Credits</b> — {total_tr} on \u201c{album_name}\u201d"
                f" &nbsp;<a href='clear'>show all</a>"
            )
        else:
            self._tracks_title.setText(
                f"<b>Track Credits</b> — {total_tr} track{'s' if total_tr != 1 else ''}"
            )


# ══════════════════════════════════════════════════════════════════════════════
# Helper: scrollable row of role-filter checkboxes
# ══════════════════════════════════════════════════════════════════════════════


class _FilterBar(QScrollArea):
    """Horizontal scrollable strip of role-filter checkboxes."""

    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFixedHeight(28)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setFrameShape(QScrollArea.NoFrame)

        self._inner = QWidget()
        self._layout = QHBoxLayout(self._inner)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(10)
        self._layout.addStretch()
        self.setWidget(self._inner)

        self._checkboxes: dict[str, QCheckBox] = {}

    def set_roles(self, roles: list[str]):
        # Remove old checkboxes
        for cb in self._checkboxes.values():
            self._layout.removeWidget(cb)
            cb.hide()
            cb.deleteLater()
        self._checkboxes.clear()

        for role in roles:
            cb = QCheckBox(role)
            cb.setChecked(True)
            cb.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)
            cb.stateChanged.connect(self.changed)
            self._layout.insertWidget(self._layout.count() - 1, cb)  # before stretch
            self._checkboxes[role] = cb

        self.setVisible(bool(roles))

    def active_roles(self) -> set[str]:
        return {r for r, cb in self._checkboxes.items() if cb.isChecked()}

    def update_counts(self, counts: dict[str, int]):
        for role, cb in self._checkboxes.items():
            cb.setText(f"{role} ({counts.get(role, 0)})")


# ══════════════════════════════════════════════════════════════════════════════
# Helper: album chip + flow area
# ══════════════════════════════════════════════════════════════════════════════


class _AlbumChip(QFrame):
    """Compact album card. Hover shows the full role list; click drills the
    track table down to this album (click again to clear)."""

    clicked = Signal(object)  # album_id

    def __init__(self, album_id, album, name, year, album_artist, roles, parent=None):
        super().__init__(parent)
        self.album_id = album_id
        self.roles = set(roles)
        self._selected = False

        self.setObjectName("albumChip")
        self.setCursor(QCursor(Qt.PointingHandCursor))
        self.setFrameShape(QFrame.StyledPanel)
        # Fixed (not just maximum) width keeps every chip the same size in
        # the flow layout \u2014 letting width vary with content, combined with
        # word-wrapped labels, made rows wrap unevenly and clipped tall
        # chips whose sizeHint() didn't account for the wrap.
        self.setFixedWidth(_CHIP_WIDTH)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(*_CHIP_MARGINS)
        lay.setSpacing(_CHIP_SPACING)

        self._art_label = QLabel()
        self._art_label.setObjectName("albumChipArt")
        self._art_label.setFixedSize(_CHIP_ART_SIZE, _CHIP_ART_SIZE)
        self._art_label.setAlignment(Qt.AlignCenter)
        self._art_label.setProperty("textRole", "note")
        self._load_art(album)
        lay.addWidget(self._art_label)

        text_col = QVBoxLayout()
        text_col.setSpacing(1)

        title_text = name or "Untitled"
        title = QLabel()
        title.setObjectName("albumChipTitle")
        title.setWordWrap(False)
        title.setText(
            QFontMetrics(title.font()).elidedText(
                title_text, Qt.ElideRight, _CHIP_TEXT_WIDTH
            )
        )
        text_col.addWidget(title)

        sub_bits = [str(year)] if year else []
        if album_artist:
            sub_bits.append(album_artist)
        sub_text = " \u00b7 ".join(sub_bits)
        sub = QLabel()
        sub.setObjectName("albumChipSub")
        sub.setWordWrap(False)
        sub.setText(
            QFontMetrics(sub.font()).elidedText(
                sub_text, Qt.ElideRight, _CHIP_TEXT_WIDTH
            )
        )
        text_col.addWidget(sub)

        lay.addLayout(text_col)

        tooltip_lines = [title_text]
        if sub_text:
            tooltip_lines.append(sub_text)
        tooltip_lines.append(", ".join(sorted(self.roles)))
        self.setToolTip("\n".join(tooltip_lines))

    def _load_art(self, album):
        """Load cover art through the shared artwork cache, same as every
        other album-art surface in the app (AlbumWidget, track_edit_album)."""
        pixmap = None
        try:
            cache = get_artwork_cache()
            if cache is not None and album is not None:
                is_explicit = bool(getattr(album, "art_is_explicit", False))
                pixmap = cache.get_pixmap(album, "front", is_explicit)
        except sqlite3.Error as e:
            logger.warning(f"Failed to load album art for discography chip: {e}")
            pixmap = None

        if pixmap and not pixmap.isNull():
            self._art_label.setText("")
            self._art_label.setPixmap(
                pixmap.scaled(
                    _CHIP_ART_SIZE,
                    _CHIP_ART_SIZE,
                    Qt.KeepAspectRatio,
                    Qt.SmoothTransformation,
                )
            )
        else:
            self._art_label.setPixmap(QPixmap())
            self._art_label.setText("No Art")

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self.album_id)
        super().mousePressEvent(event)

    def set_selected(self, selected: bool):
        if selected == self._selected:
            return
        self._selected = selected
        set_style_property(self, "selected", selected)

    def matches_roles(self, active_roles: set) -> bool:
        return bool(self.roles & active_roles)


class _AlbumFlowArea(QScrollArea):
    """Wrapping strip of album chips, replacing the old album table."""

    album_clicked = Signal(object)  # album_id

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QScrollArea.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        self._inner = QWidget()
        self._flow = FlowLayout(self._inner, margin=2, spacing=8)
        self.setWidget(self._inner)

        self._chips: dict = {}

    def _clear(self):
        while self._flow.count():
            item = self._flow.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        self._chips.clear()

    def set_albums(self, albums: list[dict]):
        self._clear()
        if not albums:
            empty = QLabel("No album credits")
            empty.setStyleSheet("color: palette(mid);")
            self._flow.addWidget(empty)
            return
        for a in albums:
            chip = _AlbumChip(
                a["id"], a["album"], a["name"], a["year"], a["album_artist"], a["roles"]
            )
            chip.clicked.connect(self.album_clicked)
            self._flow.addWidget(chip)
            self._chips[a["id"]] = chip

    def apply_role_filter(self, active_roles: set):
        for chip in self._chips.values():
            chip.setVisible(chip.matches_roles(active_roles))

    def set_selected(self, album_id):
        for aid, chip in self._chips.items():
            chip.set_selected(aid == album_id)
