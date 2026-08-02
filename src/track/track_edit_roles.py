# ---------------------------------------------------------------------------
# RolesTab — artist / role relationships
# ---------------------------------------------------------------------------
from __future__ import annotations

from PySide6.QtCore import QObject, QPoint, QRect, QSize, Qt, QThread, Signal
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLayout,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from src.common.credited_as_dialog import CreditedAsDialog
from src.common.entity_completer_edit import (
    build_entity_search_widget,
    register_cached_entity,
)
from src.core.logger_config import logger
from src.core.status_utility import show_status_message
from src.db.db_tables import Artist, ArtistAlias, Role, TrackArtistRole
from src.track.track_edit_basetab import _BaseTab

PRIMARY_ARTIST_ROLE = "Primary Artist"


def _build_artist_index(artists) -> dict:
    """display_name -> artist_id, with automatic disambiguation for
    duplicate names (appends artist_id when two artists share a name).
    Mirrors artist_edit_influences.py's _build_artist_index.
    """
    index: dict = {}
    seen_names: dict = {}
    for a in artists:
        display = a.artist_name
        if not display:
            continue
        aid = a.artist_id
        if display in seen_names and seen_names[display] != aid:
            index.pop(display, None)
            index[f"{display} #{seen_names[display]}"] = seen_names[display]
            display = f"{display} #{aid}"
        seen_names[display] = aid
        index[display] = aid
    return index


def _fetch_role_rows(session, track_ids: list):
    """Bulk-fetch every (track, artist, role) triple for `track_ids` in a
    single joined query, instead of walking `track.artist_roles` and the
    `.artist`/`.role` relationships per row -- that lazy-loads one query at
    a time and is what made the Roles tab hitch on open."""
    stmt = (
        select(
            TrackArtistRole.track_id,
            TrackArtistRole.artist_id,
            TrackArtistRole.role_id,
            TrackArtistRole.credited_alias_id,
            Role.role_name,
            Artist.artist_name,
            ArtistAlias.alias_name,
        )
        .outerjoin(Artist, TrackArtistRole.artist_id == Artist.artist_id)
        .outerjoin(Role, TrackArtistRole.role_id == Role.role_id)
        .outerjoin(
            ArtistAlias, TrackArtistRole.credited_alias_id == ArtistAlias.alias_id
        )
        .where(TrackArtistRole.track_id.in_(track_ids))
    )
    return session.execute(stmt).all()


def _group_role_rows(rows, track_ids: list, is_multi: bool) -> dict:
    """Turn flat (track_id, artist_id, role_id, ...) rows into the
    (artist_id, credited_name) -> {"roles": {...}} shape the table renders.

    Mirrors the two grouping strategies the tab always had: for a single
    track every row becomes its own group; for multiple tracks only roles
    common to *every* selected track survive (an intersection of per-track
    sets), same as before.
    """
    grouped: dict[tuple, dict] = {}

    if is_multi:
        by_track: dict = {}
        for (
            track_id,
            artist_id,
            role_id,
            _alias_id,
            role_name,
            artist_name,
            alias_name,
        ) in rows:
            credited_name = alias_name or artist_name or "?"
            role_name = role_name or "?"
            by_track.setdefault(track_id, set()).add((
                artist_id,
                role_id,
                credited_name,
                role_name,
            ))

        all_sets = [by_track.get(tid, set()) for tid in track_ids]
        common = all_sets[0] if all_sets else set()
        for s in all_sets[1:]:
            common &= s

        for artist_id, role_id, credited_name, role_name in common:
            entry = grouped.setdefault((artist_id, credited_name), {"roles": {}})
            entry["roles"][role_id] = role_name
    else:
        for (
            track_id,
            artist_id,
            role_id,
            alias_id,
            role_name,
            artist_name,
            alias_name,
        ) in rows:
            credited_name = alias_name or artist_name or "?"
            role_name = role_name or "?"
            entry = grouped.setdefault(
                (artist_id, credited_name),
                {"roles": {}, "credited_alias_id": alias_id},
            )
            entry["roles"][role_id] = role_name

    return grouped


