# ══════════════════════════════════════════════════════════════════════════════
# Tab: Discography
# ══════════════════════════════════════════════════════════════════════════════

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QGroupBox,
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)


def _make_table(headers, editable=True):
    """Create a standard QTableWidget with consistent styling."""
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
    """Read-only summary of album and track credits."""

    def __init__(self, controller, artist, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.artist = artist
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        note = QLabel(
            "<i>Album credits where this artist is the primary Album Artist are shown here. "
            '"Primary Artist" track credits for those same albums are hidden to avoid redundancy.</i>'
        )
        note.setWordWrap(True)
        layout.addWidget(note)

        albums_grp = QGroupBox("Album Credits (non-redundant roles)")
        al_layout = QVBoxLayout(albums_grp)
        self.albums_table = _make_table(["Album", "Role", "Year"], editable=False)
        al_layout.addWidget(self.albums_table)
        layout.addWidget(albums_grp)

        tracks_grp = QGroupBox("Track Credits")
        tr_layout = QVBoxLayout(tracks_grp)
        self.tracks_table = _make_table(["Track", "Role", "Album"], editable=False)
        tr_layout.addWidget(self.tracks_table)
        layout.addWidget(tracks_grp)

    def load(self, artist):
        self.artist = artist
        self.albums_table.setRowCount(0)

        # Collect album IDs where this artist is Album Artist (skip redundant Primary Artist rows)
        album_artist_ids = {
            assoc.album_id
            for assoc in getattr(artist, "album_roles", [])
            if assoc.role and getattr(assoc.role, "role_name", "") == "Album Artist"
        }

        for assoc in getattr(artist, "album_roles", []):
            if assoc.album is None:
                continue
            role_name = assoc.role.role_name if assoc.role else ""
            if role_name == "Primary Artist" and assoc.album_id in album_artist_ids:
                continue
            _append_row(
                self.albums_table,
                [assoc.album.album_name, role_name, assoc.album.release_year or ""],
            )

        self.tracks_table.setRowCount(0)
        for assoc in getattr(artist, "track_roles", []):
            if assoc.track is None:
                continue
            album_name = assoc.track.album.album_name if assoc.track.album else ""
            _append_row(
                self.tracks_table,
                [
                    assoc.track.track_name,
                    assoc.role.role_name if assoc.role else "",
                    album_name,
                ],
            )
