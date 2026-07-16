# ---------------------------------------------------------------------------
# RolesTab — artist / role relationships
# ---------------------------------------------------------------------------
from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, QSize, Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLayout,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.common.credited_as_dialog import CreditedAsDialog
from src.core.logger_config import logger
from src.core.status_utility import show_status_message
from src.track.track_edit_basetab import _BaseTab

PRIMARY_ARTIST_ROLE = "Primary Artist"


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
    """

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.resizeRowsToContents()


class RolesTab(_BaseTab):
    def __init__(self, tracks: list, controller, parent=None):
        super().__init__(tracks, controller, parent)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        # ── Search row ────────────────────────────────────────────────────
        search_row = QHBoxLayout()

        self._artist_search = QLineEdit()
        self._artist_search.setPlaceholderText("Search artists… (min 2 chars)")
        self._artist_search.textChanged.connect(self._on_artist_search)
        search_row.addWidget(self._artist_search)

        self._artist_combo = QComboBox()
        self._artist_combo.setVisible(False)
        self._artist_combo.currentIndexChanged.connect(self._on_artist_selected)
        search_row.addWidget(self._artist_combo)

        self._role_edit = QLineEdit()
        self._role_edit.setPlaceholderText("Role (e.g. Performer, Composer…)")
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
        self._table.verticalHeader().setVisible(False)
        self._table.setToolTip(
            "Each artist gets one row; their roles are shown as chips with "
            "individual remove (×) buttons."
        )
        layout.addWidget(self._table)

    # ── Loading ───────────────────────────────────────────────────────────

    def load(self, tracks: list) -> None:
        self.tracks = tracks
        self._table.setRowCount(0)

        # (artist_id, credited_name) -> {"roles": {role_id: role_name}}
        # Keyed by credited_name too (not just artist_id) so a role credited
        # under a different alias doesn't get silently merged into the same
        # display row as the artist's other, differently-credited roles.
        grouped: dict[tuple, dict] = {}

        if self.is_multi:
            self._collect_common_roles(grouped)
        else:
            for role_assoc in self.track.artist_roles:
                artist = role_assoc.artist
                role = role_assoc.role
                artist_id = artist.artist_id if artist else None
                credited_name = role_assoc.credited_name or "?"
                role_id = role.role_id if role else None
                role_name = role.role_name if role else "?"

                entry = grouped.setdefault(
                    (artist_id, credited_name),
                    {"roles": {}, "credited_alias_id": role_assoc.credited_alias_id},
                )
                entry["roles"][role_id] = role_name

        for (artist_id, credited_name), entry in self._sorted_groups(grouped):
            self._add_artist_row(
                artist_id,
                credited_name,
                entry["roles"],
                credited_alias_id=entry.get("credited_alias_id"),
            )

    def _collect_common_roles(self, grouped: dict) -> None:
        """Populate `grouped` with artist/role pairs shared by every track."""
        all_sets = []
        for t in self.tracks:
            s = set()
            for ra in t.artist_roles:
                if ra.artist and ra.role:
                    s.add(
                        (
                            ra.artist.artist_id,
                            ra.role.role_id,
                            ra.credited_name,
                            ra.role.role_name,
                        )
                    )
            all_sets.append(s)

        common = all_sets[0] if all_sets else set()
        for s in all_sets[1:]:
            common &= s

        for artist_id, role_id, credited_name, role_name in common:
            entry = grouped.setdefault((artist_id, credited_name), {"roles": {}})
            entry["roles"][role_id] = role_name

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

    def _add_artist_row(self, artist_id, artist_name, roles: dict, credited_alias_id=None):
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
        actions_layout.setContentsMargins(0, 0, 0, 0)
        actions_layout.setSpacing(4)

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

    def _on_artist_search(self, text: str):
        text = text.strip()
        self._artist_combo.blockSignals(True)
        self._artist_combo.clear()
        if len(text) >= 2:
            results = self.controller.get.get_entity_object("Artist", artist_name=text)
            self._artist_combo.addItem(f"Create new: '{text}'", "new")
            if results is not None:
                items = results if isinstance(results, list) else [results]
                for a in items:
                    self._artist_combo.addItem(a.artist_name, a.artist_id)
                # Auto-select the first real match so "Create new" isn't the default
                # when matching artists already exist.
                if items:
                    self._artist_combo.setCurrentIndex(1)
            self._artist_combo.setVisible(self._artist_combo.count() > 1)
        else:
            self._artist_combo.setVisible(False)
        self._artist_combo.blockSignals(False)
        self._update_add_btn()

    def _on_artist_selected(self, index: int):
        if index > 0:
            self._artist_search.blockSignals(True)
            self._artist_search.setText(self._artist_combo.currentText())
            self._artist_search.blockSignals(False)
        self._update_add_btn()

    def _update_add_btn(self):
        artist_ok = len(self._artist_search.text().strip()) >= 2
        role_ok = len(self._role_edit.text().strip()) >= 2
        self._add_btn.setEnabled(artist_ok and role_ok)

    # ── Resolution helpers ────────────────────────────────────────────────

    def _resolve_artist(self, artist_name: str):
        combo_data = (
            self._artist_combo.currentData() if self._artist_combo.isVisible() else None
        )
        if combo_data and combo_data != "new":
            return self.controller.get.get_entity_object("Artist", artist_id=combo_data)

        existing = self.controller.get.get_entity_object(
            "Artist", artist_name=artist_name
        )
        if existing:
            return existing if not isinstance(existing, list) else existing[0]
        return self.controller.add.add_entity("Artist", artist_name=artist_name)

    def _resolve_role(self, role_name: str):
        existing_role = self.controller.get.get_entity_object(
            "Role", role_name=role_name
        )
        if existing_role:
            return (
                existing_role
                if not isinstance(existing_role, list)
                else existing_role[0]
            )
        return self.controller.add.add_entity("Role", role_name=role_name)

    # ── Add / Remove ──────────────────────────────────────────────────────

    def _add_role(self):
        artist_name = self._artist_search.text().strip()
        role_name = self._role_edit.text().strip()
        if not artist_name or not role_name:
            return

        artist = self._resolve_artist(artist_name)
        role = self._resolve_role(role_name)

        if not artist or not role:
            QMessageBox.warning(self, "Error", "Could not resolve artist or role.")
            return

        credited_alias_id = None
        if not self.is_multi:
            credited_alias_id = self._prompt_credited_alias(artist)

        self._batch_add_track_artist_role(
            artist.artist_id, role.role_id, credited_alias_id=credited_alias_id
        )

        self._artist_search.clear()
        self._role_edit.clear()
        self._artist_combo.setVisible(False)
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

        role = self._resolve_role(role_name)
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
        self._role_edit.clear()
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
        except Exception as e:
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
        except Exception as e:
            logger.error(f"Failed to remove role from tracks: {e}")
