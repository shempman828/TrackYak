"""
artist_edit_dialog.py

A comprehensive artist editing dialog covering all fields and relationships
from the Artist ORM model in db_tables.py:

  Artist fields:
    artist_name, isgroup, artist_type, gender, begin_year/month/day,
    end_year/month/day, biography, MBID, wikipedia_link, website_link,
    profile_pic_path, is_fixed

  Relationships:
    aliases        (ArtistAlias)      → Aliases tab  (uses ArtistAliasDialog)
    places         (Place)            → Places & Awards tab
    awards         (Award)            → Places & Awards tab
    influencer_relations / influenced_relations (ArtistInfluence) → Influences tab
    group_memberships / member_memberships (GroupMembership)      → Members tab
    album_roles    (AlbumRoleAssociation)                         → Discography tab (read-only)
    track_roles    (TrackArtistRole)                              → Discography tab (read-only)

Usage:
    dialog = ArtistEditor(controller, artist)
    if dialog.exec() == QDialog.Accepted:
        pass  # changes already saved
"""

import logging

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
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
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from artist_alias_dialog import ArtistAliasDialog

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────

ARTIST_TYPES = ["", "Person", "Band", "Orchestra", "Choir"]
GENDERS = ["", "Male", "Female", "Non-binary", "Other", "Unknown"]


# ══════════════════════════════════════════════════════════════════════════════
# Helper: compact SpinBox that shows 0 as "–" (unset)
# ══════════════════════════════════════════════════════════════════════════════


class OptionalSpinBox(QSpinBox):
    """SpinBox where 0 means 'not set'. Displays blank for 0."""

    def __init__(self, minimum=0, maximum=9999, parent=None):
        super().__init__(parent)
        self.setRange(minimum, maximum)
        self.setSpecialValueText("–")  # shown when value == minimum (0)
        self.setValue(0)

    def get_value_or_none(self):
        v = self.value()
        return None if v == self.minimum() else v

    def set_from_db(self, val):
        self.setValue(int(val) if val is not None else 0)


# ══════════════════════════════════════════════════════════════════════════════
# Main editor
# ══════════════════════════════════════════════════════════════════════════════


