"""
artist_edit_dialog.py

A comprehensive artist editing dialog covering all fields and relationships
from the Artist ORM model in db_tables.py.
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

# Suggestions for artist type autocomplete (user can still type anything)
ARTIST_TYPE_SUGGESTIONS = [
    "Person",
    "Band",
    "Orchestra",
    "Choir",
    "Ensemble",
]

GENDERS = ["", "Male", "Female", "Non-binary", "Other", "Unknown"]


# ══════════════════════════════════════════════════════════════════════════════
# Helper: compact integer-only QLineEdit that returns None when empty
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
    Discography    – read-only album/track credit summary (excluding Primary Artist album credits)
    Advanced       – profile_pic_path, is_fixed, links, raw IDs
    """

    def __init__(self, controller, artist, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.artist = artist

        self.setWindowTitle(f"Edit Artist: {artist.artist_name}")
        self.setMinimumSize(920, 680)
        # WindowModal = user can still move/interact with the main app window
        self.setWindowModality(Qt.WindowModal)

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
        self.tabs.addTab(self._build_places_awards_tab(), "Places & Awards")
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

        # Artist type: free-text with autocomplete suggestions
        self.artist_type_edit = QLineEdit()
        self.artist_type_edit.setPlaceholderText("e.g. Person, Band, Orchestra…")
        self.artist_type_edit.setToolTip(
            "Type any value. Common types are suggested as you type."
        )
        self._artist_type_completer = QCompleter(ARTIST_TYPE_SUGGESTIONS, self)
        self._artist_type_completer.setCaseSensitivity(Qt.CaseInsensitive)
        self._artist_type_completer.setCompletionMode(QCompleter.PopupCompletion)
        self.artist_type_edit.setCompleter(self._artist_type_completer)
        form.addRow("Type:", self.artist_type_edit)

        self.isgroup_check = QCheckBox("This name represents a group / band")
        self.isgroup_check.toggled.connect(self._on_isgroup_changed)
        form.addRow("Is Group:", self.isgroup_check)

        self.gender_combo = QComboBox()
        self.gender_combo.addItems(GENDERS)
        form.addRow("Gender:", self.gender_combo)

        # ── Begin date (label updated dynamically) ──
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
        # Store the form row label so we can update it
        self.begin_date_label = QLabel("Born / Founded:")
        form.addRow(self.begin_date_label, begin_box)

        # ── Currently Active toggle ──
        self.is_active_check = QCheckBox("Alive / Active")
        self.is_active_check.setToolTip(
            "Check this if the artist is still active. "
            "Unchecking enables the end date fields below."
        )
        self.is_active_check.toggled.connect(self._on_active_toggled)
        form.addRow("Status:", self.is_active_check)

        # ── End date ──
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

    def _on_isgroup_changed(self, is_group: bool):
        """Update date labels when group status changes."""
        if is_group:
            self.begin_date_label.setText("Founded:")
            self.end_date_label.setText("Disbanded:")
            self.is_active_check.setText("Alive / Active (still together)")
        else:
            self.begin_date_label.setText("Born:")
            self.end_date_label.setText("Died:")
            self.is_active_check.setText("Currently alive / active")
        self._update_members_tab_visibility()

    def _on_active_toggled(self, is_active: bool):
        """Enable/disable end date fields based on active status."""
        self.end_year_edit.setEnabled(not is_active)
        self.end_month_edit.setEnabled(not is_active)
        self.end_day_edit.setEnabled(not is_active)
        if is_active:
            self.end_year_edit.clear()
            self.end_month_edit.clear()
            self.end_day_edit.clear()

    def _update_members_tab_visibility(self):
        """Show/hide sections in Members tab based on isgroup."""
        is_group = self.isgroup_check.isChecked()
        if hasattr(self, "_members_group_widget"):
            self._members_group_widget.setVisible(is_group)
        if hasattr(self, "_members_affil_widget"):
            self._members_affil_widget.setVisible(not is_group)

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
        """
        GroupMembership tab.

        - For GROUPS: shows "Members of this Group" with an Add Member form.
        - For INDIVIDUALS: shows "Groups This Artist Belongs To" (read-only list).
        Both sections exist in the widget but visibility is toggled by isgroup.
        """
        w = QWidget()
        layout = QVBoxLayout(w)
        splitter = QSplitter(Qt.Vertical)

        # ─ Group Members section (only shown when isgroup=True) ─
        self._members_group_widget = QWidget()
        gw_layout = QVBoxLayout(self._members_group_widget)
        gw_layout.setContentsMargins(0, 0, 0, 0)

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
        self.new_member_start_edit = OptionalIntEdit("Start yr")
        self.new_member_end_edit = OptionalIntEdit("End yr")
        self.new_member_current_check = QCheckBox("Current")
        add_member_btn = QPushButton("Add Member")
        add_member_btn.clicked.connect(self._add_member)
        add_member_row.addWidget(self.new_member_edit, 2)
        add_member_row.addWidget(self.new_member_role_edit, 2)
        add_member_row.addWidget(QLabel("Start"))
        add_member_row.addWidget(self.new_member_start_edit)
        add_member_row.addWidget(QLabel("End"))
        add_member_row.addWidget(self.new_member_end_edit)
        add_member_row.addWidget(self.new_member_current_check)
        add_member_row.addWidget(add_member_btn)
        gm_layout.addLayout(add_member_row)

        rm_member_btn = QPushButton("Remove Selected Member")
        rm_member_btn.clicked.connect(
            lambda: self._remove_selected_row(self.members_table, self._remove_member)
        )
        gm_layout.addWidget(rm_member_btn, alignment=Qt.AlignLeft)
        gw_layout.addWidget(grp_members)
        splitter.addWidget(self._members_group_widget)

        # ─ Group Affiliations section (shown for all, but mainly useful for individuals) ─
        self._members_affil_widget = QWidget()
        aw_layout = QVBoxLayout(self._members_affil_widget)
        aw_layout.setContentsMargins(0, 0, 0, 0)

        grp_affil = QGroupBox("Groups This Artist Belongs To")
        ga_layout = QVBoxLayout(grp_affil)
        self.affiliations_table = self._make_table(
            ["Group", "Role", "Start Year", "End Year", "Current"], editable=False
        )
        ga_layout.addWidget(self.affiliations_table)
        aw_layout.addWidget(grp_affil)
        splitter.addWidget(self._members_affil_widget)

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

        add_infl_row = QHBoxLayout()
        self.new_influenced_edit = QLineEdit()
        self.new_influenced_edit.setPlaceholderText("Artist name…")
        self.new_influenced_desc_edit = QLineEdit()
        self.new_influenced_desc_edit.setPlaceholderText("Description (optional)")
        add_infl_btn = QPushButton("Add →")
        add_infl_btn.clicked.connect(self._add_influenced)
        rm_infl_btn = QPushButton("Remove Selected")
        rm_infl_btn.clicked.connect(
            lambda: self._remove_selected_row(
                self.influenced_table, self._remove_influenced
            )
        )
        add_infl_row.addWidget(self.new_influenced_edit, 2)
        add_infl_row.addWidget(self.new_influenced_desc_edit, 2)
        add_infl_row.addWidget(add_infl_btn)
        inf_layout.addLayout(add_infl_row)
        inf_layout.addWidget(rm_infl_btn, alignment=Qt.AlignLeft)
        splitter.addWidget(inf_grp)

        # Influencers (artists who influenced this one)
        infl_grp = QGroupBox("Artists Who Influenced This Artist")
        infl_layout = QVBoxLayout(infl_grp)
        self.influencer_table = self._make_table(
            ["Influencer Artist", "Description"], editable=False
        )
        infl_layout.addWidget(self.influencer_table)

        add_influencer_row = QHBoxLayout()
        self.new_influencer_edit = QLineEdit()
        self.new_influencer_edit.setPlaceholderText("Artist name…")
        self.new_influencer_desc_edit = QLineEdit()
        self.new_influencer_desc_edit.setPlaceholderText("Description (optional)")
        add_influencer_btn = QPushButton("Add →")
        add_influencer_btn.clicked.connect(self._add_influencer)
        rm_influencer_btn = QPushButton("Remove Selected")
        rm_influencer_btn.clicked.connect(
            lambda: self._remove_selected_row(
                self.influencer_table, self._remove_influencer
            )
        )
        add_influencer_row.addWidget(self.new_influencer_edit, 2)
        add_influencer_row.addWidget(self.new_influencer_desc_edit, 2)
        add_influencer_row.addWidget(add_influencer_btn)
        infl_layout.addLayout(add_influencer_row)
        infl_layout.addWidget(rm_influencer_btn, alignment=Qt.AlignLeft)
        splitter.addWidget(infl_grp)

        layout.addWidget(splitter)
        return w

    # ── Tab: Places & Awards ──────────────────────────────────────────────────

    def _build_places_awards_tab(self):
        w = QWidget()
        layout = QVBoxLayout(w)
        splitter = QSplitter(Qt.Vertical)

        # ── Places ──
        places_grp = QGroupBox("Associated Places")
        pl_layout = QVBoxLayout(places_grp)

        # Table now shows association_type
        self.places_table = self._make_table(
            ["Place Name", "Association Type", "Place Type", "Region/Country"],
            editable=False,
        )
        pl_layout.addWidget(self.places_table)

        # Help text
        place_help = QLabel(
            "You can type a new place name — it will be created automatically if it doesn't exist yet."
        )
        place_help.setWordWrap(True)
        place_help.setStyleSheet("color: #888; font-size: 11px;")
        pl_layout.addWidget(place_help)

        pl_add_row = QHBoxLayout()
        self.new_place_edit = QLineEdit()
        self.new_place_edit.setPlaceholderText("Place name (new or existing)…")

        # Association type: combo with common suggestions + custom text allowed
        self.new_place_assoc_edit = QLineEdit()
        self.new_place_assoc_edit.setPlaceholderText(
            "Relationship (e.g. Birthplace, Hometown)…"
        )

        add_place_btn = QPushButton("Link Place")
        add_place_btn.clicked.connect(self._add_place)
        rm_place_btn = QPushButton("Unlink Selected")
        rm_place_btn.clicked.connect(
            lambda: self._remove_selected_row(self.places_table, self._remove_place)
        )
        pl_add_row.addWidget(self.new_place_edit, 3)
        pl_add_row.addWidget(self.new_place_assoc_edit, 2)
        pl_add_row.addWidget(add_place_btn)
        pl_add_row.addWidget(rm_place_btn)
        pl_layout.addLayout(pl_add_row)
        splitter.addWidget(places_grp)

        # ── Awards ──
        awards_grp = QGroupBox("Awards")
        aw_layout = QVBoxLayout(awards_grp)
        self.awards_table = self._make_table(
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
        self.new_award_edit.setPlaceholderText("Award name (new or existing)…")
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

    # ── Tab: Discography ──────────────────────────────────────────────────────

    def _build_discography_tab(self):
        w = QWidget()
        layout = QVBoxLayout(w)

        note = QLabel(
            "<i>Album credits where this artist is the primary Album Artist are shown here. "
            '"Primary Artist" track credits for those same albums are hidden to avoid redundancy.</i>'
        )
        note.setWordWrap(True)
        layout.addWidget(note)

        albums_grp = QGroupBox("Album Credits (non-redundant roles)")
        al_layout = QVBoxLayout(albums_grp)
        self.albums_table = self._make_table(["Album", "Role", "Year"], editable=False)
        al_layout.addWidget(self.albums_table)
        layout.addWidget(albums_grp)

        tracks_grp = QGroupBox("Track Credits")
        tr_layout = QVBoxLayout(tracks_grp)
        self.tracks_table = self._make_table(["Track", "Role", "Album"], editable=False)
        tr_layout.addWidget(self.tracks_table)
        layout.addWidget(tracks_grp)

        return w

    # ── Tab: Advanced ─────────────────────────────────────────────────────────

    def _build_advanced_tab(self):
        w = QWidget()
        form = QFormLayout(w)

        self.mbid_edit = QLineEdit()
        self.mbid_edit.setPlaceholderText("MusicBrainz ID")
        form.addRow("MBID:", self.mbid_edit)

        self.wiki_edit = QLineEdit()
        self.wiki_edit.setPlaceholderText("https://en.wikipedia.org/…")
        form.addRow("Wikipedia:", self.wiki_edit)

        self.website_edit = QLineEdit()
        self.website_edit.setPlaceholderText("https://…")
        form.addRow("Website:", self.website_edit)

        self.is_fixed_check = QCheckBox("Mark metadata as complete")
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
        self.artist_type_edit.setText(a.artist_type or "")

        is_group = bool(a.isgroup)
        # Block signals temporarily so _on_isgroup_changed doesn't fire mid-load
        self.isgroup_check.blockSignals(True)
        self.isgroup_check.setChecked(is_group)
        self.isgroup_check.blockSignals(False)
        # Manually set labels to match loaded state
        self._on_isgroup_changed(is_group)

        g_idx = self.gender_combo.findText(a.gender or "")
        self.gender_combo.setCurrentIndex(max(g_idx, 0))

        self.begin_year_edit.set_from_db(a.begin_year)
        self.begin_month_edit.set_from_db(a.begin_month)
        self.begin_day_edit.set_from_db(a.begin_day)

        # Determine "active" state: active if end_year is None/0
        is_active = not bool(a.end_year)
        self.is_active_check.blockSignals(True)
        self.is_active_check.setChecked(is_active)
        self.is_active_check.blockSignals(False)
        self._on_active_toggled(is_active)

        if not is_active:
            self.end_year_edit.set_from_db(a.end_year)
            self.end_month_edit.set_from_db(a.end_month)
            self.end_day_edit.set_from_db(a.end_day)

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

        # Apply visibility based on isgroup
        self._update_members_tab_visibility()

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
        """Load place associations, showing the association_type column."""
        self.places_table.setRowCount(0)

        # Try to load via PlaceAssociation for full data including association_type
        assocs_loaded = False
        try:
            place_assocs = self.controller.get.get_all_entities(
                "PlaceAssociation",
                entity_id=self.artist.artist_id,
                entity_type="Artist",
            )
            if place_assocs is not None:
                for assoc in place_assocs:
                    place = assoc.place
                    if place is None:
                        continue
                    self._append_row(
                        self.places_table,
                        [
                            place.place_name,
                            assoc.association_type or "",
                            place.place_type or "",
                            self._parent_place_name(place),
                        ],
                        user_data=assoc.association_id,
                    )
                assocs_loaded = True
        except Exception as e:
            logger.debug(f"Could not load via PlaceAssociation entities: {e}")

        if not assocs_loaded:
            # Fallback: use artist.places (association_type not available here)
            for place in getattr(self.artist, "places", []):
                self._append_row(
                    self.places_table,
                    [
                        place.place_name,
                        "",
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
        """
        Load album and track credits.
        Filter out album-level "Primary Artist" credits where the artist IS
        the album artist — those are the main discography, not additional credits.
        """
        self.albums_table.setRowCount(0)

        # Collect album IDs where this artist is Album Artist (to filter redundant Primary Artist rows)
        album_artist_album_ids = set()
        for assoc in getattr(self.artist, "album_roles", []):
            role = assoc.role
            if role and getattr(role, "role_name", "") == "Album Artist":
                album_artist_album_ids.add(assoc.album_id)

        for assoc in getattr(self.artist, "album_roles", []):
            album = assoc.album
            role = assoc.role
            if album is None:
                continue
            role_name = role.role_name if role else ""
            # Skip "Primary Artist" credits for albums where this artist is already Album Artist
            if (
                role_name == "Primary Artist"
                and album.album_id in album_artist_album_ids
            ):
                continue
            self._append_row(
                self.albums_table,
                [
                    album.album_name,
                    role_name,
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
                active_start_year=self.new_member_start_edit.get_value_or_none(),
                active_end_year=self.new_member_end_edit.get_value_or_none(),
                is_current=1 if self.new_member_current_check.isChecked() else 0,
            )
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Could not add member:\n{e}")
            return

        self._reload_artist()
        self._load_members()
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
        self._reload_artist()
        self._load_members()

    # ─ Influences ──────────────────────────────────────

    def _add_influenced(self):
        name = self.new_influenced_edit.text().strip()
        if not name:
            return
        try:
            influenced = self.controller.get.get_entity_object(
                "Artist", artist_name=name
            )
            if not influenced:
                influenced = self.controller.add.add_entity("Artist", artist_name=name)
            if isinstance(influenced, list):
                influenced = influenced[0]
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
        self._reload_artist()
        self._load_influences()
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
        self._reload_artist()
        self._load_influences()

    def _add_influencer(self):
        name = self.new_influencer_edit.text().strip()
        if not name:
            return
        try:
            influencer = self.controller.get.get_entity_object(
                "Artist", artist_name=name
            )
            if not influencer:
                influencer = self.controller.add.add_entity("Artist", artist_name=name)
            if isinstance(influencer, list):
                influencer = influencer[0]
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
        self._reload_artist()
        self._load_influences()
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
        self._reload_artist()
        self._load_influences()

    # ─ Places ──────────────────────────────────────────

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

        # Find or create the place
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

        self._reload_artist()
        self._load_places()
        self.new_place_edit.clear()
        self.new_place_assoc_edit.clear()

    def _remove_place(self, row):
        assoc_id = self.places_table.item(row, 0).data(Qt.UserRole)
        if assoc_id is None:
            return
        try:
            # Try to delete by association_id first
            self.controller.delete.delete_entity("PlaceAssociation", assoc_id)
        except Exception:
            # Fallback: try by entity/place combo (older controller versions)
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
        self._reload_artist()
        self._load_places()

    # ─ Awards ──────────────────────────────────────────

    def _add_award(self):
        name = self.new_award_edit.text().strip()
        if not name:
            return

        # Find or create the award
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
            artist_type=self.artist_type_edit.text().strip() or None,
            isgroup=1 if self.isgroup_check.isChecked() else 0,
            gender=self.gender_combo.currentText() or None,
            begin_year=self.begin_year_edit.get_value_or_none(),
            begin_month=self.begin_month_edit.get_value_or_none(),
            begin_day=self.begin_day_edit.get_value_or_none(),
            end_year=self.end_year_edit.get_value_or_none(),
            end_month=self.end_month_edit.get_value_or_none(),
            end_day=self.end_day_edit.get_value_or_none(),
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
