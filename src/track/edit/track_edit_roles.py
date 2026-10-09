# ---------------------------------------------------------------------------
# RolesTab — artist / role relationships
# ---------------------------------------------------------------------------
from __future__ import annotations

from PySide6.QtCore import QObject, QPoint, QRect, QSize, Qt, QThread, QTimer, Signal
from PySide6.QtWidgets import QAbstractItemView, QDialog, QHBoxLayout, QHeaderView, QLabel, QLayout, QMessageBox, QPushButton, QSizePolicy, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget
from sqlalchemy import select

from src.artist.artist_resolution import resolve_or_create_artist
from src.common.dialogs.credited_as_dialog import CreditedAsDialog
from src.common.widgets.entity_completer_context import artist_context_map
from src.common.widgets.entity_completer_edit import build_entity_search_widget, register_cached_entity
from src.db.db_tables import Artist, ArtistAlias, Role, TrackArtistRole
from src.foundation.logger_config import logger
from src.foundation.status_utility import show_status_message
from src.track.edit.track_edit_basetab import _BaseTab

PRIMARY_ARTIST_ROLE = "Primary Artist"


def _build_artist_index(artists) -> dict:
    """Return display_name -> artist_id, adding ' #id' to names that two artists share."""
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
    """Fetch every (track, artist, role) row for `track_ids` in one joined query."""
    stmt = (
        select(TrackArtistRole.track_id, TrackArtistRole.artist_id, TrackArtistRole.role_id, TrackArtistRole.credited_alias_id, Role.role_name, Artist.artist_name, ArtistAlias.alias_name)
        .outerjoin(Artist, TrackArtistRole.artist_id == Artist.artist_id)
        .outerjoin(Role, TrackArtistRole.role_id == Role.role_id)
        .outerjoin(ArtistAlias, TrackArtistRole.credited_alias_id == ArtistAlias.alias_id)
        .where(TrackArtistRole.track_id.in_(track_ids))
    )
    return session.execute(stmt).all()


def _group_role_rows(rows, track_ids: list, is_multi: bool) -> dict:
    """Group role rows by (artist_id, credited_name); in multi mode keep only roles common to every track."""
    grouped: dict[tuple, dict] = {}

    if is_multi:
        by_track: dict = {}
        for track_id, artist_id, role_id, _alias_id, role_name, artist_name, alias_name in rows:
            credited_name = alias_name or artist_name or "?"
            role_name = role_name or "?"
            by_track.setdefault(track_id, set()).add((artist_id, role_id, credited_name, role_name))

        all_sets = [by_track.get(tid, set()) for tid in track_ids]
        common = all_sets[0] if all_sets else set()
        for s in all_sets[1:]:
            common &= s

        for artist_id, role_id, credited_name, role_name in common:
            entry = grouped.setdefault((artist_id, credited_name), {"roles": {}})
            entry["roles"][role_id] = role_name
    else:
        for _track_id, artist_id, role_id, alias_id, role_name, artist_name, alias_name in rows:
            credited_name = alias_name or artist_name or "?"
            role_name = role_name or "?"
            entry = grouped.setdefault((artist_id, credited_name), {"roles": {}, "credited_alias_id": alias_id})
            entry["roles"][role_id] = role_name

    return grouped


class _RolesLoaderWorker(QObject):
    """Fetch and group the artist/role rows on a background thread."""

    # Payload: grouped dict, as returned by _group_role_rows.
    finished = Signal(object)
    error = Signal(str)

    def __init__(self, controller, track_ids: list, is_multi: bool):
        super().__init__()
        self.controller = controller
        self._track_ids = track_ids
        self._is_multi = is_multi

    def run(self):
        """Fetch and group the rows, then emit finished (or error)."""
        try:
            rows = _fetch_role_rows(self.controller.get.session, self._track_ids)
            grouped = _group_role_rows(rows, self._track_ids, self._is_multi)
            self.finished.emit(grouped)
        except Exception as e:
            # Intentional broad boundary catch: this runs on a background thread
            # and must not let an exception be lost silently.
            logger.exception("Failed to load track roles")
            self.error.emit(str(e))
        finally:
            # Each load uses a new thread with its own scoped session; release it or the pool runs dry.
            self.controller.SessionFactory.remove()


