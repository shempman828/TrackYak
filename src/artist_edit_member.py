# ══════════════════════════════════════════════════════════════════════════════
# Tab: Members
# ══════════════════════════════════════════════════════════════════════════════
from PySide6.QtCore import Qt
from PySide6.QtGui import QIntValidator
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.logger_config import logger


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


def _remove_selected_row(parent_widget, table, remove_fn):
    rows = table.selectionModel().selectedRows()
    if not rows:
        QMessageBox.information(
            parent_widget, "No Selection", "Please select a row first."
        )
        return
    remove_fn(rows[0].row())


def _find_or_create_artist(controller, name, **create_kwargs):
    """
    Look up an artist by name; create one if none is found.
    Raises on error — let the caller catch and show a dialog.
    """
    result = controller.get.get_entity_object("Artist", artist_name=name)
    if result:
        return result[0] if isinstance(result, list) else result
    return controller.add.add_entity("Artist", artist_name=name, **create_kwargs)


class MembersTab(QWidget):
    """
    GroupMembership tab.

    - For GROUPS (isgroup=True):  shows "Members of this Group" with an Add form.
    - For INDIVIDUALS (isgroup=False): shows "Groups This Artist Belongs To" with an Add form.
    Both panels are always present; update_visibility() toggles which is shown.
    """

    def __init__(self, controller, artist, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.artist = artist
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        splitter = QSplitter(Qt.Vertical)
        splitter.addWidget(self._build_group_members_panel())
        splitter.addWidget(self._build_affiliations_panel())
        layout.addWidget(splitter)

    def _build_group_members_panel(self):
        """'Members of this Group' — shown when isgroup=True."""
        self._group_panel = QWidget()
        layout = QVBoxLayout(self._group_panel)
        layout.setContentsMargins(0, 0, 0, 0)

        grp = QGroupBox("Members of this Group")
        grp_layout = QVBoxLayout(grp)

        self.members_table = _make_table(
            ["Member", "Role", "Start Year", "End Year", "Current"], editable=False
        )
        grp_layout.addWidget(self.members_table)

        add_row = QHBoxLayout()
        self.new_member_edit = QLineEdit()
        self.new_member_edit.setPlaceholderText("Member artist name...")
        self.new_member_role_edit = QLineEdit()
        self.new_member_role_edit.setPlaceholderText("Role (e.g. Guitarist)")
        self.new_member_start_edit = OptionalIntEdit("Start yr")
        self.new_member_end_edit = OptionalIntEdit("End yr")
        self.new_member_current_check = QCheckBox("Current")
        add_btn = QPushButton("Add Member")
        add_btn.clicked.connect(self._add_member)
        add_row.addWidget(self.new_member_edit, 2)
        add_row.addWidget(self.new_member_role_edit, 2)
        add_row.addWidget(QLabel("Start"))
        add_row.addWidget(self.new_member_start_edit)
        add_row.addWidget(QLabel("End"))
        add_row.addWidget(self.new_member_end_edit)
        add_row.addWidget(self.new_member_current_check)
        add_row.addWidget(add_btn)
        grp_layout.addLayout(add_row)

        rm_btn = QPushButton("Remove Selected Member")
        rm_btn.clicked.connect(
            lambda: _remove_selected_row(self, self.members_table, self._remove_member)
        )
        grp_layout.addWidget(rm_btn, alignment=Qt.AlignLeft)

        layout.addWidget(grp)
        return self._group_panel

    def _build_affiliations_panel(self):
        """'Groups This Artist Belongs To' — shown when isgroup=False."""
        self._affil_panel = QWidget()
        layout = QVBoxLayout(self._affil_panel)
        layout.setContentsMargins(0, 0, 0, 0)

        grp = QGroupBox("Groups This Artist Belongs To")
        grp_layout = QVBoxLayout(grp)

        self.affiliations_table = _make_table(
            ["Group", "Role", "Start Year", "End Year", "Current"], editable=False
        )
        grp_layout.addWidget(self.affiliations_table)

        add_row = QHBoxLayout()
        self.new_affil_group_edit = QLineEdit()
        self.new_affil_group_edit.setPlaceholderText("Group / band name...")
        self.new_affil_role_edit = QLineEdit()
        self.new_affil_role_edit.setPlaceholderText("Role (e.g. Vocalist)")
        self.new_affil_start_edit = OptionalIntEdit("Start yr")
        self.new_affil_end_edit = OptionalIntEdit("End yr")
        self.new_affil_current_check = QCheckBox("Current")
        add_btn = QPushButton("Add to Group")
        add_btn.clicked.connect(self._add_affiliation)
        add_row.addWidget(self.new_affil_group_edit, 2)
        add_row.addWidget(self.new_affil_role_edit, 2)
        add_row.addWidget(QLabel("Start"))
        add_row.addWidget(self.new_affil_start_edit)
        add_row.addWidget(QLabel("End"))
        add_row.addWidget(self.new_affil_end_edit)
        add_row.addWidget(self.new_affil_current_check)
        add_row.addWidget(add_btn)
        grp_layout.addLayout(add_row)

        rm_btn = QPushButton("Remove Selected")
        rm_btn.clicked.connect(
            lambda: _remove_selected_row(
                self, self.affiliations_table, self._remove_affiliation
            )
        )
        grp_layout.addWidget(rm_btn, alignment=Qt.AlignLeft)

        layout.addWidget(grp)
        return self._affil_panel

    def load(self, artist):
        self.artist = artist

        self.members_table.setRowCount(0)
        for m in getattr(artist, "group_memberships", []):
            if m.member is None:
                continue
            _append_row(
                self.members_table,
                [
                    m.member.artist_name,
                    m.role or "",
                    m.active_start_year or "",
                    m.active_end_year or "",
                    "Yes" if m.is_current else "No",
                ],
                user_data=(m.group_id, m.member_id),
            )

        self.affiliations_table.setRowCount(0)
        for m in getattr(artist, "member_memberships", []):
            if m.group is None:
                continue
            _append_row(
                self.affiliations_table,
                [
                    m.group.artist_name,
                    m.role or "",
                    m.active_start_year or "",
                    m.active_end_year or "",
                    "Yes" if m.is_current else "No",
                ],
                user_data=(m.group_id, m.member_id),
            )

        self.update_visibility(bool(artist.isgroup))

    def update_visibility(self, is_group: bool):
        """Called by ArtistEditor when the isgroup checkbox changes."""
        self._group_panel.setVisible(is_group)
        self._affil_panel.setVisible(not is_group)

    def _reload_and_refresh(self):
        try:
            refreshed = self.controller.get.get_entity_object(
                "Artist", artist_id=self.artist.artist_id
            )
            if refreshed:
                self.artist = refreshed
        except Exception as e:
            logger.warning(f"Could not reload artist: {e}")
        self.load(self.artist)

    def _add_member(self):
        name = self.new_member_edit.text().strip()
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
                role=self.new_member_role_edit.text().strip() or None,
                active_start_year=self.new_member_start_edit.get_value_or_none(),
                active_end_year=self.new_member_end_edit.get_value_or_none(),
                is_current=1 if self.new_member_current_check.isChecked() else 0,
            )
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Could not add member:\n{e}")
            return
        self._reload_and_refresh()
        self.new_member_edit.clear()
        self.new_member_role_edit.clear()
        self.new_member_start_edit.clear()
        self.new_member_end_edit.clear()
        self.new_member_current_check.setChecked(False)

    def _remove_member(self, row):
        data = self.members_table.item(row, 0).data(Qt.UserRole)
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
        self._reload_and_refresh()

    def _add_affiliation(self):
        name = self.new_affil_group_edit.text().strip()
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
                role=self.new_affil_role_edit.text().strip() or None,
                active_start_year=self.new_affil_start_edit.get_value_or_none(),
                active_end_year=self.new_affil_end_edit.get_value_or_none(),
                is_current=1 if self.new_affil_current_check.isChecked() else 0,
            )
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Could not add to group:\n{e}")
            return
        self._reload_and_refresh()
        self.new_affil_group_edit.clear()
        self.new_affil_role_edit.clear()
        self.new_affil_start_edit.clear()
        self.new_affil_end_edit.clear()
        self.new_affil_current_check.setChecked(False)

    def _remove_affiliation(self, row):
        data = self.affiliations_table.item(row, 0).data(Qt.UserRole)
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
        self._reload_and_refresh()


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