class _RolesLoaderWorker(QObject):
    """Runs on a background thread: fetches and groups the artist/role data
    with a single bulk query, then emits the result back to the main thread."""

    # Payload: grouped dict, as returned by _group_role_rows.
    finished = Signal(object)
    error = Signal(str)

    def __init__(self, controller, track_ids: list, is_multi: bool):
        super().__init__()
        self.controller = controller
        self._track_ids = track_ids
        self._is_multi = is_multi

    def run(self):
        try:
            rows = _fetch_role_rows(self.controller.get.session, self._track_ids)
            grouped = _group_role_rows(rows, self._track_ids, self._is_multi)
            self.finished.emit(grouped)
        except Exception as e:  # ruff: ignore[blind-except]
            # Intentional broad boundary catch: this runs on a background thread
            # and must not let an exception be lost silently.
            logger.exception("Failed to load track roles")
            self.error.emit(str(e))


class _FlowLayout(QLayout):
    """Lays out child widgets left-to-right, wrapping to a new line when the
    current line runs out of horizontal space.

    Used for the role-chip cell so an artist with many roles expands the
    row vertically to fit every chip, instead of squeezing/truncating them
    onto a single line.
    """

    def __init__(
        self, parent=None, margin: int = 0, h_spacing: int = 6, v_spacing: int = 4
    ):
        super().__init__(parent)
        self._h_spacing = h_spacing
        self._v_spacing = v_spacing
        self._items: list = []
        self.setContentsMargins(margin, margin, margin, margin)

    def __del__(self):
        while self.count():
            self.takeAt(0)

    def addItem(self, item):
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int):
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index: int):
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def expandingDirections(self):
        return Qt.Orientations(Qt.Orientation(0))

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._do_layout(QRect(0, 0, width, 0), test_only=True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do_layout(rect, test_only=False)

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

    def _do_layout(self, rect, test_only: bool) -> int:
        left, top, right, bottom = self.getContentsMargins()
        effective_rect = rect.adjusted(left, top, -right, -bottom)
        x = effective_rect.x()
        y = effective_rect.y()
        line_height = 0

        for item in self._items:
            hint = item.sizeHint()
            next_x = x + hint.width() + self._h_spacing

            # Wrap to a new line once the current one is full, unless this
            # is the first item on the line (nothing left to shrink).
            if next_x - self._h_spacing > effective_rect.right() and line_height > 0:
                x = effective_rect.x()
                y = y + line_height + self._v_spacing
                next_x = x + hint.width() + self._h_spacing
                line_height = 0

            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), hint))

            x = next_x
            line_height = max(line_height, hint.height())

        return y + line_height - rect.y() + bottom


class _RolesTable(QTableWidget):
    """QTableWidget that keeps row heights matched to wrapped chip content.

    Column 1 stretches to fill available width, so how many chip lines fit
    per row changes whenever the widget is resized. Qt doesn't recompute
    row heights on its own when a stretched column's width changes, so we
    do it explicitly here.

    QAbstractScrollArea (QTableWidget's base) defaults to an Expanding
    vertical size policy, so this table would otherwise inflate to fill
    whatever leftover space its parent layout hands it -- with only one or
    two artist rows (a single-artist track, or an album's Track Credits
    tab), that left a mostly-empty grid stretching down the page. Sizing to
    content instead (capped so a long artist list still scrolls rather than
    pushing the dialog taller) keeps the table only as tall as its rows.
    """

    _MAX_CONTENT_HEIGHT = 260

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.resizeRowsToContents()
        self.updateGeometry()

    def sizeHint(self):
        header = self.horizontalHeader()
        header_h = 0 if header.isHidden() else header.height()
        rows_h = sum(self.rowHeight(r) for r in range(self.rowCount()))
        frame = 2 * self.frameWidth()
        content_h = header_h + rows_h + frame
        return QSize(super().sizeHint().width(), min(content_h, self._MAX_CONTENT_HEIGHT))


