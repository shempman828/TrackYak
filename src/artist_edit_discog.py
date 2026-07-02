# ══════════════════════════════════════════════════════════════════════════════
# Tab: Discography
# ══════════════════════════════════════════════════════════════════════════════

from PySide6.QtCore import QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLayout,
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

    Albums are shown as a compact flow of chips (name / year / album artist)
    with the full role list on hover, since a table row-per-role was noisy
    and album counts are usually small. Clicking a chip drills the track
    table down to that album; clicking it again (or the "show all" link)
    clears the drill-down. Track credits stay in a table since there are
    typically many more of them, and the panel gets the larger share of
    the splitter.
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

        self._album_flow = _AlbumFlowArea()
        self._album_flow.album_clicked.connect(self._on_album_clicked)
        al_layout.addWidget(self._album_flow)
        splitter.addWidget(albums_widget)

        # ── Tracks panel ──────────────────────────────────────────────────
        tracks_widget = QWidget()
        tr_layout = QVBoxLayout(tracks_widget)
        tr_layout.setContentsMargins(0, 0, 0, 0)
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
        splitter.addWidget(tracks_widget)

        # Tracks generally outnumber albums considerably; give that panel
        # the larger share of space instead of a flat 50/50 split.
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([180, 420])

        root.addWidget(splitter)

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
                    "name": assoc.album.album_name,
                    "year": assoc.album.release_year or "",
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
            key=lambda g: (
                -(g["year"] or 0) if isinstance(g["year"], int) else 0,
                g["name"] or "",
            ),
        )
        self._album_flow.set_albums(album_list)

        self._apply_filters()

    # ------------------------------------------------------------------ Interaction

    def _on_album_clicked(self, album_id):
        self._selected_album_id = (
            None if self._selected_album_id == album_id else album_id
        )
        self._album_flow.set_selected(self._selected_album_id)
        self._apply_filters()

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
# Helper: wrapping "flow" layout (Qt has no built-in equivalent)
# ══════════════════════════════════════════════════════════════════════════════


class FlowLayout(QLayout):
    def __init__(self, parent=None, margin=0, spacing=-1):
        super().__init__(parent)
        if parent is not None:
            self.setContentsMargins(margin, margin, margin, margin)
        self.setSpacing(spacing)
        self._items: list = []

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index):
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def expandingDirections(self):
        return Qt.Orientations(Qt.Orientation(0))

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._do_layout(QRect(0, 0, width, 0), True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do_layout(rect, False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        size += QSize(
            margins.left() + margins.right(), margins.top() + margins.bottom()
        )
        return size

    def _do_layout(self, rect, test_only):
        left, top, right, bottom = self.getContentsMargins()
        effective_rect = rect.adjusted(left, top, -right, -bottom)
        x = effective_rect.x()
        y = effective_rect.y()
        line_height = 0
        spacing = self.spacing()

        for item in self._items:
            next_x = x + item.sizeHint().width() + spacing
            if next_x - spacing > effective_rect.right() and line_height > 0:
                x = effective_rect.x()
                y = y + line_height + spacing
                next_x = x + item.sizeHint().width() + spacing
                line_height = 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), item.sizeHint()))
            x = next_x
            line_height = max(line_height, item.sizeHint().height())

        return y + line_height - rect.y() + bottom


# ══════════════════════════════════════════════════════════════════════════════
# Helper: album chip + flow area
# ══════════════════════════════════════════════════════════════════════════════


class _AlbumChip(QFrame):
    """Compact album card. Hover shows the full role list; click drills the
    track table down to this album (click again to clear)."""

    clicked = Signal(object)  # album_id

    def __init__(self, album_id, name, year, album_artist, roles, parent=None):
        super().__init__(parent)
        self.album_id = album_id
        self.roles = set(roles)
        self._selected = False

        self.setObjectName("albumChip")
        self.setCursor(QCursor(Qt.PointingHandCursor))
        self.setFrameShape(QFrame.StyledPanel)
        self.setMaximumWidth(220)
        self.setToolTip(", ".join(sorted(self.roles)))

        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 6)
        lay.setSpacing(1)

        title = QLabel(name or "Untitled")
        title.setObjectName("albumChipTitle")
        title.setWordWrap(True)
        title.setStyleSheet("font-weight: 600;")
        lay.addWidget(title)

        sub_bits = [str(year)] if year else []
        if album_artist:
            sub_bits.append(album_artist)
        sub = QLabel(" \u00b7 ".join(sub_bits))
        sub.setObjectName("albumChipSub")
        sub.setWordWrap(True)
        lay.addWidget(sub)

        self._apply_style()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self.album_id)
        super().mousePressEvent(event)

    def set_selected(self, selected: bool):
        if selected == self._selected:
            return
        self._selected = selected
        self._apply_style()

    def matches_roles(self, active_roles: set) -> bool:
        return bool(self.roles & active_roles)

    def _apply_style(self):
        if self._selected:
            self.setStyleSheet(
                "#albumChip { background: palette(highlight); border: 1px solid palette(highlight); border-radius: 6px; }"
                "#albumChipTitle, #albumChipSub { color: palette(highlighted-text); }"
                "#albumChipSub { font-size: 11px; }"
            )
        else:
            self.setStyleSheet(
                "#albumChip { background: palette(base); border: 1px solid palette(mid); border-radius: 6px; }"
                "#albumChipSub { color: palette(mid); font-size: 11px; }"
            )


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
                a["id"], a["name"], a["year"], a["album_artist"], a["roles"]
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
