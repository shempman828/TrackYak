# ══════════════════════════════════════════════════════════════════════════════
# Tab: Discography
# ══════════════════════════════════════════════════════════════════════════════

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
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


def _set_item(table, row, col, text, user_data=None):
    item = QTableWidgetItem(str(text) if text is not None else "")
    if user_data is not None:
        item.setData(Qt.UserRole, user_data)
    table.setItem(row, col, item)


def _append_row(table, values, user_data=None):
    row = table.rowCount()
    table.insertRow(row)
    for col, val in enumerate(values):
        _set_item(table, row, col, val, user_data if col == 0 else None)
    return row


class DiscographyTab(QWidget):
    """Read-only summary of album and track credits with role filtering."""

    def __init__(self, controller, artist, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.artist = artist
        self._album_rows: list[tuple] = []  # (role_name, values_tuple)
        self._track_rows: list[tuple] = []
        self._build_ui()

    # ------------------------------------------------------------------ UI

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(4)

        splitter = QSplitter(Qt.Vertical)
        splitter.setChildrenCollapsible(False)

        # ── Albums panel ──────────────────────────────────────────────────
        albums_widget = QWidget()
        al_layout = QVBoxLayout(albums_widget)
        al_layout.setContentsMargins(0, 0, 0, 0)
        al_layout.setSpacing(2)

        al_header = QHBoxLayout()
        self._albums_title = QLabel("<b>Album Credits</b>")
        al_header.addWidget(self._albums_title)
        al_header.addStretch()
        al_layout.addLayout(al_header)

        self._album_filter_bar = _FilterBar()
        self._album_filter_bar.changed.connect(self._apply_filters)
        al_layout.addWidget(self._album_filter_bar)

        self.albums_table = _make_table(["Album", "Role", "Year", "Album Artist"])
        al_layout.addWidget(self.albums_table)
        splitter.addWidget(albums_widget)

        # ── Tracks panel ──────────────────────────────────────────────────
        tracks_widget = QWidget()
        tr_layout = QVBoxLayout(tracks_widget)
        tr_layout.setContentsMargins(0, 0, 0, 0)
        tr_layout.setSpacing(2)

        tr_header = QHBoxLayout()
        self._tracks_title = QLabel("<b>Track Credits</b>")
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
        splitter.addWidget(tracks_widget)

        root.addWidget(splitter)

    # ------------------------------------------------------------------ Load

    def load(self, artist):
        self.artist = artist
        self._album_rows.clear()
        self._track_rows.clear()

        # Album-artist IDs for redundancy suppression
        album_artist_ids = {
            assoc.album_id
            for assoc in getattr(artist, "album_roles", [])
            if assoc.role and getattr(assoc.role, "role_name", "") == "Album Artist"
        }

        # ── Collect album rows ────────────────────────────────────────────
        for assoc in getattr(artist, "album_roles", []):
            if assoc.album is None:
                continue
            role_name = assoc.role.role_name if assoc.role else ""
            if role_name == "Primary Artist" and assoc.album_id in album_artist_ids:
                continue
            album_artist = _primary_artist_name(assoc.album)
            self._album_rows.append(
                (
                    role_name,
                    (
                        assoc.album.album_name,
                        role_name,
                        assoc.album.release_year or "",
                        album_artist,
                    ),
                )
            )

        # ── Collect track rows ────────────────────────────────────────────
        # Albums where this artist IS the album artist → suppress "Primary Artist" track credits
        for assoc in getattr(artist, "track_roles", []):
            if assoc.track is None:
                continue
            role_name = assoc.role.role_name if assoc.role else ""
            track = assoc.track
            album = track.album
            album_name = album.album_name if album else ""
            album_artist = _primary_artist_name(album) if album else ""
            year = (album.release_year or "") if album else ""
            album_id = album.id if album else None
            if role_name == "Primary Artist" and album_id in album_artist_ids:
                continue
            self._track_rows.append(
                (
                    role_name,
                    (track.track_name, role_name, album_name, album_artist, year),
                )
            )

        # Rebuild filter bars with discovered roles
        album_roles = sorted({r for r, _ in self._album_rows})
        track_roles = sorted({r for r, _ in self._track_rows})
        self._album_filter_bar.set_roles(album_roles)
        self._track_filter_bar.set_roles(track_roles)

        self._apply_filters()

    # ------------------------------------------------------------------ Filtering

    def _apply_filters(self):
        album_active = self._album_filter_bar.active_roles()
        track_active = self._track_filter_bar.active_roles()

        # Albums
        self.albums_table.setRowCount(0)
        al_counts: dict[str, int] = {}
        for role_name, values in self._album_rows:
            if role_name in album_active:
                _append_row(self.albums_table, values)
                al_counts[role_name] = al_counts.get(role_name, 0) + 1
        total_al = sum(al_counts.values())
        self._albums_title.setText(
            "<b>Album Credits</b> — " + _role_summary(al_counts, total_al)
        )
        self._album_filter_bar.update_counts(al_counts)

        # Tracks
        self.tracks_table.setRowCount(0)
        tr_counts: dict[str, int] = {}
        for role_name, values in self._track_rows:
            if role_name in track_active:
                _append_row(self.tracks_table, values)
                tr_counts[role_name] = tr_counts.get(role_name, 0) + 1
        total_tr = sum(tr_counts.values())
        self._tracks_title.setText(
            "<b>Track Credits</b> — " + _role_summary(tr_counts, total_tr)
        )
        self._track_filter_bar.update_counts(tr_counts)


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
# Helpers
# ══════════════════════════════════════════════════════════════════════════════


def _primary_artist_name(album) -> str:
    """Return the Album Artist name for an album, or '' if none."""
    if album is None:
        return ""
    for assoc in getattr(album, "artist_roles", []):
        if assoc.role and getattr(assoc.role, "role_name", "") == "Album Artist":
            return assoc.artist.artist_name if assoc.artist else ""
    return ""


def _role_summary(counts: dict[str, int], total: int) -> str:
    """'12 total  (Producer ×4, Mixer ×3, …)'"""
    if not counts:
        return "0 total"
    parts = ", ".join(f"{role} ×{n}" for role, n in sorted(counts.items()))
    return f"{total} total  ({parts})"