class RolesTab(_BaseTab):
    def __init__(self, tracks: list, controller, parent=None, on_convert_to_album=None):
        super().__init__(tracks, controller, parent)
        self._loader_thread: QThread | None = None
        self._worker: _RolesLoaderWorker | None = None
        # Optional hook(artist_id, role_id): when set, a "→ Album" button is
        # shown next to every role chip, letting the album editor's Track
        # Credits tab convert a credit that's on every track into a single
        # album-level credit. Only meaningful when `tracks` is a whole
        # album's tracks (see base_album_edit_tabs.TrackCreditsTab) -- the
        # regular per-track/multi-track editor never passes this.
        self._on_convert_to_album = on_convert_to_album
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        # ── Search row ────────────────────────────────────────────────────
        search_row = QHBoxLayout()
        search_row.setSpacing(8)

        self._artist_search = build_entity_search_widget(
            self.controller,
            "Artist",
            "artist_name",
            "artist_id",
            "Search artists…",
            index_builder=_build_artist_index,
        )
        self._artist_search.textChanged.connect(self._update_add_btn)
        search_row.addWidget(self._artist_search)

        self._role_edit = build_entity_search_widget(
            self.controller,
            "Role",
            "role_name",
            "role_id",
            "Role (e.g. Performer, Composer…)",
        )
        self._role_edit.textChanged.connect(self._update_add_btn)
        search_row.addWidget(self._role_edit)

        self._add_btn = QPushButton("Add Role")
        self._add_btn.setEnabled(False)
        self._add_btn.clicked.connect(self._add_role)
        search_row.addWidget(self._add_btn)
        layout.addLayout(search_row)

        # ── Current roles table ───────────────────────────────────────────
        # One row per artist. Roles for that artist are listed in column 1
        # as removable chips; "Add role…" lets you append another role to
        # the same artist without re-searching for them.
        self._table = _RolesTable(0, 3)
        self._table.setHorizontalHeaderLabels(["Artist", "Roles", ""])
        self._table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeToContents
        )
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeToContents
        )
        self._table.horizontalHeader().sectionResized.connect(
            lambda *_args: self._table.resizeRowsToContents()
        )
        self._table.setWordWrap(True)
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(36)
        self._table.setToolTip(
            "Each artist gets one row; their roles are shown as chips with "
            "individual remove (×) buttons."
        )
        layout.addWidget(self._table)
        # Preferred vertical policy lets the table grow, so without a
        # stretch here a QVBoxLayout with no other expanding widget hands
        # it any leftover space instead of collapsing to _RolesTable's
        # content-based sizeHint.
        layout.addStretch(1)

    # ── Loading ───────────────────────────────────────────────────────────

    def load(self, tracks: list) -> None:
        """Kick off a background load: fetching artist/role rows previously
        walked `track.artist_roles` and `.artist`/`.role` per row, which
        lazy-loads one DB query at a time and hitched the UI thread on open.
        The table is (re)populated in `_on_roles_loaded` once the bulk query
        finishes on a worker thread."""
        self.tracks = tracks

        try:
            if self._loader_thread and self._loader_thread.isRunning():
                return
        except RuntimeError:
            self._loader_thread = None

        self._table.setEnabled(False)

        track_ids = [t.track_id for t in tracks]
        self._loader_thread = QThread(self)
        self._worker = _RolesLoaderWorker(self.controller, track_ids, self.is_multi)
        self._worker.moveToThread(self._loader_thread)

        self._loader_thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._on_roles_loaded)
        self._worker.error.connect(self._on_roles_load_error)
        self._worker.finished.connect(self._loader_thread.quit)
        self._worker.error.connect(self._loader_thread.quit)
        self._loader_thread.finished.connect(self._loader_thread.deleteLater)

        self._loader_thread.start()

    def _on_roles_loaded(self, grouped: dict) -> None:
        """Called on the main thread once the background worker finishes."""
        self._table.setRowCount(0)

        for (artist_id, credited_name), entry in self._sorted_groups(grouped):
            self._add_artist_row(
                artist_id,
                credited_name,
                entry["roles"],
                credited_alias_id=entry.get("credited_alias_id"),
            )

        # ResizeToContents tracks each insertRow individually, so a column
        # can be left sized from an earlier, narrower row in the batch —
        # force both content columns to re-fit their widest cell now that
        # every row is in, so e.g. "Credit as…" never renders clipped.
        self._table.resizeColumnToContents(0)
        self._table.resizeColumnToContents(2)
        self._table.setEnabled(True)

        # Row count/heights just changed, which changes the table's content
        # based sizeHint (see _RolesTable) -- tell the layout to re-query it
        # rather than leaving the table sized for whatever it hinted before.
        self._table.updateGeometry()

    def _on_roles_load_error(self, message: str) -> None:
        logger.error(f"Error loading artist roles: {message}")
        self._table.setEnabled(True)

    def cleanup(self) -> None:
        """Stop any in-flight background load before the tab is destroyed,
        so the worker's finished/error signals never land on a deleted
        widget."""
        try:
            if self._loader_thread and self._loader_thread.isRunning():
                self._worker.finished.disconnect(self._on_roles_loaded)
                self._worker.error.disconnect(self._on_roles_load_error)
                self._loader_thread.quit()
                self._loader_thread.wait(3000)
        except RuntimeError:
            pass

    @staticmethod
    def _sorted_groups(grouped: dict) -> list:
        """Sort artists, putting anyone with a Primary Artist role first,
        then alphabetically by credited name."""

        def sort_key(item):
            (_artist_id, credited_name), entry = item
            has_primary = PRIMARY_ARTIST_ROLE in entry["roles"].values()
            return (0 if has_primary else 1, credited_name.lower())

        return sorted(grouped.items(), key=sort_key)

    @staticmethod
    def _sorted_roles(roles: dict[int | None, str]) -> list[tuple]:
        """Sort an artist's roles, Primary Artist first, then alphabetical."""

        def sort_key(item):
            _role_id, role_name = item
            return (0 if role_name == PRIMARY_ARTIST_ROLE else 1, role_name.lower())

        return sorted(roles.items(), key=sort_key)

    def _add_artist_row(
        self, artist_id, artist_name, roles: dict, credited_alias_id=None
    ):
        row = self._table.rowCount()
        self._table.insertRow(row)

        artist_item = QTableWidgetItem(artist_name)
        artist_item.setData(Qt.UserRole, artist_id)
        artist_item.setFlags(artist_item.flags() & ~Qt.ItemIsEditable)
        self._table.setItem(row, 0, artist_item)

        roles_widget = self._build_roles_cell(artist_id, artist_name, roles)
        self._table.setCellWidget(row, 1, roles_widget)

        actions_widget = QWidget()
        actions_layout = QHBoxLayout(actions_widget)
        actions_layout.setContentsMargins(6, 4, 6, 4)
        actions_layout.setSpacing(8)

        # A single credited name is only unambiguous when editing one track
        # at a time -- with multiple tracks selected, different tracks may
        # already have different credited names for the same artist/role.
        if not self.is_multi:
            credit_btn = QPushButton("Credit as…")
            credit_btn.setToolTip(f"Choose which name to credit {artist_name} as")
            credit_btn.clicked.connect(
                lambda _checked, aid=artist_id, r=dict(roles), cur=credited_alias_id: (
                    self._change_credited_alias(aid, r, cur)
                )
            )
            actions_layout.addWidget(credit_btn)

        remove_artist_btn = QPushButton("Remove All")
        remove_artist_btn.setToolTip(f"Remove every role for {artist_name}")
        remove_artist_btn.clicked.connect(
            lambda _checked, aid=artist_id: self._remove_all_roles_for_artist(aid)
        )
        actions_layout.addWidget(remove_artist_btn)

        self._table.setCellWidget(row, 2, actions_widget)

        self._table.resizeRowToContents(row)

    def _build_roles_cell(self, artist_id, artist_name, roles: dict) -> QWidget:
        cell = QWidget()
        row_layout = _FlowLayout(cell, margin=4, h_spacing=6, v_spacing=4)

        for role_id, role_name in self._sorted_roles(roles):
            chip = QPushButton(f"{role_name}  ×")
            chip.setFlat(True)
            chip.setStyleSheet(
                "QPushButton {"
                "  border: 1px solid #888;"
                "  border-radius: 10px;"
                "  padding: 2px 8px;"
                "}"
                "QPushButton:hover { background-color: #444; }"
            )
            chip.setToolTip(f"Remove '{role_name}' from {artist_name}")
            chip.clicked.connect(
                lambda _checked, aid=artist_id, rid=role_id: self._remove_role(aid, rid)
            )
            row_layout.addWidget(chip)

            if self._on_convert_to_album is not None:
                to_album_btn = QPushButton("→ Album")
                to_album_btn.setFlat(True)
                to_album_btn.setStyleSheet("font-size: 10px; padding: 1px 6px;")
                to_album_btn.setToolTip(
                    f"Make '{role_name}' for {artist_name} a single album-level "
                    f"credit instead (removes it from every track)"
                )
                to_album_btn.clicked.connect(
                    lambda _checked, aid=artist_id, rid=role_id: (
                        self._on_convert_to_album(aid, rid)
                    )
                )
                row_layout.addWidget(to_album_btn)

        add_role_btn = QPushButton("+ Add role…")
        add_role_btn.setFlat(True)
        add_role_btn.clicked.connect(
            lambda _checked, aid=artist_id, name=artist_name: (
                self._prompt_add_role_for_artist(aid, name)
            )
        )
        row_layout.addWidget(add_role_btn)
        return cell

    # ── Search ────────────────────────────────────────────────────────────

    def _update_add_btn(self):
        artist_ok = len(self._artist_search.text().strip()) >= 2
        role_ok = len(self._role_edit.text().strip()) >= 2
        self._add_btn.setEnabled(artist_ok and role_ok)

    # ── Resolution helpers ────────────────────────────────────────────────

    def _resolve_artist(self, artist_name: str):
        matched_id = self._artist_search.matched_id()
        if matched_id is not None:
            return self.controller.get.get_entity_object("Artist", artist_id=matched_id)

        existing = self.controller.get.get_entity_object(
            "Artist", artist_name=artist_name
        )
        artist = existing[0] if isinstance(existing, list) else existing
        if not artist:
            artist = self.controller.add.add_entity("Artist", artist_name=artist_name)

        if artist:
            self._artist_search.add_to_index(artist.artist_name, artist.artist_id)
            register_cached_entity("Artist", artist)
        return artist

    def _resolve_role(self, role_name: str, matched_id=None):
        if matched_id is not None:
            return self.controller.get.get_entity_object("Role", role_id=matched_id)

        existing_role = self.controller.get.get_entity_object(
            "Role", role_name=role_name
        )
        role = (
            existing_role
            if not isinstance(existing_role, list)
            else (existing_role[0] if existing_role else None)
        )
        if not role:
            role = self.controller.add.add_entity("Role", role_name=role_name)
            register_cached_entity("Role", role)

        if role:
            self._role_edit.add_to_index(role.role_name, role.role_id)
        return role

    # ── Add / Remove ──────────────────────────────────────────────────────

    def _add_role(self):
        artist_name = self._artist_search.text().strip()
        role_name = self._role_edit.text().strip()
        if not artist_name or not role_name:
            return

        artist = self._resolve_artist(artist_name)
        role = self._resolve_role(role_name, matched_id=self._role_edit.matched_id())

        if not artist or not role:
            QMessageBox.warning(self, "Error", "Could not resolve artist or role.")
            return

        credited_alias_id = None
        if not self.is_multi:
            credited_alias_id = self._prompt_credited_alias(artist)

        self._batch_add_track_artist_role(
            artist.artist_id, role.role_id, credited_alias_id=credited_alias_id
        )

        self._artist_search.reset()
        self._role_edit.reset()
        self.load(self.tracks)

    def _prompt_add_role_for_artist(self, artist_id, artist_name):
        """Add another role to an artist already shown in the table, via
        the existing role input field (reused as a quick prompt)."""
        role_name = self._role_edit.text().strip()
        if len(role_name) < 2:
            show_status_message(
                self,
                f"Type a role name (min 2 chars) in the role field, then click "
                f"'+ Add role…' next to {artist_name}.",
            )
            self._role_edit.setFocus()
            return

        role = self._resolve_role(role_name, matched_id=self._role_edit.matched_id())
        if not role:
            QMessageBox.warning(self, "Error", "Could not resolve role.")
            return

        credited_alias_id = None
        if not self.is_multi:
            artist = self.controller.get.get_entity_object(
                "Artist", artist_id=artist_id
            )
            if artist:
                credited_alias_id = self._prompt_credited_alias(artist)

        self._batch_add_track_artist_role(
            artist_id, role.role_id, credited_alias_id=credited_alias_id
        )
        self._role_edit.reset()
        self.load(self.tracks)

    def _prompt_credited_alias(self, artist, current_alias_id=None):
        """Show the alias picker for `artist` if they have any aliases on
        file; return the chosen alias_id, or `current_alias_id` unchanged
        if they have none or the user cancels."""
        aliases = self.controller.get.get_all_entities(
            "ArtistAlias", artist_id=artist.artist_id
        )
        if not aliases:
            return current_alias_id

        dialog = CreditedAsDialog(
            artist, aliases, current_alias_id=current_alias_id, parent=self
        )
        if dialog.exec() == QDialog.Accepted:
            return dialog.selected_alias_id()
        return current_alias_id

    def _change_credited_alias(self, artist_id, roles: dict, current_alias_id):
        """Change the credited name for every role this artist currently
        holds on the single selected track."""
        artist = self.controller.get.get_entity_object("Artist", artist_id=artist_id)
        if not artist:
            return

        new_alias_id = self._prompt_credited_alias(
            artist, current_alias_id=current_alias_id
        )
        if new_alias_id == current_alias_id:
            return

        for role_id in roles:
            self.controller.update.update_entity_by_filter(
                "TrackArtistRole",
                {
                    "track_id": self.track.track_id,
                    "artist_id": artist_id,
                    "role_id": role_id,
                },
                credited_alias_id=new_alias_id,
            )
        self.load(self.tracks)

    def _remove_role(self, artist_id, role_id):
        self._batch_delete_track_artist_role(artist_id, role_id)
        self.load(self.tracks)

    def _remove_all_roles_for_artist(self, artist_id):
        # Gather every role_id this artist currently holds across the
        # selected tracks, then remove each.
        role_ids: set = set()
        for t in self.tracks:
            for ra in t.artist_roles:
                if ra.artist and ra.artist.artist_id == artist_id and ra.role:
                    role_ids.add(ra.role.role_id)

        for role_id in role_ids:
            self._batch_delete_track_artist_role(artist_id, role_id)

        self.load(self.tracks)

    # ── Batch helpers ─────────────────────────────────────────────────────
    #
    # Both calls below issue a single DB statement covering every selected
    # track (a bulk INSERT and a single DELETE ... WHERE track_id IN (...)
    # respectively), instead of one round-trip per track.

    def _batch_add_track_artist_role(self, artist_id, role_id, credited_alias_id=None):
        rows = [
            {
                "track_id": track.track_id,
                "artist_id": artist_id,
                "role_id": role_id,
                "credited_alias_id": credited_alias_id,
            }
            for track in self.tracks
        ]
        try:
            self.controller.add.add_entities("TrackArtistRole", rows)
        except SQLAlchemyError as e:
            logger.error(f"Failed to add role to tracks: {e}")

    def _batch_delete_track_artist_role(self, artist_id, role_id):
        track_ids = [track.track_id for track in self.tracks]
        try:
            self.controller.delete.delete_entity(
                "TrackArtistRole",
                track_id=track_ids,
                artist_id=artist_id,
                role_id=role_id,
            )
        except SQLAlchemyError as e:
            logger.error(f"Failed to remove role from tracks: {e}")