class _FlowLayout(QLayout):
    """Lay out child widgets left to right, wrapping to a new line when a line is full."""

    def __init__(self, parent=None, margin: int = 0, h_spacing: int = 6, v_spacing: int = 4):
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
        size += QSize(margins.left() + margins.right(), margins.top() + margins.bottom())
        return size

    def _do_layout(self, rect, test_only: bool) -> int:
        """Place the items in wrapped lines inside `rect`; return the height used."""
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


class _ChipCell(QWidget):
    """Table cell that holds a role-chip _FlowLayout and reports its height at its real width."""

    # QWidget.sizeHint() evaluates heightForWidth() at the layout's minimum width
    # (one chip), which makes rows far too tall; resizeRowsToContents() cannot fix that.

    def _hint(self) -> QSize:
        """Return the size hint at the cell's current width."""
        lay = self.layout()
        width = self.width()
        if lay is not None and lay.hasHeightForWidth() and width > 0:
            return QSize(width, lay.heightForWidth(width))
        return super().sizeHint()

    def sizeHint(self) -> QSize:
        return self._hint()

    def minimumSizeHint(self) -> QSize:
        return self._hint()

    def resizeEvent(self, event):
        # A stretched column changing width changes how many chip lines fit,
        # hence this cell's preferred height -- let Qt know the hint is stale.
        super().resizeEvent(event)
        self.updateGeometry()


class _RolesTable(QTableWidget):
    """Roles table whose height follows its wrapped rows, up to _MAX_CONTENT_HEIGHT."""

    # Qt does not recompute row heights when a stretched column changes width, so resizeEvent does.

    _MAX_CONTENT_HEIGHT = 260

    # Emitted whenever a resize or row-count change flips whether the table's
    # content exceeds _MAX_CONTENT_HEIGHT, so an owning layout can decide
    # whether the table should claim leftover vertical space instead of
    # leaving it blank below a capped table.
    content_changed = Signal()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.resizeRowsToContents()
        self.updateGeometry()
        self.content_changed.emit()

    def content_height(self) -> int:
        """Return the height of the header, all rows and the frame."""
        header = self.horizontalHeader()
        header_h = 0 if header.isHidden() else header.height()
        rows_h = sum(self.rowHeight(r) for r in range(self.rowCount()))
        frame = 2 * self.frameWidth()
        return header_h + rows_h + frame

    def is_overflowing(self) -> bool:
        """Return True if the rows are taller than the height cap."""
        return self.content_height() > self._MAX_CONTENT_HEIGHT

    def sizeHint(self):
        return QSize(super().sizeHint().width(), min(self.content_height(), self._MAX_CONTENT_HEIGHT))


