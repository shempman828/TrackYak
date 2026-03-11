"""
artist_edit.py

Tabbed dialog for editing every field and relationship of an Artist.
"""

from PySide6.QtCore import Qt
from PySide6.QtGui import QIntValidator, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QCompleter,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
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
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from src.artist_alias_dialog import ArtistAliasDialog
from src.logger_config import logger

# ── Constants ──────────────────────────────────────────────────────────────────

ARTIST_TYPE_SUGGESTIONS = ["Person", "Band", "Orchestra", "Choir", "Ensemble"]
GENDERS = ["", "Male", "Female", "Other"]


# ══════════════════════════════════════════════════════════════════════════════
# Shared widget and table helpers (module-level, used by all tabs)
# ══════════════════════════════════════════════════════════════════════════════


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


def _parent_place_name(place):
    if place.parent_id and hasattr(place, "parent") and place.parent:
        return place.parent.place_name
    return ""


# ══════════════════════════════════════════════════════════════════════════════
# Tab: Basic
# ══════════════════════════════════════════════════════════════════════════════


class BasicTab(QWidget):
    """
    Core identity fields: name, type, isgroup, gender, dates, profile picture.

    collect_changes() returns a dict ready to pass to update_entity("Artist", ...).
    The isgroup_check signal is connected externally by ArtistEditor to keep
    MembersTab visibility in sync.
    """

    def __init__(self, controller, artist, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.artist = artist
        self._build_ui()

    def _build_ui(self):
        layout = QHBoxLayout(self)

        # ── Left: form ──────────────────────────────────────────────────────
        form_widget = QWidget()
        form = QFormLayout(form_widget)
        form.setLabelAlignment(Qt.AlignRight)
        form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("Artist name")
        form.addRow("Name *:", self.name_edit)

        self.artist_type_edit = QLineEdit()
        self.artist_type_edit.setPlaceholderText("e.g. Person, Band, Orchestra...")
        self.artist_type_edit.setToolTip(
            "Type any value. Common types are suggested as you type."
        )
        completer = QCompleter(ARTIST_TYPE_SUGGESTIONS, self)
        completer.setCaseSensitivity(Qt.CaseInsensitive)
        completer.setCompletionMode(QCompleter.PopupCompletion)
        self.artist_type_edit.setCompleter(completer)
        form.addRow("Type:", self.artist_type_edit)

        self.isgroup_check = QCheckBox("This name represents a group / band")
        self.isgroup_check.toggled.connect(self._on_isgroup_changed)
        form.addRow("Is Group:", self.isgroup_check)

        self.gender_combo = QComboBox()
        self.gender_combo.addItems(GENDERS)
        form.addRow("Gender:", self.gender_combo)

        # Begin date
        begin_box = QHBoxLayout()
        self.begin_year_edit = OptionalIntEdit("YYYY")
        self.begin_year_edit.setToolTip("Year born / founded")
        self.begin_month_edit = OptionalIntEdit("MM")
        self.begin_month_edit.setToolTip("Month (1-12)")
        self.begin_day_edit = OptionalIntEdit("DD")
        self.begin_day_edit.setToolTip("Day (1-31)")
        begin_box.addWidget(QLabel("Year"))
        begin_box.addWidget(self.begin_year_edit)
        begin_box.addWidget(QLabel("Month"))
        begin_box.addWidget(self.begin_month_edit)
        begin_box.addWidget(QLabel("Day"))
        begin_box.addWidget(self.begin_day_edit)
        begin_box.addStretch()
        self.begin_date_label = QLabel("Born / Founded:")
        form.addRow(self.begin_date_label, begin_box)

        # Active status toggle
        self.is_active_check = QCheckBox("Alive / Active")
        self.is_active_check.setToolTip(
            "Check this if the artist is still active. "
            "Unchecking enables the end date fields below."
        )
        self.is_active_check.toggled.connect(self._on_active_toggled)
        form.addRow("Status:", self.is_active_check)

        # End date
        end_box = QHBoxLayout()
        self.end_year_edit = OptionalIntEdit("YYYY")
        self.end_year_edit.setToolTip("Year died / disbanded")
        self.end_month_edit = OptionalIntEdit("MM")
        self.end_day_edit = OptionalIntEdit("DD")
        end_box.addWidget(QLabel("Year"))
        end_box.addWidget(self.end_year_edit)
        end_box.addWidget(QLabel("Month"))
        end_box.addWidget(self.end_month_edit)
        end_box.addWidget(QLabel("Day"))
        end_box.addWidget(self.end_day_edit)
        end_box.addStretch()
        self.end_date_label = QLabel("Died / Disbanded:")
        form.addRow(self.end_date_label, end_box)

        layout.addWidget(form_widget, 1)

        # ── Right: profile picture ───────────────────────────────────────────
        pic_grp = QGroupBox("Profile Picture")
        pic_layout = QVBoxLayout(pic_grp)
        self.pic_label = QLabel()
        self.pic_label.setFixedSize(180, 180)
        self.pic_label.setAlignment(Qt.AlignCenter)
        self.pic_label.setStyleSheet("border: 1px solid #888; background: #222;")
        self.pic_label.setText("No Image")
        pic_layout.addWidget(self.pic_label)
        self.pic_path_edit = QLineEdit()
        self.pic_path_edit.setPlaceholderText("Image path...")
        self.pic_path_edit.textChanged.connect(self._refresh_pic_preview)
        pic_layout.addWidget(self.pic_path_edit)
        browse_btn = QPushButton("Browse...")
        browse_btn.clicked.connect(self._browse_pic)
        pic_layout.addWidget(browse_btn)
        layout.addWidget(pic_grp)

    def load(self, artist):
        self.artist = artist
        self.name_edit.setText(artist.artist_name or "")
        self.artist_type_edit.setText(artist.artist_type or "")

        is_group = bool(artist.isgroup)
        self.isgroup_check.blockSignals(True)
        self.isgroup_check.setChecked(is_group)
        self.isgroup_check.blockSignals(False)
        self._on_isgroup_changed(is_group)

        g_idx = self.gender_combo.findText(artist.gender or "")
        self.gender_combo.setCurrentIndex(max(g_idx, 0))

        self.begin_year_edit.set_from_db(artist.begin_year)
        self.begin_month_edit.set_from_db(artist.begin_month)
        self.begin_day_edit.set_from_db(artist.begin_day)

        is_active = not bool(artist.end_year)
        self.is_active_check.blockSignals(True)
        self.is_active_check.setChecked(is_active)
        self.is_active_check.blockSignals(False)
        self._on_active_toggled(is_active)

        if not is_active:
            self.end_year_edit.set_from_db(artist.end_year)
            self.end_month_edit.set_from_db(artist.end_month)
            self.end_day_edit.set_from_db(artist.end_day)

        self.pic_path_edit.setText(artist.profile_pic_path or "")

    def collect_changes(self):
        """Return a dict of basic field values for update_entity."""
        return dict(
            artist_name=self.name_edit.text().strip(),
            artist_type=self.artist_type_edit.text().strip() or None,
            isgroup=1 if self.isgroup_check.isChecked() else 0,
            gender=self.gender_combo.currentText() or None,
            begin_year=self.begin_year_edit.get_value_or_none(),
            begin_month=self.begin_month_edit.get_value_or_none(),
            begin_day=self.begin_day_edit.get_value_or_none(),
            end_year=self.end_year_edit.get_value_or_none(),
            end_month=self.end_month_edit.get_value_or_none(),
            end_day=self.end_day_edit.get_value_or_none(),
            profile_pic_path=self.pic_path_edit.text().strip() or None,
        )

    # ── Internal slots ─────────────────────────────────────────────────────

    def _on_isgroup_changed(self, is_group: bool):
        if is_group:
            self.begin_date_label.setText("Founded:")
            self.end_date_label.setText("Disbanded:")
            self.is_active_check.setText("Alive / Active (still together)")
        else:
            self.begin_date_label.setText("Born:")
            self.end_date_label.setText("Died:")
            self.is_active_check.setText("Currently alive / active")

    def _on_active_toggled(self, is_active: bool):
        self.end_year_edit.setEnabled(not is_active)
        self.end_month_edit.setEnabled(not is_active)
        self.end_day_edit.setEnabled(not is_active)
        if is_active:
            self.end_year_edit.clear()
            self.end_month_edit.clear()
            self.end_day_edit.clear()

    def _browse_pic(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Profile Picture",
            "",
            "Images (*.png *.jpg *.jpeg *.webp *.bmp *.gif)",
        )
        if path:
            self.pic_path_edit.setText(path)

    def _refresh_pic_preview(self, path):
        if not path:
            self.pic_label.setText("No Image")
            return
        px = QPixmap(path)
        if px.isNull():
            self.pic_label.setText("Invalid image")
        else:
            self.pic_label.setPixmap(
                px.scaled(
                    self.pic_label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation
                )
            )


# ══════════════════════════════════════════════════════════════════════════════
# Tab: Biography
# ══════════════════════════════════════════════════════════════════════════════


class BiographyTab(QWidget):
    def __init__(self, controller, artist, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.artist = artist
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Biography:"))
        self.bio_edit = QTextEdit()
        self.bio_edit.setPlaceholderText("Enter artist biography...")
        layout.addWidget(self.bio_edit)

    def load(self, artist):
        self.artist = artist
        self.bio_edit.setPlainText(artist.biography or "")

    def collect_changes(self):
        return dict(biography=self.bio_edit.toPlainText().strip() or None)


# ══════════════════════════════════════════════════════════════════════════════
# Tab: Aliases
# ══════════════════════════════════════════════════════════════════════════════


class AliasesTab(QWidget):
    def __init__(self, controller, artist, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.artist = artist
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        info = QLabel(
            "Aliases allow an artist to be found under multiple names.\n"
            "Use the button below to open the full alias manager."
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        self.summary_label = QLabel()
        self.summary_label.setWordWrap(True)
        layout.addWidget(self.summary_label)

        btn = QPushButton("Manage Aliases...")
        btn.setFixedHeight(36)
        btn.clicked.connect(self._open_alias_dialog)
        layout.addWidget(btn, alignment=Qt.AlignLeft)
        layout.addStretch()

    def load(self, artist):
        self.artist = artist
        aliases = getattr(artist, "aliases", [])
        if aliases:
            names = ", ".join(a.alias_name for a in aliases)
            self.summary_label.setText(f"<b>{len(aliases)} alias(es):</b> {names}")
        else:
            self.summary_label.setText("<i>No aliases yet.</i>")

    def _open_alias_dialog(self):
        dlg = ArtistAliasDialog(self.controller, self.artist, parent=self)
        dlg.exec()
        try:
            refreshed = self.controller.get.get_entity_object(
                "Artist", artist_id=self.artist.artist_id
            )
            if refreshed:
                self.artist = refreshed
        except Exception:
            pass
        self.load(self.artist)


# ══════════════════════════════════════════════════════════════════════════════
# Tab: Members
# ══════════════════════════════════════════════════════════════════════════════


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


# ══════════════════════════════════════════════════════════════════════════════
# Tab: Influences
# ══════════════════════════════════════════════════════════════════════════════


class InfluencesTab(QWidget):
    def __init__(self, controller, artist, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.artist = artist
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        splitter = QSplitter(Qt.Vertical)

        # This artist influenced others
        inf_grp = QGroupBox("This Artist Influenced")
        inf_layout = QVBoxLayout(inf_grp)
        self.influenced_table = _make_table(
            ["Influenced Artist", "Description"], editable=False
        )
        inf_layout.addWidget(self.influenced_table)
        add_inf_row = QHBoxLayout()
        self.new_influenced_edit = QLineEdit()
        self.new_influenced_edit.setPlaceholderText("Artist name...")
        self.new_influenced_desc_edit = QLineEdit()
        self.new_influenced_desc_edit.setPlaceholderText("Description (optional)")
        add_inf_btn = QPushButton("Add")
        add_inf_btn.clicked.connect(self._add_influenced)
        rm_inf_btn = QPushButton("Remove Selected")
        rm_inf_btn.clicked.connect(
            lambda: _remove_selected_row(
                self, self.influenced_table, self._remove_influenced
            )
        )
        add_inf_row.addWidget(self.new_influenced_edit, 2)
        add_inf_row.addWidget(self.new_influenced_desc_edit, 2)
        add_inf_row.addWidget(add_inf_btn)
        inf_layout.addLayout(add_inf_row)
        inf_layout.addWidget(rm_inf_btn, alignment=Qt.AlignLeft)
        splitter.addWidget(inf_grp)

        # Artists who influenced this one
        infl_grp = QGroupBox("Artists Who Influenced This Artist")
        infl_layout = QVBoxLayout(infl_grp)
        self.influencer_table = _make_table(
            ["Influencer Artist", "Description"], editable=False
        )
        infl_layout.addWidget(self.influencer_table)
        add_infl_row = QHBoxLayout()
        self.new_influencer_edit = QLineEdit()
        self.new_influencer_edit.setPlaceholderText("Artist name...")
        self.new_influencer_desc_edit = QLineEdit()
        self.new_influencer_desc_edit.setPlaceholderText("Description (optional)")
        add_infl_btn = QPushButton("Add")
        add_infl_btn.clicked.connect(self._add_influencer)
        rm_infl_btn = QPushButton("Remove Selected")
        rm_infl_btn.clicked.connect(
            lambda: _remove_selected_row(
                self, self.influencer_table, self._remove_influencer
            )
        )
        add_infl_row.addWidget(self.new_influencer_edit, 2)
        add_infl_row.addWidget(self.new_influencer_desc_edit, 2)
        add_infl_row.addWidget(add_infl_btn)
        infl_layout.addLayout(add_infl_row)
        infl_layout.addWidget(rm_infl_btn, alignment=Qt.AlignLeft)
        splitter.addWidget(infl_grp)

        layout.addWidget(splitter)

    def load(self, artist):
        self.artist = artist

        self.influenced_table.setRowCount(0)
        for rel in getattr(artist, "influencer_relations", []):
            if rel.influenced is None:
                continue
            _append_row(
                self.influenced_table,
                [rel.influenced.artist_name, rel.description or ""],
                user_data=rel.influence_id if hasattr(rel, "influence_id") else None,
            )

        self.influencer_table.setRowCount(0)
        for rel in getattr(artist, "influenced_relations", []):
            if rel.influencer is None:
                continue
            _append_row(
                self.influencer_table,
                [rel.influencer.artist_name, rel.description or ""],
                user_data=rel.influence_id if hasattr(rel, "influence_id") else None,
            )

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

    def _add_influenced(self):
        name = self.new_influenced_edit.text().strip()
        if not name:
            return
        try:
            influenced = _find_or_create_artist(self.controller, name)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Could not find/create artist:\n{e}")
            return
        try:
            self.controller.add.add_entity(
                "ArtistInfluence",
                influencer_id=self.artist.artist_id,
                influenced_id=influenced.artist_id,
                description=self.new_influenced_desc_edit.text().strip() or None,
            )
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Could not add influence:\n{e}")
            return
        self._reload_and_refresh()
        self.new_influenced_edit.clear()
        self.new_influenced_desc_edit.clear()

    def _remove_influenced(self, row):
        influence_id = self.influenced_table.item(row, 0).data(Qt.UserRole)
        if influence_id is None:
            return
        try:
            self.controller.delete.delete_entity("ArtistInfluence", influence_id)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Could not remove influence:\n{e}")
            return
        self._reload_and_refresh()

    def _add_influencer(self):
        name = self.new_influencer_edit.text().strip()
        if not name:
            return
        try:
            influencer = _find_or_create_artist(self.controller, name)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Could not find/create artist:\n{e}")
            return
        try:
            self.controller.add.add_entity(
                "ArtistInfluence",
                influencer_id=influencer.artist_id,
                influenced_id=self.artist.artist_id,
                description=self.new_influencer_desc_edit.text().strip() or None,
            )
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Could not add influence:\n{e}")
            return
        self._reload_and_refresh()
        self.new_influencer_edit.clear()
        self.new_influencer_desc_edit.clear()

    def _remove_influencer(self, row):
        influence_id = self.influencer_table.item(row, 0).data(Qt.UserRole)
        if influence_id is None:
            return
        try:
            self.controller.delete.delete_entity("ArtistInfluence", influence_id)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Could not remove influence:\n{e}")
            return
        self._reload_and_refresh()


# ══════════════════════════════════════════════════════════════════════════════
# Tab: Places & Awards
# ══════════════════════════════════════════════════════════════════════════════


class PlacesAwardsTab(QWidget):
    def __init__(self, controller, artist, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.artist = artist
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        splitter = QSplitter(Qt.Vertical)

        # ── Places ──────────────────────────────────────────────────────────
        places_grp = QGroupBox("Associated Places")
        pl_layout = QVBoxLayout(places_grp)
        self.places_table = _make_table(
            ["Place Name", "Association Type", "Place Type", "Region/Country"],
            editable=False,
        )
        pl_layout.addWidget(self.places_table)
        place_help = QLabel(
            "You can type a new place name — it will be created automatically if it doesn't exist yet."
        )
        place_help.setWordWrap(True)
        place_help.setStyleSheet("color: #888; font-size: 11px;")
        pl_layout.addWidget(place_help)
        pl_add_row = QHBoxLayout()
        self.new_place_edit = QLineEdit()
        self.new_place_edit.setPlaceholderText("Place name (new or existing)...")
        self.new_place_assoc_edit = QLineEdit()
        self.new_place_assoc_edit.setPlaceholderText(
            "Relationship (e.g. Birthplace, Hometown)..."
        )
        add_place_btn = QPushButton("Link Place")
        add_place_btn.clicked.connect(self._add_place)
        rm_place_btn = QPushButton("Unlink Selected")
        rm_place_btn.clicked.connect(
            lambda: _remove_selected_row(self, self.places_table, self._remove_place)
        )
        pl_add_row.addWidget(self.new_place_edit, 3)
        pl_add_row.addWidget(self.new_place_assoc_edit, 2)
        pl_add_row.addWidget(add_place_btn)
        pl_add_row.addWidget(rm_place_btn)
        pl_layout.addLayout(pl_add_row)
        splitter.addWidget(places_grp)

        # ── Awards ──────────────────────────────────────────────────────────
        awards_grp = QGroupBox("Awards")
        aw_layout = QVBoxLayout(awards_grp)
        self.awards_table = _make_table(
            ["Award Name", "Category", "Year"], editable=False
        )
        aw_layout.addWidget(self.awards_table)
        award_help = QLabel(
            "You can type a new award name — it will be created automatically if it doesn't exist yet."
        )
        award_help.setWordWrap(True)
        award_help.setStyleSheet("color: #888; font-size: 11px;")
        aw_layout.addWidget(award_help)
        aw_add_row = QHBoxLayout()
        self.new_award_edit = QLineEdit()
        self.new_award_edit.setPlaceholderText("Award name (new or existing)...")
        add_award_btn = QPushButton("Link Award")
        add_award_btn.clicked.connect(self._add_award)
        rm_award_btn = QPushButton("Unlink Selected")
        rm_award_btn.clicked.connect(
            lambda: _remove_selected_row(self, self.awards_table, self._remove_award)
        )
        aw_add_row.addWidget(self.new_award_edit, 3)
        aw_add_row.addWidget(add_award_btn)
        aw_add_row.addWidget(rm_award_btn)
        aw_layout.addLayout(aw_add_row)
        splitter.addWidget(awards_grp)

        layout.addWidget(splitter)

    def load(self, artist):
        self.artist = artist
        self._load_places()
        self._load_awards()

    def _load_places(self):
        self.places_table.setRowCount(0)
        assocs_loaded = False
        try:
            place_assocs = self.controller.get.get_all_entities(
                "PlaceAssociation",
                entity_id=self.artist.artist_id,
                entity_type="Artist",
            )
            if place_assocs is not None:
                for assoc in place_assocs:
                    if assoc.place is None:
                        continue
                    _append_row(
                        self.places_table,
                        [
                            assoc.place.place_name,
                            assoc.association_type or "",
                            assoc.place.place_type or "",
                            _parent_place_name(assoc.place),
                        ],
                        user_data=assoc.association_id,
                    )
                assocs_loaded = True
        except Exception as e:
            logger.debug(f"Could not load via PlaceAssociation entities: {e}")
        if not assocs_loaded:
            for place in getattr(self.artist, "places", []):
                _append_row(
                    self.places_table,
                    [
                        place.place_name,
                        "",
                        place.place_type or "",
                        _parent_place_name(place),
                    ],
                    user_data=place.place_id,
                )

    def _load_awards(self):
        self.awards_table.setRowCount(0)
        assocs_loaded = False
        try:
            award_assocs = self.controller.get.get_all_entities(
                "AwardAssociation",
                entity_id=self.artist.artist_id,
                entity_type="Artist",
            )
            if award_assocs is not None:
                for assoc in award_assocs:
                    if assoc.award is None:
                        continue
                    _append_row(
                        self.awards_table,
                        [
                            assoc.award.award_name,
                            assoc.award.award_category or "",
                            assoc.award.award_year or "",
                        ],
                        user_data=assoc.association_id,
                    )
                assocs_loaded = True
        except Exception as e:
            logger.debug(f"Could not load via AwardAssociation entities: {e}")
        if not assocs_loaded:
            for award in getattr(self.artist, "awards", []):
                _append_row(
                    self.awards_table,
                    [
                        award.award_name,
                        award.award_category or "",
                        award.award_year or "",
                    ],
                    user_data=award.award_id,
                )

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

    def _add_place(self):
        name = self.new_place_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "Validation", "Please enter a place name.")
            return
        association_type = self.new_place_assoc_edit.text().strip()
        if not association_type:
            QMessageBox.warning(
                self,
                "Validation",
                "Please enter the relationship type (e.g. Birthplace, Hometown).",
            )
            return
        try:
            place = self.controller.get.get_entity_object("Place", place_name=name)
            if isinstance(place, list):
                place = place[0] if place else None
            if place is None:
                place = self.controller.add.add_entity("Place", place_name=name)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Could not find/create place:\n{e}")
            return
        try:
            self.controller.add.add_entity(
                "PlaceAssociation",
                entity_id=self.artist.artist_id,
                entity_type="Artist",
                place_id=place.place_id,
                association_type=association_type,
            )
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Could not link place:\n{e}")
            return
        self._reload_and_refresh()
        self.new_place_edit.clear()
        self.new_place_assoc_edit.clear()

    def _remove_place(self, row):
        assoc_id = self.places_table.item(row, 0).data(Qt.UserRole)
        if assoc_id is None:
            return
        try:
            self.controller.delete.delete_entity("PlaceAssociation", assoc_id)
        except Exception:
            try:
                self.controller.delete.delete_entity(
                    "PlaceAssociation",
                    entity_id=self.artist.artist_id,
                    entity_type="Artist",
                    place_id=assoc_id,
                )
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Could not unlink place:\n{e}")
                return
        self._reload_and_refresh()

    def _add_award(self):
        name = self.new_award_edit.text().strip()
        if not name:
            return
        try:
            awards = self.controller.get.get_entity_object("Award", award_name=name)
            if isinstance(awards, list):
                award = awards[0] if awards else None
            else:
                award = awards
            if award is None:
                award = self.controller.add.add_entity("Award", award_name=name)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Could not find/create award:\n{e}")
            return
        try:
            self.controller.add.add_entity(
                "AwardAssociation",
                entity_id=self.artist.artist_id,
                entity_type="Artist",
                award_id=award.award_id,
            )
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Could not link award:\n{e}")
            return
        self._reload_and_refresh()
        self.new_award_edit.clear()

    def _remove_award(self, row):
        assoc_id = self.awards_table.item(row, 0).data(Qt.UserRole)
        if assoc_id is None:
            return
        try:
            self.controller.delete.delete_entity("AwardAssociation", assoc_id)
        except Exception:
            try:
                self.controller.delete.delete_entity(
                    "AwardAssociation",
                    entity_id=self.artist.artist_id,
                    entity_type="Artist",
                    award_id=assoc_id,
                )
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Could not unlink award:\n{e}")
                return
        self._reload_and_refresh()


# ══════════════════════════════════════════════════════════════════════════════
# Tab: Discography
# ══════════════════════════════════════════════════════════════════════════════


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


# ══════════════════════════════════════════════════════════════════════════════
# Tab: Advanced
# ══════════════════════════════════════════════════════════════════════════════


class AdvancedTab(QWidget):
    def __init__(self, controller, artist, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.artist = artist
        self._build_ui()

    def _build_ui(self):
        form = QFormLayout(self)

        self.mbid_edit = QLineEdit()
        self.mbid_edit.setPlaceholderText("MusicBrainz ID")
        form.addRow("MBID:", self.mbid_edit)

        self.wiki_edit = QLineEdit()
        self.wiki_edit.setPlaceholderText("https://en.wikipedia.org/...")
        form.addRow("Wikipedia:", self.wiki_edit)

        self.website_edit = QLineEdit()
        self.website_edit.setPlaceholderText("https://...")
        form.addRow("Website:", self.website_edit)

        self.is_fixed_check = QCheckBox("Mark metadata as complete")
        form.addRow("Metadata Complete:", self.is_fixed_check)

        self.artist_id_label = QLabel()
        self.artist_id_label.setStyleSheet("color: grey;")
        form.addRow("Artist ID (read-only):", self.artist_id_label)

    def load(self, artist):
        self.artist = artist
        self.mbid_edit.setText(artist.MBID or "")
        self.wiki_edit.setText(artist.wikipedia_link or "")
        self.website_edit.setText(artist.website_link or "")
        self.is_fixed_check.setChecked(bool(artist.is_fixed))
        self.artist_id_label.setText(str(artist.artist_id))

    def collect_changes(self):
        return dict(
            MBID=self.mbid_edit.text().strip() or None,
            wikipedia_link=self.wiki_edit.text().strip() or None,
            website_link=self.website_edit.text().strip() or None,
            is_fixed=1 if self.is_fixed_check.isChecked() else 0,
        )


# ══════════════════════════════════════════════════════════════════════════════
# Main dialog shell
# ══════════════════════════════════════════════════════════════════════════════


class ArtistEditor(QDialog):
    """
    Thin shell dialog that assembles the tab widgets and handles Save/Cancel.

    All UI logic, loading, and relationship editing lives inside the tab classes.
    Save collects changes from BasicTab, BiographyTab, and AdvancedTab and
    commits them in a single update_entity call.
    """

    def __init__(self, controller, artist, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.artist = artist

        self.setWindowTitle(f"Edit Artist: {artist.artist_name}")
        self.setMinimumSize(920, 680)
        # NonModal = dialog is fully independent; user can move it freely
        self.setWindowModality(Qt.NonModal)

        self._init_ui()
        self._load_all()

    def _init_ui(self):
        root = QVBoxLayout(self)
        root.setSpacing(8)

        hdr = QLabel(
            f"<b style='font-size:15px'>Editing: {self.artist.artist_name}</b>"
        )
        root.addWidget(hdr)

        # Instantiate tabs
        self.tab_basic = BasicTab(self.controller, self.artist)
        self.tab_biography = BiographyTab(self.controller, self.artist)
        self.tab_aliases = AliasesTab(self.controller, self.artist)
        self.tab_members = MembersTab(self.controller, self.artist)
        self.tab_influences = InfluencesTab(self.controller, self.artist)
        self.tab_places_awards = PlacesAwardsTab(self.controller, self.artist)
        self.tab_discography = DiscographyTab(self.controller, self.artist)
        self.tab_advanced = AdvancedTab(self.controller, self.artist)

        # Wire isgroup checkbox -> MembersTab so the right panel shows immediately
        self.tab_basic.isgroup_check.toggled.connect(self.tab_members.update_visibility)

        tabs = QTabWidget()
        tabs.addTab(self.tab_basic, "Basic")
        tabs.addTab(self.tab_biography, "Biography")
        tabs.addTab(self.tab_aliases, "Aliases")
        tabs.addTab(self.tab_members, "Members")
        tabs.addTab(self.tab_influences, "Influences")
        tabs.addTab(self.tab_places_awards, "Places & Awards")
        tabs.addTab(self.tab_discography, "Discography")
        tabs.addTab(self.tab_advanced, "Advanced")
        root.addWidget(tabs)

        btns = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        btns.accepted.connect(self._save)
        btns.rejected.connect(self.reject)
        root.addWidget(btns)

    def _load_all(self):
        self.tab_basic.load(self.artist)
        self.tab_biography.load(self.artist)
        self.tab_aliases.load(self.artist)
        self.tab_members.load(self.artist)
        self.tab_influences.load(self.artist)
        self.tab_places_awards.load(self.artist)
        self.tab_discography.load(self.artist)
        self.tab_advanced.load(self.artist)

    def _save(self):
        name = self.tab_basic.name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "Validation", "Artist name cannot be empty.")
            return

        kwargs = {}
        kwargs.update(self.tab_basic.collect_changes())
        kwargs.update(self.tab_biography.collect_changes())
        kwargs.update(self.tab_advanced.collect_changes())

        try:
            self.controller.update.update_entity(
                "Artist", self.artist.artist_id, **kwargs
            )
            logger.info(f"Saved artist {self.artist.artist_id} '{name}'")
        except Exception as e:
            QMessageBox.critical(self, "Save Error", f"Could not save artist:\n{e}")
            logger.error(f"Failed to save artist {self.artist.artist_id}: {e}")
            return

        self.accept()
