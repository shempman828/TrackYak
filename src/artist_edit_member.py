# ══════════════════════════════════════════════════════════════════════════════
# Tab: Members
# ══════════════════════════════════════════════════════════════════════════════
from PySide6.QtCore import QStringListModel, Qt
from PySide6.QtGui import QIntValidator
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QCompleter,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.logger_config import logger

# ──────────────────────────────────────────────────────────────────────────────
# Shared helpers
# ──────────────────────────────────────────────────────────────────────────────


def _make_table(headers):
    """Create a read-only QTableWidget with consistent styling."""
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.horizontalHeader().setStretchLastSection(True)
    t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
    t.horizontalHeader().setSectionResizeMode(len(headers) - 1, QHeaderView.Stretch)
    t.setSelectionBehavior(QAbstractItemView.SelectRows)
    t.setEditTriggers(QAbstractItemView.NoEditTriggers)
    t.verticalHeader().setVisible(False)
    t.setAlternatingRowColors(True)
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


def _get_selected_row(parent_widget, table):
    rows = table.selectionModel().selectedRows()
    if not rows:
        QMessageBox.information(
            parent_widget, "No Selection", "Please select a row first."
        )
        return None
    return rows[0].row()


def _remove_selected_row(parent_widget, table, remove_fn):
    row = _get_selected_row(parent_widget, table)
    if row is not None:
        remove_fn(row)


def _edit_selected_row(parent_widget, table, edit_fn):
    row = _get_selected_row(parent_widget, table)
    if row is not None:
        edit_fn(row)


def _find_or_create_artist(controller, name, **create_kwargs):
    """
    Look up an artist by name; create one if none is found.
    Raises on error — let the caller catch and show a dialog.
    """
    result = controller.get.get_entity_object("Artist", artist_name=name)
    if result:
        return result[0] if isinstance(result, list) else result
    return controller.add.add_entity("Artist", artist_name=name, **create_kwargs)


def _fetch_artist_names(controller):
    """Fetch all artist names for completer indexing."""
    try:
        artists = controller.get.get_all_entities("Artist") or []
        return sorted({a.artist_name for a in artists if getattr(a, "artist_name", None)})
    except Exception as e:
        logger.warning(f"Could not fetch artists for completer: {e}")
        return []


def _attach_name_completer(line_edit):
    """Attach an empty QCompleter to a name field; populate via completer.setModel()."""
    completer = QCompleter([], line_edit)
    completer.setCaseSensitivity(Qt.CaseInsensitive)
    completer.setFilterMode(Qt.MatchContains)
    line_edit.setCompleter(completer)
    return completer