class ArtistEditor(QDialog):
    """
    Tabbed dialog for editing every field and relationship of an Artist.

    Tabs
    ----
    Basic          – core identity fields (name, type, dates, gender, MBID …)
    Biography      – long-form biography textarea
    Aliases        – alias management via ArtistAliasDialog button
    Members        – group_memberships / member_memberships (GroupMembership)
    Influences     – influencer_relations / influenced_relations (ArtistInfluence)
    Places & Awards– artist.places, artist.awards
    Discography    – read-only album/track credit summary
    Advanced       – profile_pic_path, is_fixed, links, raw IDs
    """

    def __init__(self, controller, artist, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.artist = artist

        self.setWindowTitle(f"Edit Artist: {artist.artist_name}")
        self.setMinimumSize(920, 680)
        self.setModal(True)

        self._init_ui()
        self._load_all()

    # ── UI construction ────────────────────────────────────────────────────────

    def _init_ui(self):
        root = QVBoxLayout(self)
        root.setSpacing(8)

        # Header bar
        hdr = QLabel(
            f"<b style='font-size:15px'>✏ Editing: {self.artist.artist_name}</b>"
        )
        root.addWidget(hdr)

        # Tab widget
        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_basic_tab(), "Basic")
        self.tabs.addTab(self._build_biography_tab(), "Biography")
        self.tabs.addTab(self._build_aliases_tab(), "Aliases")
        self.tabs.addTab(self._build_members_tab(), "Members")
        self.tabs.addTab(self._build_influences_tab(), "Influences")
        self.tabs.addTab(self._build_places_awards_tab(), "Places && Awards")
        self.tabs.addTab(self._build_discography_tab(), "Discography")
        self.tabs.addTab(self._build_advanced_tab(), "Advanced")
        root.addWidget(self.tabs)

        # Dialog buttons
        btns = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        btns.accepted.connect(self._save)
        btns.rejected.connect(self.reject)
        root.addWidget(btns)

    # ── Tab: Basic ─────────────────────────────────────────────────────────────

    def _build_basic_tab(self):
        w = QWidget()
        layout = QHBoxLayout(w)

        # Left: form fields
        form_widget = QWidget()
        form = QFormLayout(form_widget)
        form.setLabelAlignment(Qt.AlignRight)
        form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("Artist name")
        form.addRow("Name *:", self.name_edit)

        self.artist_type_combo = QComboBox()
        self.artist_type_combo.addItems(ARTIST_TYPES)
        form.addRow("Type:", self.artist_type_combo)

        self.isgroup_check = QCheckBox("This name represents a group / band")
        form.addRow("Is Group:", self.isgroup_check)

        self.gender_combo = QComboBox()
        self.gender_combo.addItems(GENDERS)
        form.addRow("Gender:", self.gender_combo)

        # Begin date
        begin_box = QHBoxLayout()
        self.begin_year_spin = OptionalSpinBox(0, 2100)
        self.begin_year_spin.setToolTip("Year born / founded (0 = unknown)")
        self.begin_month_spin = OptionalSpinBox(0, 12)
        self.begin_month_spin.setToolTip("Month (0 = unknown)")
        self.begin_day_spin = OptionalSpinBox(0, 31)
        self.begin_day_spin.setToolTip("Day (0 = unknown)")
        begin_box.addWidget(QLabel("Year"))
        begin_box.addWidget(self.begin_year_spin)
        begin_box.addWidget(QLabel("Month"))
        begin_box.addWidget(self.begin_month_spin)
        begin_box.addWidget(QLabel("Day"))
        begin_box.addWidget(self.begin_day_spin)
        begin_box.addStretch()
        form.addRow("Born / Founded:", begin_box)

        # End date
        end_box = QHBoxLayout()
        self.end_year_spin = OptionalSpinBox(0, 2100)
        self.end_year_spin.setToolTip("Year died / disbanded (0 = still active)")
        self.end_month_spin = OptionalSpinBox(0, 12)
        self.end_day_spin = OptionalSpinBox(0, 31)
        end_box.addWidget(QLabel("Year"))
        end_box.addWidget(self.end_year_spin)
        end_box.addWidget(QLabel("Month"))
        end_box.addWidget(self.end_month_spin)
        end_box.addWidget(QLabel("Day"))
        end_box.addWidget(self.end_day_spin)
        end_box.addStretch()
        form.addRow("Died / Disbanded:", end_box)

        layout.addWidget(form_widget, 1)

        # Right: profile picture preview
        pic_grp = QGroupBox("Profile Picture")
        pic_layout = QVBoxLayout(pic_grp)
        self.pic_label = QLabel()
        self.pic_label.setFixedSize(180, 180)
        self.pic_label.setAlignment(Qt.AlignCenter)
        self.pic_label.setStyleSheet("border: 1px solid #888; background: #222;")
        self.pic_label.setText("No Image")
        pic_layout.addWidget(self.pic_label)
        self.pic_path_edit = QLineEdit()
        self.pic_path_edit.setPlaceholderText("Image path…")
        self.pic_path_edit.textChanged.connect(self._refresh_pic_preview)
        pic_layout.addWidget(self.pic_path_edit)
        browse_btn = QPushButton("Browse…")
        browse_btn.clicked.connect(self._browse_pic)
        pic_layout.addWidget(browse_btn)
        layout.addWidget(pic_grp)

        return w

    # ── Tab: Biography ────────────────────────────────────────────────────────

    def _build_biography_tab(self):
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.addWidget(QLabel("Biography:"))
        self.bio_edit = QTextEdit()
        self.bio_edit.setPlaceholderText("Enter artist biography…")
        layout.addWidget(self.bio_edit)
        return w

    # ── Tab: Aliases ──────────────────────────────────────────────────────────

    def _build_aliases_tab(self):
        w = QWidget()
        layout = QVBoxLayout(w)

        info = QLabel(
            "Aliases allow an artist to be found under multiple names.\n"
            "Use the button below to open the full alias manager."
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        self.aliases_summary_label = QLabel()
        self.aliases_summary_label.setWordWrap(True)
        layout.addWidget(self.aliases_summary_label)

        open_btn = QPushButton("🏷  Manage Aliases…")
        open_btn.setFixedHeight(36)
        open_btn.clicked.connect(self._open_alias_dialog)
        layout.addWidget(open_btn, alignment=Qt.AlignLeft)
        layout.addStretch()
        return w

    # ── Tab: Members ──────────────────────────────────────────────────────────

    def _build_members_tab(self):
        """GroupMembership – either members of this group, or groups this person belongs to."""
        w = QWidget()
        layout = QVBoxLayout(w)

        splitter = QSplitter(Qt.Vertical)

        # ─ Group Members (when this artist IS the group) ─
        grp_members = QGroupBox("Members of this Group")
        gm_layout = QVBoxLayout(grp_members)
        self.members_table = self._make_table(
            ["Member", "Role", "Start Year", "End Year", "Current"], editable=False
        )
        gm_layout.addWidget(self.members_table)

        add_member_row = QHBoxLayout()
        self.new_member_edit = QLineEdit()
        self.new_member_edit.setPlaceholderText("Member artist name…")
        self.new_member_role_edit = QLineEdit()
        self.new_member_role_edit.setPlaceholderText("Role (e.g. Guitarist)")
        self.new_member_start_spin = OptionalSpinBox(0, 2100)
        self.new_member_end_spin = OptionalSpinBox(0, 2100)
        self.new_member_current_check = QCheckBox("Current")
        add_member_btn = QPushButton("Add Member")
        add_member_btn.clicked.connect(self._add_member)
        add_member_row.addWidget(self.new_member_edit, 2)
        add_member_row.addWidget(self.new_member_role_edit, 2)
        add_member_row.addWidget(QLabel("Start"))
        add_member_row.addWidget(self.new_member_start_spin)
        add_member_row.addWidget(QLabel("End"))
        add_member_row.addWidget(self.new_member_end_spin)
        add_member_row.addWidget(self.new_member_current_check)
        add_member_row.addWidget(add_member_btn)
        gm_layout.addLayout(add_member_row)

        rm_member_btn = QPushButton("Remove Selected Member")
        rm_member_btn.clicked.connect(
            lambda: self._remove_selected_row(self.members_table, self._remove_member)
        )
        gm_layout.addWidget(rm_member_btn, alignment=Qt.AlignLeft)
        splitter.addWidget(grp_members)

        # ─ Group Affiliations (when this artist IS a member) ─
        grp_affil = QGroupBox("Groups This Artist Belongs To")
        ga_layout = QVBoxLayout(grp_affil)
        self.affiliations_table = self._make_table(
            ["Group", "Role", "Start Year", "End Year", "Current"], editable=False
        )
        ga_layout.addWidget(self.affiliations_table)
        splitter.addWidget(grp_affil)

        layout.addWidget(splitter)
        return w

    # ── Tab: Influences ───────────────────────────────────────────────────────

    def _build_influences_tab(self):
        w = QWidget()
        layout = QVBoxLayout(w)
        splitter = QSplitter(Qt.Vertical)

        # Influences (this artist influenced others)
        inf_grp = QGroupBox("This Artist Influenced")
        inf_layout = QVBoxLayout(inf_grp)
        self.influenced_table = self._make_table(
            ["Influenced Artist", "Description"], editable=False
        )
        inf_layout.addWidget(self.influenced_table)

        add_inf_row = QHBoxLayout()
        self.new_influenced_edit = QLineEdit()
        self.new_influenced_edit.setPlaceholderText("Artist name…")
        self.new_inf_desc_edit = QLineEdit()
        self.new_inf_desc_edit.setPlaceholderText("Description (optional)")
        add_inf_btn = QPushButton("Add")
        add_inf_btn.clicked.connect(self._add_influenced)
        add_inf_row.addWidget(self.new_influenced_edit, 2)
        add_inf_row.addWidget(self.new_inf_desc_edit, 2)
        add_inf_row.addWidget(add_inf_btn)
        inf_layout.addLayout(add_inf_row)
        rm_inf_btn = QPushButton("Remove Selected")
        rm_inf_btn.clicked.connect(
            lambda: self._remove_selected_row(
                self.influenced_table, self._remove_influenced
            )
        )
        inf_layout.addWidget(rm_inf_btn, alignment=Qt.AlignLeft)
        splitter.addWidget(inf_grp)

        # Influencers (artists that influenced this one)
        infl_grp = QGroupBox("Influenced By")
        infl_layout = QVBoxLayout(infl_grp)
        self.influencer_table = self._make_table(
            ["Influencing Artist", "Description"], editable=False
        )
        infl_layout.addWidget(self.influencer_table)

        add_infl_row = QHBoxLayout()
        self.new_influencer_edit = QLineEdit()
        self.new_influencer_edit.setPlaceholderText("Artist name…")
        self.new_influencer_desc_edit = QLineEdit()
        self.new_influencer_desc_edit.setPlaceholderText("Description (optional)")
        add_infl_btn = QPushButton("Add")
        add_infl_btn.clicked.connect(self._add_influencer)
        add_infl_row.addWidget(self.new_influencer_edit, 2)
        add_infl_row.addWidget(self.new_influencer_desc_edit, 2)
        add_infl_row.addWidget(add_infl_btn)
        infl_layout.addLayout(add_infl_row)
        rm_infl_btn = QPushButton("Remove Selected")
        rm_infl_btn.clicked.connect(
            lambda: self._remove_selected_row(
                self.influencer_table, self._remove_influencer
            )
        )
        infl_layout.addWidget(rm_infl_btn, alignment=Qt.AlignLeft)
        splitter.addWidget(infl_grp)

        layout.addWidget(splitter)
        return w

    # ── Tab: Places & Awards ──────────────────────────────────────────────────

    def _build_places_awards_tab(self):
        w = QWidget()
        layout = QVBoxLayout(w)
        splitter = QSplitter(Qt.Vertical)

        # Places
        places_grp = QGroupBox("Associated Places")
        pl_layout = QVBoxLayout(places_grp)
        self.places_table = self._make_table(
            ["Place Name", "Type", "Country"], editable=False
        )
        pl_layout.addWidget(self.places_table)

        pl_add_row = QHBoxLayout()
        self.new_place_edit = QLineEdit()
        self.new_place_edit.setPlaceholderText("Place name (must already exist)…")
        add_place_btn = QPushButton("Link Place")
        add_place_btn.clicked.connect(self._add_place)
        rm_place_btn = QPushButton("Unlink Selected")
        rm_place_btn.clicked.connect(
            lambda: self._remove_selected_row(self.places_table, self._remove_place)
        )
        pl_add_row.addWidget(self.new_place_edit, 3)
        pl_add_row.addWidget(add_place_btn)
        pl_add_row.addWidget(rm_place_btn)
        pl_layout.addLayout(pl_add_row)
        splitter.addWidget(places_grp)

        # Awards
        awards_grp = QGroupBox("Awards")
        aw_layout = QVBoxLayout(awards_grp)
        self.awards_table = self._make_table(
            ["Award Name", "Category", "Year"], editable=False
        )
        aw_layout.addWidget(self.awards_table)

        aw_add_row = QHBoxLayout()
        self.new_award_edit = QLineEdit()
        self.new_award_edit.setPlaceholderText("Award name (must already exist)…")
        add_award_btn = QPushButton("Link Award")
        add_award_btn.clicked.connect(self._add_award)
        rm_award_btn = QPushButton("Unlink Selected")
        rm_award_btn.clicked.connect(
            lambda: self._remove_selected_row(self.awards_table, self._remove_award)
        )
        aw_add_row.addWidget(self.new_award_edit, 3)
        aw_add_row.addWidget(add_award_btn)
        aw_add_row.addWidget(rm_award_btn)
        aw_layout.addLayout(aw_add_row)
        splitter.addWidget(awards_grp)

        layout.addWidget(splitter)
        return w

    # ── Tab: Discography (read-only) ──────────────────────────────────────────

    def _build_discography_tab(self):
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.addWidget(
            QLabel("<i>Read-only summary of this artist's album and track credits.</i>")
        )

        albums_grp = QGroupBox("Album Credits")
        alb_layout = QVBoxLayout(albums_grp)
        self.albums_table = self._make_table(["Album", "Role", "Year"], editable=False)
        alb_layout.addWidget(self.albums_table)
        layout.addWidget(albums_grp)

        tracks_grp = QGroupBox("Track Credits")
        trk_layout = QVBoxLayout(tracks_grp)
        self.tracks_table = self._make_table(["Track", "Role", "Album"], editable=False)
        trk_layout.addWidget(self.tracks_table)
        layout.addWidget(tracks_grp)
        return w

    # ── Tab: Advanced ─────────────────────────────────────────────────────────

    def _build_advanced_tab(self):
        w = QWidget()
        form = QFormLayout(w)
        form.setLabelAlignment(Qt.AlignRight)

        self.mbid_edit = QLineEdit()
        self.mbid_edit.setPlaceholderText("xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx")
        form.addRow("MusicBrainz ID:", self.mbid_edit)

        self.wiki_edit = QLineEdit()
        self.wiki_edit.setPlaceholderText("https://en.wikipedia.org/wiki/…")
        form.addRow("Wikipedia Link:", self.wiki_edit)

        self.website_edit = QLineEdit()
        self.website_edit.setPlaceholderText("https://…")
        form.addRow("Official Website:", self.website_edit)

        self.is_fixed_check = QCheckBox("Mark profile as complete / fixed")
        self.is_fixed_check.setToolTip(
            "Tick when you are satisfied that this artist's metadata is complete."
        )
        form.addRow("Metadata Complete:", self.is_fixed_check)

        # Read-only artist_id
        self.artist_id_label = QLabel()
        self.artist_id_label.setStyleSheet("color: grey;")
        form.addRow("Artist ID (read-only):", self.artist_id_label)

        return w

    # ── Utility ────────────────────────────────────────────────────────────────

    @staticmethod
    def _make_table(headers, editable=True):
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

    @staticmethod
    def _set_item(table, row, col, text, user_data=None):
        item = QTableWidgetItem(str(text) if text is not None else "")
        if user_data is not None:
            item.setData(Qt.UserRole, user_data)
        table.setItem(row, col, item)

    def _append_row(self, table, values, user_data=None):
        row = table.rowCount()
        table.insertRow(row)
        for col, val in enumerate(values):
            self._set_item(table, row, col, val, user_data if col == 0 else None)
        return row

    # ── Data loading ───────────────────────────────────────────────────────────

    def _load_all(self):
        a = self.artist
        # Basic
        self.name_edit.setText(a.artist_name or "")
        idx = self.artist_type_combo.findText(a.artist_type or "")
        self.artist_type_combo.setCurrentIndex(max(idx, 0))
        self.isgroup_check.setChecked(bool(a.isgroup))
        g_idx = self.gender_combo.findText(a.gender or "")
        self.gender_combo.setCurrentIndex(max(g_idx, 0))
        self.begin_year_spin.set_from_db(a.begin_year)
        self.begin_month_spin.set_from_db(a.begin_month)
        self.begin_day_spin.set_from_db(a.begin_day)
        self.end_year_spin.set_from_db(a.end_year)
        self.end_month_spin.set_from_db(a.end_month)
        self.end_day_spin.set_from_db(a.end_day)
        self.pic_path_edit.setText(a.profile_pic_path or "")

        # Biography
        self.bio_edit.setPlainText(a.biography or "")

        # Advanced
        self.mbid_edit.setText(a.MBID or "")
        self.wiki_edit.setText(a.wikipedia_link or "")
        self.website_edit.setText(a.website_link or "")
        self.is_fixed_check.setChecked(bool(a.is_fixed))
        self.artist_id_label.setText(str(a.artist_id))

        # Relationships
        self._load_aliases_summary()
        self._load_members()
        self._load_influences()
        self._load_places()
        self._load_awards()
        self._load_discography()

    def _load_aliases_summary(self):
        aliases = getattr(self.artist, "aliases", [])
        if aliases:
            names = ", ".join(a.alias_name for a in aliases)
            self.aliases_summary_label.setText(
                f"<b>{len(aliases)} alias(es):</b> {names}"
            )
        else:
            self.aliases_summary_label.setText("<i>No aliases yet.</i>")

    def _load_members(self):
        self.members_table.setRowCount(0)
        for m in getattr(self.artist, "group_memberships", []):
            member = m.member
            if member is None:
                continue
            self._append_row(
                self.members_table,
                [
                    member.artist_name,
                    m.role or "",
                    m.active_start_year or "",
                    m.active_end_year or "",
                    "Yes" if m.is_current else "No",
                ],
                user_data=(m.group_id, m.member_id),
            )

        self.affiliations_table.setRowCount(0)
        for m in getattr(self.artist, "member_memberships", []):
            group = m.group
            if group is None:
                continue
            self._append_row(
                self.affiliations_table,
                [
                    group.artist_name,
                    m.role or "",
                    m.active_start_year or "",
                    m.active_end_year or "",
                    "Yes" if m.is_current else "No",
                ],
            )

    def _load_influences(self):
        self.influenced_table.setRowCount(0)
        for rel in getattr(self.artist, "influencer_relations", []):
            influenced = rel.influenced
            if influenced is None:
                continue
            self._append_row(
                self.influenced_table,
                [influenced.artist_name, rel.description or ""],
                user_data=rel.influence_id if hasattr(rel, "influence_id") else None,
            )

        self.influencer_table.setRowCount(0)
        for rel in getattr(self.artist, "influenced_relations", []):
            influencer = rel.influencer
            if influencer is None:
                continue
            self._append_row(
                self.influencer_table,
                [influencer.artist_name, rel.description or ""],
                user_data=rel.influence_id if hasattr(rel, "influence_id") else None,
            )

    def _load_places(self):
        self.places_table.setRowCount(0)
        for place in getattr(self.artist, "places", []):
            self._append_row(
                self.places_table,
                [
                    place.place_name,
                    place.place_type or "",
                    self._parent_place_name(place),
                ],
                user_data=place.place_id,
            )

    def _load_awards(self):
        self.awards_table.setRowCount(0)
        for award in getattr(self.artist, "awards", []):
            self._append_row(
                self.awards_table,
                [award.award_name, award.award_category or "", award.award_year or ""],
                user_data=award.award_id,
            )

    def _load_discography(self):
        self.albums_table.setRowCount(0)
        for assoc in getattr(self.artist, "album_roles", []):
            album = assoc.album
            role = assoc.role
            if album is None:
                continue
            self._append_row(
                self.albums_table,
                [
                    album.album_name,
                    role.role_name if role else "",
                    album.release_year or "",
                ],
            )

        self.tracks_table.setRowCount(0)
        for assoc in getattr(self.artist, "track_roles", []):
            track = assoc.track
            role = assoc.role
            if track is None:
                continue
            album_name = track.album.album_name if track.album else ""
            self._append_row(
                self.tracks_table,
                [track.track_name, role.role_name if role else "", album_name],
            )

    # ── Actions ────────────────────────────────────────────────────────────────

    def _open_alias_dialog(self):
        dlg = ArtistAliasDialog(self.controller, self.artist, parent=self)
        dlg.exec()
        # Refresh summary after dialog closes
        # Re-fetch artist to get updated aliases
        try:
            refreshed = self.controller.get.get_entity_object(
                "Artist", artist_id=self.artist.artist_id
            )
            if refreshed:
                self.artist = refreshed
        except Exception:
            pass
        self._load_aliases_summary()

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

    # ─ Members ─────────────────────────────────────────

    def _add_member(self):
        name = self.new_member_edit.text().strip()
        if not name:
            QMessageBox.warning(
                self, "Validation", "Please enter a member artist name."
            )
            return
        # Find or create member artist
        try:
            members = self.controller.get.get_entity_object("Artist", artist_name=name)
            if members:
                member = members[0] if isinstance(members, list) else members
            else:
                member = self.controller.add.add_entity("Artist", artist_name=name)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Could not find/create artist:\n{e}")
            return

        try:
            self.controller.add.add_entity(
                "GroupMembership",
                group_id=self.artist.artist_id,
                member_id=member.artist_id,
                role=self.new_member_role_edit.text().strip() or None,
                active_start_year=self.new_member_start_spin.get_value_or_none(),
                active_end_year=self.new_member_end_spin.get_value_or_none(),
                is_current=1 if self.new_member_current_check.isChecked() else 0,
            )
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Could not add member:\n{e}")
            return

        self._reload_artist()
        self._load_members()
        # Clear fields
        self.new_member_edit.clear()
        self.new_member_role_edit.clear()
        self.new_member_start_spin.setValue(0)
        self.new_member_end_spin.setValue(0)
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
        self._reload_artist()
        self._load_members()

    # ─ Influences ──────────────────────────────────────

    def _add_influenced(self):
        self._add_influence_rel(
            influencer_id=self.artist.artist_id,
            new_artist_name=self.new_influenced_edit.text().strip(),
            description=self.new_inf_desc_edit.text().strip(),
            is_influencer=True,
        )
        self.new_influenced_edit.clear()
        self.new_inf_desc_edit.clear()

    def _add_influencer(self):
        self._add_influence_rel(
            influenced_id=self.artist.artist_id,
            new_artist_name=self.new_influencer_edit.text().strip(),
            description=self.new_influencer_desc_edit.text().strip(),
            is_influencer=False,
        )
        self.new_influencer_edit.clear()
        self.new_influencer_desc_edit.clear()

    def _add_influence_rel(
        self,
        new_artist_name,
        description,
        is_influencer,
        influencer_id=None,
        influenced_id=None,
    ):
        if not new_artist_name:
            QMessageBox.warning(self, "Validation", "Please enter an artist name.")
            return
        try:
            artists = self.controller.get.get_entity_object(
                "Artist", artist_name=new_artist_name
            )
            if artists:
                other = artists[0] if isinstance(artists, list) else artists
            else:
                other = self.controller.add.add_entity(
                    "Artist", artist_name=new_artist_name
                )
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Could not find/create artist:\n{e}")
            return

        try:
            if is_influencer:
                self.controller.add.add_entity(
                    "ArtistInfluence",
                    influencer_id=self.artist.artist_id,
                    influenced_id=other.artist_id,
                    description=description or None,
                )
            else:
                self.controller.add.add_entity(
                    "ArtistInfluence",
                    influencer_id=other.artist_id,
                    influenced_id=self.artist.artist_id,
                    description=description or None,
                )
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Could not add influence:\n{e}")
            return

        self._reload_artist()
        self._load_influences()

    def _remove_influenced(self, row):
        influence_id = self.influenced_table.item(row, 0).data(Qt.UserRole)
        if influence_id is None:
            return
        try:
            self.controller.delete.delete_entity("ArtistInfluence", influence_id)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Could not remove influence:\n{e}")
            return
        self._reload_artist()
        self._load_influences()

    def _remove_influencer(self, row):
        influence_id = self.influencer_table.item(row, 0).data(Qt.UserRole)
        if influence_id is None:
            return
        try:
            self.controller.delete.delete_entity("ArtistInfluence", influence_id)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Could not remove influence:\n{e}")
            return
        self._reload_artist()
        self._load_influences()

    # ─ Places ──────────────────────────────────────────

    def _add_place(self):
        name = self.new_place_edit.text().strip()
        if not name:
            return
        try:
            places = self.controller.get.get_entity_object("Place", place_name=name)
            place = (
                (places[0] if isinstance(places, list) else places) if places else None
            )
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Could not find place:\n{e}")
            return

        if place is None:
            QMessageBox.warning(
                self,
                "Not Found",
                f"No place named '{name}' exists.\nPlease create it in the Places view first.",
            )
            return

        try:
            self.controller.add.add_entity(
                "PlaceAssociation",
                entity_id=self.artist.artist_id,
                entity_type="Artist",
                place_id=place.place_id,
            )
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Could not link place:\n{e}")
            return

        self._reload_artist()
        self._load_places()
        self.new_place_edit.clear()

    def _remove_place(self, row):
        place_id = self.places_table.item(row, 0).data(Qt.UserRole)
        if place_id is None:
            return
        try:
            self.controller.delete.delete_entity(
                "PlaceAssociation",
                entity_id=self.artist.artist_id,
                entity_type="Artist",
                place_id=place_id,
            )
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Could not unlink place:\n{e}")
            return
        self._reload_artist()
        self._load_places()

    # ─ Awards ──────────────────────────────────────────

    def _add_award(self):
        name = self.new_award_edit.text().strip()
        if not name:
            return
        try:
            awards = self.controller.get.get_entity_object("Award", award_name=name)
            award = (
                (awards[0] if isinstance(awards, list) else awards) if awards else None
            )
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Could not find award:\n{e}")
            return

        if award is None:
            QMessageBox.warning(
                self,
                "Not Found",
                f"No award named '{name}' exists.\nPlease create it in the Awards view first.",
            )
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

        self._reload_artist()
        self._load_awards()
        self.new_award_edit.clear()

    def _remove_award(self, row):
        award_id = self.awards_table.item(row, 0).data(Qt.UserRole)
        if award_id is None:
            return
        try:
            self.controller.delete.delete_entity(
                "AwardAssociation",
                entity_id=self.artist.artist_id,
                entity_type="Artist",
                award_id=award_id,
            )
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Could not unlink award:\n{e}")
            return
        self._reload_artist()
        self._load_awards()

    # ─ Shared helpers ──────────────────────────────────

    def _remove_selected_row(self, table, remove_fn):
        rows = table.selectionModel().selectedRows()
        if not rows:
            QMessageBox.information(self, "No Selection", "Please select a row first.")
            return
        remove_fn(rows[0].row())

    def _reload_artist(self):
        """Refresh the artist ORM object from the database."""
        try:
            refreshed = self.controller.get.get_entity_object(
                "Artist", artist_id=self.artist.artist_id
            )
            if refreshed:
                self.artist = refreshed
        except Exception as e:
            logger.warning(f"Could not reload artist: {e}")

    @staticmethod
    def _parent_place_name(place):
        if place.parent_id and hasattr(place, "parent") and place.parent:
            return place.parent.place_name
        return ""

    # ── Save ───────────────────────────────────────────────────────────────────

    def _save(self):
        name = self.name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "Validation", "Artist name cannot be empty.")
            return

        kwargs = dict(
            artist_name=name,
            artist_type=self.artist_type_combo.currentText() or None,
            isgroup=1 if self.isgroup_check.isChecked() else 0,
            gender=self.gender_combo.currentText() or None,
            begin_year=self.begin_year_spin.get_value_or_none(),
            begin_month=self.begin_month_spin.get_value_or_none(),
            begin_day=self.begin_day_spin.get_value_or_none(),
            end_year=self.end_year_spin.get_value_or_none(),
            end_month=self.end_month_spin.get_value_or_none(),
            end_day=self.end_day_spin.get_value_or_none(),
            biography=self.bio_edit.toPlainText().strip() or None,
            profile_pic_path=self.pic_path_edit.text().strip() or None,
            MBID=self.mbid_edit.text().strip() or None,
            wikipedia_link=self.wiki_edit.text().strip() or None,
            website_link=self.website_edit.text().strip() or None,
            is_fixed=1 if self.is_fixed_check.isChecked() else 0,
        )

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