class RolesTab(_BaseTab):
    """Add, remove and credit artist roles on the edited track(s)."""

    # Mirrors _RolesTable.is_overflowing(), so an owning layout can give this tab leftover space.
    overflow_changed = Signal(bool)
    saves_immediately = True  # add/remove write to the DB at once

    def __init__(self, tracks: list, controller, parent=None, on_convert_to_album=None):
        super().__init__(tracks, controller, parent)
        self._loader_thread: QThread | None = None
        self._worker: _RolesLoaderWorker | None = None
        self._reload_pending = False
        self._cleaned_up = False
        # Optional hook(artist_id, role_id) for the album editor's Track Credits tab:
        # shows a "→ Album" button on each chip to make the credit album-level.
        self._on_convert_to_album = on_convert_to_album
        self._build_ui()

    def _build_ui(self):
        """Build the artist/role search row and the roles table."""
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        # ── Search row ────────────────────────────────────────────────────
        search_row = QHBoxLayout()
        search_row.setSpacing(8)

        self._artist_search = build_entity_search_widget(
            self.controller, "Artist", "artist_name", "artist_id", "Search artists…", index_builder=_build_artist_index, context_builder=artist_context_map
        )
        self._artist_search.textChanged.connect(self._update_add_btn)
        search_row.addWidget(self._artist_search)

        self._role_edit = build_entity_search_widget(self.controller, "Role", "role_name", "role_id", "Role (e.g. Performer, Composer…)")
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
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self._table.horizontalHeader().sectionResized.connect(lambda *_args: self._table.resizeRowsToContents())
        self._table.setWordWrap(True)
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(36)
        # Rows wrap to fit their chips, so heights vary a lot row to row.
        # The default ScrollPerItem mode jumps a full (variable) row per
        # wheel notch, which feels jumpy and makes it hard to track a row
        # across a scroll -- ScrollPerPixel scrolls smoothly instead.
        self._table.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self._table.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        # ScrollPerPixel alone isn't enough: Qt still derives the vertical
        # scrollbar's wheel-notch step (singleStep) from the table's row
        # heights, so with these variable/wrapped rows a single notch could
        # still jump ~90px+ -- as tall as the tallest row on screen -- which
        # still feels like "scrolling by row." Pin it to a small fixed
        # pixel step instead so every notch moves the same modest amount
        # regardless of row height. Set once here rather than after each
        # reload: it isn't touched by setRowCount/resizeRowsToContents, and
        # this table is reused in place across reloads (see
        # AlbumEditor._refresh_track_credits_tab), not recreated.
        self._table.verticalScrollBar().setSingleStep(24)
        self._table.setToolTip("Each artist gets one row; their roles are shown as chips with individual remove (\u00d7) buttons.")
        layout.addWidget(self._table)
        # Preferred vertical policy lets the table grow, so without a
        # stretch here a QVBoxLayout with no other expanding widget hands
        # it any leftover space instead of collapsing to _RolesTable's
        # content-based sizeHint.
        self._table_stretch_idx = layout.count() - 1
        layout.addStretch(1)
        self._trailing_stretch_idx = layout.count() - 1
        self._layout = layout

        self._table.content_changed.connect(self._sync_table_stretch)
        self._sync_table_stretch()

    def _sync_table_stretch(self) -> None:
        """Give the table the tab's leftover height once its rows overflow the height cap."""
        overflow = self._table.is_overflowing()
        self._layout.setStretch(self._table_stretch_idx, 1 if overflow else 0)
        self._layout.setStretch(self._trailing_stretch_idx, 0 if overflow else 1)
        self.overflow_changed.emit(overflow)

    # ── Loading ───────────────────────────────────────────────────────────

    def load(self, tracks: list) -> None:
        """Start a background load of the artist/role rows of `tracks`."""
        self.tracks = tracks

        try:
            if self._loader_thread and self._loader_thread.isRunning():
                # Run again when the current load ends, so a reload after an edit is not lost.
                self._reload_pending = True
                return
        except RuntimeError:
            self._loader_thread = None
        self._reload_pending = False

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
        self._loader_thread.finished.connect(self._run_pending_reload)

        self._loader_thread.start()

    def _run_pending_reload(self) -> None:
        """Start the reload that was asked for while a load was running."""
        if self._reload_pending and not self._cleaned_up:
            self.load(self.tracks)

    def _on_roles_loaded(self, grouped: dict) -> None:
        """Fill the table from the grouped rows (main thread)."""
        saved_scroll = self._table.verticalScrollBar().value()
        self._table.setRowCount(0)

        for (artist_id, credited_name), entry in self._sorted_groups(grouped):
            self._add_artist_row(artist_id, credited_name, entry["roles"], credited_alias_id=entry.get("credited_alias_id"))

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
        self._sync_table_stretch()

        # The scrollbar range isn't recomputed until Qt processes the
        # geometry changes above, so defer the restore to the next event
        # loop pass -- otherwise setValue clamps to the stale (pre-rebuild)
        # range and silently resets to the top.
        # Chip cells only know their real (stretched-column) width once Qt has
        # laid the table out, and their height depends on it (see _ChipCell) --
        # re-fit every row on the next pass so rows that wrap don't stay sized
        # for the pre-layout, one-chip-wide hint.
        QTimer.singleShot(0, self._table.resizeRowsToContents)

        QTimer.singleShot(0, lambda: self._table.verticalScrollBar().setValue(saved_scroll))

    def _on_roles_load_error(self, message: str) -> None:
        """Log a load error and enable the table again."""
        logger.error(f"Error loading artist roles: {message}")
        self._table.setEnabled(True)

    def cleanup(self) -> None:
        """Disconnect and stop a running background load before the tab is destroyed."""
        self._cleaned_up = True
        self._reload_pending = False
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
        """Sort artists: Primary Artists first, then by credited name."""

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

    def _add_artist_row(self, artist_id, artist_name, roles: dict, credited_alias_id=None):
        """Append one artist row: name, role chips and actions."""
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
            credit_btn.clicked.connect(lambda _checked, aid=artist_id, r=dict(roles), cur=credited_alias_id: self._change_credited_alias(aid, r, cur))
            actions_layout.addWidget(credit_btn)

        remove_artist_btn = QPushButton("Remove All")
        remove_artist_btn.setToolTip(f"Remove every role for {artist_name}")
        remove_artist_btn.clicked.connect(lambda _checked, aid=artist_id: self._remove_all_roles_for_artist(aid))
        actions_layout.addWidget(remove_artist_btn)

        self._table.setCellWidget(row, 2, actions_widget)

        self._table.resizeRowToContents(row)

    def _build_role_chip(self, artist_id, artist_name, role_id, role_name) -> QWidget:
        """Return one removable role chip."""
        chip = QWidget()
        chip.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        chip.setProperty("class", "roleChip")

        chip_layout = QVBoxLayout(chip)
        chip_layout.setContentsMargins(8, 4, 8, 6)
        chip_layout.setSpacing(2)

        top_row = QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.setSpacing(2)

        label = QLabel(role_name)
        top_row.addWidget(label)

        close_btn = QPushButton("\u00d7")
        close_btn.setFlat(True)
        close_btn.setProperty("class", "roleChipClose")
        close_btn.setFixedSize(16, 16)
        close_btn.setToolTip(f"Remove '{role_name}' from {artist_name}")
        close_btn.clicked.connect(lambda _checked, aid=artist_id, rid=role_id: self._remove_role(aid, rid))
        top_row.addWidget(close_btn)

        chip_layout.addLayout(top_row)

        # Placed under the role's own label/close row (rather than beside it
        # in the flow layout) so it stays grouped with the role it acts on
        # even after the row wraps, and so it doesn't double the horizontal
        # space every chip takes up.
        if self._on_convert_to_album is not None:
            to_album_btn = QPushButton("\u2192 Album")
            to_album_btn.setFlat(True)
            to_album_btn.setObjectName("ToAlbumButton")
            to_album_btn.setToolTip(f"Make '{role_name}' for {artist_name} a single album-level credit instead (removes it from every track)")
            to_album_btn.clicked.connect(lambda _checked, aid=artist_id, rid=role_id: self._on_convert_to_album(aid, rid))
            chip_layout.addWidget(to_album_btn)

        return chip

    def _build_roles_cell(self, artist_id, artist_name, roles: dict) -> QWidget:
        """Return the wrapped chip cell for one artist's roles."""
        cell = _ChipCell()
        row_layout = _FlowLayout(cell, margin=4, h_spacing=6, v_spacing=4)

        for role_id, role_name in self._sorted_roles(roles):
            chip = self._build_role_chip(artist_id, artist_name, role_id, role_name)
            row_layout.addWidget(chip)

        add_role_btn = QPushButton("+ Add role…")
        add_role_btn.setFlat(True)
        add_role_btn.clicked.connect(lambda _checked, aid=artist_id, name=artist_name: self._prompt_add_role_for_artist(aid, name))
        row_layout.addWidget(add_role_btn)
        return cell

    # ── Search ────────────────────────────────────────────────────────────

    def _update_add_btn(self):
        """Enable "Add Role" when both fields have at least 2 characters."""
        artist_ok = len(self._artist_search.text().strip()) >= 2
        role_ok = len(self._role_edit.text().strip()) >= 2
        self._add_btn.setEnabled(artist_ok and role_ok)

    # ── Resolution helpers ────────────────────────────────────────────────

    def _resolve_artist(self, artist_name: str, matched_id=None):
        """Return the artist by completer pick or name, creating it if needed; None on failure."""
        if matched_id is not None:
            return self.controller.get.get_entity_object("Artist", artist_id=matched_id)

        artist = resolve_or_create_artist(self.controller, artist_name)

        if artist:
            self._artist_search.add_to_index(artist.artist_name, artist.artist_id)
            register_cached_entity("Artist", artist)
        return artist

    def _resolve_role(self, role_name: str, matched_id=None):
        """Return the role by completer pick or name, creating it if needed; None on failure."""
        if matched_id is not None:
            return self.controller.get.get_entity_object("Role", role_id=matched_id)

        existing_role = self.controller.get.get_entity_object("Role", role_name=role_name)
        role = existing_role if not isinstance(existing_role, list) else (existing_role[0] if existing_role else None)
        if not role:
            role = self.controller.add.add_entity("Role", role_name=role_name)
            if role is not None:
                register_cached_entity("Role", role)

        if role:
            self._role_edit.add_to_index(role.role_name, role.role_id)
        return role

    # ── Add / Remove ──────────────────────────────────────────────────────

    def _add_role(self):
        """Add every typed role to every typed artist on the edited track(s)."""
        artist_names = self._artist_search.split_names()
        role_names = self._role_edit.split_names()
        if not artist_names or not role_names:
            return

        # matched_id only names a single typed entry; several names are each resolved by name.
        single_artist_matched_id = self._artist_search.matched_id() if len(artist_names) == 1 else None
        artists = [self._resolve_artist(name, matched_id=single_artist_matched_id) for name in artist_names]
        if any(artist is None for artist in artists):
            QMessageBox.warning(self, "Error", "Could not resolve artist.")
            return

        single_role_matched_id = self._role_edit.matched_id() if len(role_names) == 1 else None
        roles = [self._resolve_role(name, matched_id=single_role_matched_id) for name in role_names]
        if any(role is None for role in roles):
            QMessageBox.warning(self, "Error", "Could not resolve role.")
            return

        all_ok = True
        for artist in artists:
            credited_alias_id = None
            if not self.is_multi:
                accepted, credited_alias_id = self._prompt_credited_alias(artist)
                if not accepted:
                    continue  # dismissed "Credit as…": skip, do not add under the canonical name
            for role in roles:
                all_ok = self._batch_add_track_artist_role(artist.artist_id, role.role_id, credited_alias_id=credited_alias_id) and all_ok
        if not all_ok:
            self._warn_failed("add the role to")

        self._artist_search.reset()
        self._role_edit.reset()
        self.load(self.tracks)

    def _prompt_add_role_for_artist(self, artist_id, artist_name):
        """Add the role(s) typed in the role field to an artist already in the table."""
        role_names = self._role_edit.split_names()
        if not role_names:
            show_status_message(self, f"Type a role name (min 2 chars) in the role field, then click '+ Add role…' next to {artist_name}.")
            self._role_edit.setFocus()
            return

        single_matched_id = self._role_edit.matched_id() if len(role_names) == 1 else None
        roles = [self._resolve_role(name, matched_id=single_matched_id) for name in role_names]
        if any(role is None for role in roles):
            QMessageBox.warning(self, "Error", "Could not resolve role.")
            return

        credited_alias_id = None
        if not self.is_multi:
            artist = self.controller.get.get_entity_object("Artist", artist_id=artist_id)
            if artist:
                accepted, credited_alias_id = self._prompt_credited_alias(artist)
                if not accepted:
                    return

        all_ok = True
        for role in roles:  # try every role, even after a failure
            all_ok = self._batch_add_track_artist_role(artist_id, role.role_id, credited_alias_id=credited_alias_id) and all_ok
        if not all_ok:
            self._warn_failed("add the role to")
        self._role_edit.reset()
        self.load(self.tracks)

    def _prompt_credited_alias(self, artist, current_alias_id=None):
        """Ask which alias to credit `artist` as; return (accepted, alias_id)."""
        aliases = self.controller.get.get_all_entities("ArtistAlias", artist_id=artist.artist_id)
        if not aliases:
            return True, current_alias_id

        dialog = CreditedAsDialog(artist, aliases, current_alias_id=current_alias_id, parent=self)
        if dialog.exec() == QDialog.Accepted:
            return True, dialog.selected_alias_id()
        return False, current_alias_id

    def _change_credited_alias(self, artist_id, roles: dict, current_alias_id):
        """Change the credited name for every role this artist holds on the single track."""
        artist = self.controller.get.get_entity_object("Artist", artist_id=artist_id)
        if not artist:
            return

        accepted, new_alias_id = self._prompt_credited_alias(artist, current_alias_id=current_alias_id)
        if not accepted or new_alias_id == current_alias_id:
            return

        failed = [
            role_id
            for role_id in roles
            if not self.controller.update.update_entity_by_filter("TrackArtistRole", {"track_id": self.track.track_id, "artist_id": artist_id, "role_id": role_id}, credited_alias_id=new_alias_id)
        ]
        if failed:
            self._warn_failed("change the credited name of")
        self.load(self.tracks)

    def _remove_role(self, artist_id, role_id):
        """Remove one role of one artist from every edited track."""
        if not self._batch_delete_track_artist_role(artist_id, role_id):
            self._warn_failed("remove the role from")
        self.load(self.tracks)

    def _remove_all_roles_for_artist(self, artist_id):
        """Remove every role of one artist from every edited track."""
        # One filtered delete: the cached track.artist_roles can be out of date after edits.
        track_ids = [track.track_id for track in self.tracks]
        if not self.controller.delete.delete_entity("TrackArtistRole", track_id=track_ids, artist_id=artist_id):
            self._warn_failed("remove the artist's roles from")
        self.load(self.tracks)

    def _warn_failed(self, action: str) -> None:
        """Tell the user that a role write failed."""
        QMessageBox.warning(self, "Error", f"Could not {action} the selected track(s). See the log for details.")

    # ── Batch helpers ─────────────────────────────────────────────────────
    # Each call is one DB statement for every selected track.

    def _batch_add_track_artist_role(self, artist_id, role_id, credited_alias_id=None) -> bool:
        """Add one artist role to every edited track; True if no row failed."""
        rows = [{"track_id": track.track_id, "artist_id": artist_id, "role_id": role_id, "credited_alias_id": credited_alias_id} for track in self.tracks]
        # Rows that already exist are skipped, not reported as failed.
        _added, failed = self.controller.add.add_entities_with_fallback("TrackArtistRole", rows)
        if failed:
            logger.error(f"Failed to add role {role_id} for artist {artist_id} to track(s) {[r['track_id'] for r in failed]}")
        return not failed

    def _batch_delete_track_artist_role(self, artist_id, role_id) -> bool:
        """Remove one artist role from every edited track; True on success."""
        track_ids = [track.track_id for track in self.tracks]
        return bool(self.controller.delete.delete_entity("TrackArtistRole", track_id=track_ids, artist_id=artist_id, role_id=role_id))