class _EditMembershipDialog(QDialog):
    """Small modal for editing an existing membership's role/years/current flag."""

    def __init__(self, role, start_year, end_year, is_current, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit Membership")

        layout = QFormLayout(self)

        self.role_edit = QLineEdit(role or "")
        self.start_edit = OptionalIntEdit()
        self.start_edit.set_from_db(start_year)
        self.end_edit = OptionalIntEdit()
        self.end_edit.set_from_db(end_year)
        self.current_check = QCheckBox("Current")
        self.current_check.setChecked(bool(is_current))

        layout.addRow("Role", self.role_edit)
        layout.addRow("Start Year", self.start_edit)
        layout.addRow("End Year", self.end_edit)
        layout.addRow("", self.current_check)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def values(self):
        return {
            "role": self.role_edit.text().strip() or None,
            "active_start_year": self.start_edit.get_value_or_none(),
            "active_end_year": self.end_edit.get_value_or_none(),
            "is_current": 1 if self.current_check.isChecked() else 0,
        }


# ──────────────────────────────────────────────────────────────────────────────
# _GroupMembersPanel  (used when isgroup=True)
# ──────────────────────────────────────────────────────────────────────────────


class _GroupMembersPanel(QWidget):
    """
    Shows the members *of* this group artist, with an add/remove form.
    Only instantiated (and data-loaded) when the artist is a group.
    """

    _HEADERS = ["Member", "Role", "Start Year", "End Year", "Current"]

    def __init__(self, controller, artist, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.artist = artist
        self._build_ui()
        self._refresh_completer()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        grp = QGroupBox("Members of this Group")
        grp_layout = QVBoxLayout(grp)

        self.table = _make_table(self._HEADERS)
        self.table.cellDoubleClicked.connect(lambda row, _col: self._edit(row))
        grp_layout.addWidget(self.table)

        # ── Add-member form ──────────────────────────────────────────────────
        add_row = QHBoxLayout()
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("Member artist name...")
        self._name_completer = _attach_name_completer(self.name_edit)
        self.role_edit = QLineEdit()
        self.role_edit.setPlaceholderText("Role (e.g. Guitarist)")
        self.start_edit = OptionalIntEdit("Start yr")
        self.end_edit = OptionalIntEdit("End yr")
        self.current_check = QCheckBox("Current")
        add_btn = QPushButton("Add Member")
        add_btn.clicked.connect(self._add)

        add_row.addWidget(self.name_edit, 2)
        add_row.addWidget(self.role_edit, 2)
        add_row.addWidget(QLabel("Start"))
        add_row.addWidget(self.start_edit)
        add_row.addWidget(QLabel("End"))
        add_row.addWidget(self.end_edit)
        add_row.addWidget(self.current_check)
        add_row.addWidget(add_btn)
        grp_layout.addLayout(add_row)

        btn_row = QHBoxLayout()
        edit_btn = QPushButton("Edit Selected Member")
        edit_btn.clicked.connect(
            lambda: _edit_selected_row(self, self.table, self._edit)
        )
        btn_row.addWidget(edit_btn)

        rm_btn = QPushButton("Remove Selected Member")
        rm_btn.clicked.connect(
            lambda: _remove_selected_row(self, self.table, self._remove)
        )
        btn_row.addWidget(rm_btn)
        btn_row.addStretch()
        grp_layout.addLayout(btn_row)

        layout.addWidget(grp)

    def _refresh_completer(self):
        names = _fetch_artist_names(self.controller)
        self._name_completer.setModel(QStringListModel(names, self))

    def load(self, artist):
        self.artist = artist
        self.table.setRowCount(0)
        for m in getattr(artist, "group_memberships", []):
            if m.member is None:
                continue
            _append_row(
                self.table,
                [
                    m.member.artist_name,
                    m.role or "",
                    m.active_start_year or "",
                    m.active_end_year or "",
                    "Yes" if m.is_current else "No",
                ],
                user_data=(m.group_id, m.member_id),
            )

    def _reload(self):
        try:
            refreshed = self.controller.get.get_entity_object(
                "Artist", artist_id=self.artist.artist_id
            )
            if refreshed:
                self.artist = refreshed
        except Exception as e:
            logger.warning(f"Could not reload artist: {e}")
        self.load(self.artist)

    def _add(self):
        name = self.name_edit.text().strip()
        if not name:
            QMessageBox.warning(
                self, "Validation", "Please enter a member artist name."
            )
            return
        try:
            member = _find_or_create_artist(self.controller, name)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Could not find/create artist:\n{e}")
            return
        try:
            self.controller.add.add_entity(
                "GroupMembership",
                group_id=self.artist.artist_id,
                member_id=member.artist_id,
                role=self.role_edit.text().strip() or None,
                active_start_year=self.start_edit.get_value_or_none(),
                active_end_year=self.end_edit.get_value_or_none(),
                is_current=1 if self.current_check.isChecked() else 0,
            )
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Could not add member:\n{e}")
            return
        self._reload()
        self._refresh_completer()
        self.name_edit.clear()
        self.role_edit.clear()
        self.start_edit.clear()
        self.end_edit.clear()
        self.current_check.setChecked(False)

    def _edit(self, row):
        data = self.table.item(row, 0).data(Qt.UserRole)
        if data is None:
            return
        group_id, member_id = data
        role = self.table.item(row, 1).text()
        start_year = self.table.item(row, 2).text() or None
        end_year = self.table.item(row, 3).text() or None
        is_current = self.table.item(row, 4).text() == "Yes"

        dialog = _EditMembershipDialog(role, start_year, end_year, is_current, self)
        if dialog.exec() != QDialog.Accepted:
            return
        try:
            self.controller.delete.delete_entity(
                "GroupMembership", group_id=group_id, member_id=member_id
            )
            self.controller.add.add_entity(
                "GroupMembership",
                group_id=group_id,
                member_id=member_id,
                **dialog.values(),
            )
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Could not update membership:\n{e}")
            return
        self._reload()

    def _remove(self, row):
        data = self.table.item(row, 0).data(Qt.UserRole)
        if data is None:
            return
        group_id, member_id = data
        try:
            self.controller.delete.delete_entity(
                "GroupMembership", group_id=group_id, member_id=member_id
            )
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Could not remove member:\n{e}")
            return
        self._reload()


# ──────────────────────────────────────────────────────────────────────────────
# _AffiliationsPanel  (used when isgroup=False)
# ──────────────────────────────────────────────────────────────────────────────


class _AffiliationsPanel(QWidget):
    """
    Shows the groups *this* individual artist belongs to, with an add/remove form.
    Only instantiated (and data-loaded) when the artist is not a group.
    """

    _HEADERS = ["Group", "Role", "Start Year", "End Year", "Current"]

    def __init__(self, controller, artist, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.artist = artist
        self._build_ui()
        self._refresh_completer()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        grp = QGroupBox("Groups This Artist Belongs To")
        grp_layout = QVBoxLayout(grp)

        self.table = _make_table(self._HEADERS)
        self.table.cellDoubleClicked.connect(lambda row, _col: self._edit(row))
        grp_layout.addWidget(self.table)

        # ── Add-affiliation form ─────────────────────────────────────────────
        add_row = QHBoxLayout()
        self.group_edit = QLineEdit()
        self.group_edit.setPlaceholderText("Group / band name...")
        self._name_completer = _attach_name_completer(self.group_edit)
        self.role_edit = QLineEdit()
        self.role_edit.setPlaceholderText("Role (e.g. Vocalist)")
        self.start_edit = OptionalIntEdit("Start yr")
        self.end_edit = OptionalIntEdit("End yr")
        self.current_check = QCheckBox("Current")
        add_btn = QPushButton("Add to Group")
        add_btn.clicked.connect(self._add)

        add_row.addWidget(self.group_edit, 2)
        add_row.addWidget(self.role_edit, 2)
        add_row.addWidget(QLabel("Start"))
        add_row.addWidget(self.start_edit)
        add_row.addWidget(QLabel("End"))
        add_row.addWidget(self.end_edit)
        add_row.addWidget(self.current_check)
        add_row.addWidget(add_btn)
        grp_layout.addLayout(add_row)

        btn_row = QHBoxLayout()
        edit_btn = QPushButton("Edit Selected")
        edit_btn.clicked.connect(
            lambda: _edit_selected_row(self, self.table, self._edit)
        )
        btn_row.addWidget(edit_btn)

        rm_btn = QPushButton("Remove Selected")
        rm_btn.clicked.connect(
            lambda: _remove_selected_row(self, self.table, self._remove)
        )
        btn_row.addWidget(rm_btn)
        btn_row.addStretch()
        grp_layout.addLayout(btn_row)

        layout.addWidget(grp)

    def _refresh_completer(self):
        names = _fetch_artist_names(self.controller)
        self._name_completer.setModel(QStringListModel(names, self))

    def load(self, artist):
        self.artist = artist
        self.table.setRowCount(0)
        for m in getattr(artist, "member_memberships", []):
            if m.group is None:
                continue
            _append_row(
                self.table,
                [
                    m.group.artist_name,
                    m.role or "",
                    m.active_start_year or "",
                    m.active_end_year or "",
                    "Yes" if m.is_current else "No",
                ],
                user_data=(m.group_id, m.member_id),
            )

    def _reload(self):
        try:
            refreshed = self.controller.get.get_entity_object(
                "Artist", artist_id=self.artist.artist_id
            )
            if refreshed:
                self.artist = refreshed
        except Exception as e:
            logger.warning(f"Could not reload artist: {e}")
        self.load(self.artist)

    def _add(self):
        name = self.group_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "Validation", "Please enter a group name.")
            return
        try:
            group = _find_or_create_artist(self.controller, name, isgroup=1)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Could not find/create group:\n{e}")
            return
        try:
            self.controller.add.add_entity(
                "GroupMembership",
                group_id=group.artist_id,
                member_id=self.artist.artist_id,
                role=self.role_edit.text().strip() or None,
                active_start_year=self.start_edit.get_value_or_none(),
                active_end_year=self.end_edit.get_value_or_none(),
                is_current=1 if self.current_check.isChecked() else 0,
            )
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Could not add to group:\n{e}")
            return
        self._reload()
        self._refresh_completer()
        self.group_edit.clear()
        self.role_edit.clear()
        self.start_edit.clear()
        self.end_edit.clear()
        self.current_check.setChecked(False)

    def _edit(self, row):
        data = self.table.item(row, 0).data(Qt.UserRole)
        if data is None:
            return
        group_id, member_id = data
        role = self.table.item(row, 1).text()
        start_year = self.table.item(row, 2).text() or None
        end_year = self.table.item(row, 3).text() or None
        is_current = self.table.item(row, 4).text() == "Yes"

        dialog = _EditMembershipDialog(role, start_year, end_year, is_current, self)
        if dialog.exec() != QDialog.Accepted:
            return
        try:
            self.controller.delete.delete_entity(
                "GroupMembership", group_id=group_id, member_id=member_id
            )
            self.controller.add.add_entity(
                "GroupMembership",
                group_id=group_id,
                member_id=member_id,
                **dialog.values(),
            )
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Could not update membership:\n{e}")
            return
        self._reload()

    def _remove(self, row):
        data = self.table.item(row, 0).data(Qt.UserRole)
        if data is None:
            return
        group_id, member_id = data
        try:
            self.controller.delete.delete_entity(
                "GroupMembership", group_id=group_id, member_id=member_id
            )
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Could not remove from group:\n{e}")
            return
        self._reload()


# ──────────────────────────────────────────────────────────────────────────────
# MembersTab  (public API — unchanged from the caller's perspective)
# ──────────────────────────────────────────────────────────────────────────────

_GROUP_PAGE = 0
_AFFIL_PAGE = 1


class MembersTab(QWidget):
    """
    GroupMembership tab.

    Uses a QStackedWidget so only the relevant panel exists in the layout at any
    time — no hidden-but-present sibling confusing resizes or eating queries.

    Public API (unchanged):
        load(artist)                — populate and switch to the right panel
        update_visibility(is_group) — called by ArtistEditor on checkbox toggle
    """

    def __init__(self, controller, artist, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.artist = artist
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        self._stack = QStackedWidget()
        self._group_panel = _GroupMembersPanel(self.controller, self.artist)
        self._affil_panel = _AffiliationsPanel(self.controller, self.artist)
        self._stack.insertWidget(_GROUP_PAGE, self._group_panel)
        self._stack.insertWidget(_AFFIL_PAGE, self._affil_panel)

        layout.addWidget(self._stack)

    def load(self, artist):
        self.artist = artist
        is_group = bool(artist.isgroup)
        if is_group:
            self._group_panel.load(artist)
        else:
            self._affil_panel.load(artist)
        self._stack.setCurrentIndex(_GROUP_PAGE if is_group else _AFFIL_PAGE)

    def update_visibility(self, is_group: bool):
        """Called by ArtistEditor when the isgroup checkbox changes."""
        self._stack.setCurrentIndex(_GROUP_PAGE if is_group else _AFFIL_PAGE)
        # Re-load the now-visible panel so it always reflects current data.
        if is_group:
            self._group_panel.load(self.artist)
        else:
            self._affil_panel.load(self.artist)


# ──────────────────────────────────────────────────────────────────────────────
# OptionalIntEdit
# ──────────────────────────────────────────────────────────────────────────────


class OptionalIntEdit(QLineEdit):
    """A QLineEdit that only accepts integers and returns None when empty."""

    def __init__(self, placeholder="", parent=None):
        super().__init__(parent)
        self.setPlaceholderText(placeholder)
        self.setFixedWidth(70)
        self.setValidator(QIntValidator(0, 9999, self))

    def get_value_or_none(self):
        text = self.text().strip()
        return int(text) if text else None

    def set_from_db(self, val):
        self.setText(str(int(val)) if val is not None else "")
