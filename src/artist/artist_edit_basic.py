# ══════════════════════════════════════════════════════════════════════════════
# Tab: Basic
# ══════════════════════════════════════════════════════════════════════════════

import os

from PySide6.QtCore import QSettings, QSize, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QIntValidator
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from src.artist.artist_edit_types import ArtistTypesWidget
from src.artist.artist_image_manager import move_to_artist_images_dir
from src.artist.religion_manager import ReligionManagerDialog
from src.common.edit_dirty import value_changed
from src.common.entity_completer_edit import EntityCompleterEdit, find_or_create_by_name
from src.core.logger_config import logger
from src.image.pixmap_with_fallback import load_pixmap_with_fallback

# ── Constants ──────────────────────────────────────────────────────────────────

GENDERS = ["", "Male", "Female", "Other"]

# QSettings key for remembering the last directory used in the picture browser
_SETTINGS_LAST_PIC_DIR = "artist_editor/last_pic_dir"

# Bounding box for the picture preview — the photo is scaled to fit inside
# it while keeping its own aspect ratio, not stretched or cropped square.
PIC_MAX_SIZE = QSize(180, 180)


class BasicTab(QWidget):
    """
    Core identity fields: name, isgroup, gender, dates, profile picture.

    collect_changes() returns a dict ready to pass to update_entity("Artist", ...).
    The isgroup_check signal is connected externally by ArtistEditor to keep
    MembersTab visibility in sync.
    """

    def __init__(self, controller, artist, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.artist = artist
        self._wiki_link = ""  # Read from the artist record; never set from here
        self._mb_link = ""  # Read from the artist record; never set from here
        self._pic_path = ""  # Stores the current profile picture path
        self._settings = QSettings()
        self._build_ui()

    def _build_ui(self):
        outer = QHBoxLayout(self)
        outer.setSpacing(16)

        # ── Left: form, organized into logical sections ─────────────────────
        left_col = QVBoxLayout()
        left_col.setSpacing(12)

        left_col.addWidget(self._build_identity_group())
        left_col.addWidget(self._build_types_group())
        left_col.addWidget(self._build_dates_group())
        left_col.addWidget(self._build_links_group())
        left_col.addStretch()

        outer.addLayout(left_col, 1)

        # ── Right: profile picture ───────────────────────────────────────────
        outer.addWidget(self._build_picture_group())

    # ── Group builders ───────────────────────────────────────────────────────

    def _build_identity_group(self):
        grp = QGroupBox("Identity")
        form = QFormLayout(grp)
        form.setLabelAlignment(Qt.AlignRight)
        form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(8)

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("Artist name")
        form.addRow("Name *:", self.name_edit)

        self.isgroup_check = SegmentedToggle("Person", "Group")
        form.addRow("Is Group:", self.isgroup_check)
        self.isgroup_check.toggled.connect(self._on_isgroup_changed)

        # Gender row — hidden when isgroup is checked
        self.gender_combo = QComboBox()
        self.gender_combo.addItems(GENDERS)
        self._gender_label = QLabel("Gender:")
        form.addRow(self._gender_label, self.gender_combo)

        religion_row = QHBoxLayout()
        self.religion_edit = EntityCompleterEdit("Search or add a religion…")
        religion_row.addWidget(self.religion_edit, 1)
        self.religion_manage_btn = QPushButton("Manage…")
        self.religion_manage_btn.setFlat(True)
        self.religion_manage_btn.setToolTip("Rename, delete, or set parent religions")
        self.religion_manage_btn.clicked.connect(self._open_religion_manager)
        religion_row.addWidget(self.religion_manage_btn)
        form.addRow("Religion:", religion_row)
        self._refresh_religion_index()

        return grp

    def _build_types_group(self):
        grp = QGroupBox("Types")
        v = QVBoxLayout(grp)
        self.types_widget = ArtistTypesWidget(self.controller, self.artist)
        v.addWidget(self.types_widget)
        return grp

    def _build_dates_group(self):
        grp = QGroupBox("Dates")
        form = QFormLayout(grp)
        form.setLabelAlignment(Qt.AlignRight)
        form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(8)

        # Begin date
        self.begin_year_edit = OptionalIntEdit("YYYY")
        self.begin_year_edit.setToolTip("Year born / founded")
        self.begin_month_edit = OptionalIntEdit("MM")
        self.begin_month_edit.setToolTip("Month (1-12)")
        self.begin_day_edit = OptionalIntEdit("DD")
        self.begin_day_edit.setToolTip("Day (1-31)")
        self.begin_date_label = QLabel("Born / Founded:")
        form.addRow(
            self.begin_date_label,
            self._build_date_row(
                self.begin_year_edit, self.begin_month_edit, self.begin_day_edit
            ),
        )

        # Active status toggle
        self.is_active_check = QCheckBox("Alive / Active")
        self.is_active_check.setToolTip(
            "Check this if the artist is still active. "
            "Unchecking enables the end date fields below."
        )
        self.is_active_check.toggled.connect(self._on_active_toggled)
        form.addRow("Status:", self.is_active_check)

        # End date
        self.end_year_edit = OptionalIntEdit("YYYY")
        self.end_year_edit.setToolTip("Year died / disbanded")
        self.end_month_edit = OptionalIntEdit("MM")
        self.end_day_edit = OptionalIntEdit("DD")
        self.end_date_label = QLabel("Died / Disbanded:")
        self.end_date_row = self._build_date_row(
            self.end_year_edit, self.end_month_edit, self.end_day_edit
        )
        form.addRow(self.end_date_label, self.end_date_row)

        # Read-only computed stats: age, career span, track count
        stats_row = QHBoxLayout()
        stats_row.setSpacing(14)
        self.age_label = QLabel("—")
        self.career_span_label = QLabel("—")
        self.track_count_label = QLabel("—")
        for label in (self.age_label, self.career_span_label, self.track_count_label):
            label.setStyleSheet("color: palette(mid);")
        stats_row.addWidget(self.age_label)
        stats_row.addWidget(self._stat_separator())
        stats_row.addWidget(self.career_span_label)
        stats_row.addWidget(self._stat_separator())
        stats_row.addWidget(self.track_count_label)
        stats_row.addStretch()
        form.addRow("Stats:", stats_row)

        return grp

    def _stat_separator(self):
        sep = QLabel("·")
        sep.setStyleSheet("color: palette(mid);")
        return sep

    def _build_date_row(self, year_edit, month_edit, day_edit):
        """A compact Year/Month/Day cluster with small captions above each field."""
        row = QWidget()
        grid = QGridLayout(row)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(2)

        for col, (caption, edit) in enumerate(
            (("Year", year_edit), ("Month", month_edit), ("Day", day_edit))
        ):
            caption_label = QLabel(caption)
            caption_label.setStyleSheet("color: palette(mid); font-size: 10px;")
            grid.addWidget(caption_label, 0, col, Qt.AlignHCenter)
            grid.addWidget(edit, 1, col)

        grid.setColumnStretch(3, 1)
        return row

    def _build_links_group(self):
        grp = QGroupBox("Links")
        self._links_group = grp
        v = QVBoxLayout(grp)

        wiki_box = QHBoxLayout()
        self.wiki_open_btn = QPushButton("🌐 Open Wikipedia Page")
        self.wiki_open_btn.setToolTip("Open the Wikipedia page in your browser")
        self.wiki_open_btn.clicked.connect(self._open_wiki_link)
        wiki_box.addWidget(self.wiki_open_btn)
        wiki_box.addStretch()

        v.addLayout(wiki_box)

        mb_box = QHBoxLayout()
        self.mb_open_btn = QPushButton("🎵 Open in MusicBrainz")
        self.mb_open_btn.setToolTip("Open the MusicBrainz artist page in your browser")
        self.mb_open_btn.clicked.connect(self._open_mb_link)
        mb_box.addWidget(self.mb_open_btn)
        mb_box.addStretch()

        v.addLayout(mb_box)

        # Hidden entirely until load() finds a link on the artist record
        grp.setVisible(False)
        return grp

    def _build_picture_group(self):
        pic_grp = QGroupBox()
        pic_grp.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        pic_layout = QVBoxLayout(pic_grp)

        self.pic_label = QLabel()
        self.pic_label.setMaximumSize(PIC_MAX_SIZE)
        self.pic_label.setAlignment(Qt.AlignCenter)
        self.pic_label.setStyleSheet(
            "border: 1px solid #888; border-radius: 4px; background: #222;"
        )
        pic_layout.addWidget(self.pic_label)

        btn_row = QHBoxLayout()
        browse_btn = QPushButton("Browse...")
        browse_btn.clicked.connect(self._browse_pic)
        btn_row.addWidget(browse_btn)

        clear_pic_btn = QPushButton("Clear")
        clear_pic_btn.clicked.connect(self._clear_pic)
        btn_row.addWidget(clear_pic_btn)

        pic_layout.addLayout(btn_row)
        pic_layout.addStretch()
        return pic_grp

    def _refresh_religion_index(self):
        try:
            self._known_religions = self.controller.get.get_all_entities("Religion") or []
        except Exception as e:
            logger.warning(f"Could not fetch Religion for completer: {e}")
            self._known_religions = []
        index = {
            r.religion_name: r.religion_id for r in self._known_religions if r.religion_name
        }
        self.religion_edit.set_index(index)

    def _open_religion_manager(self):
        dialog = ReligionManagerDialog(self.controller, self)
        dialog.exec()
        self._refresh_religion_index()

    def _resolve_religion_id(self):
        """Resolve the religion text field to an id, creating a new Religion
        row if the typed name doesn't match an existing one (mirrors the
        gender/types find-or-create pattern used elsewhere in this tab)."""
        name = self.religion_edit.text().strip()
        if not name:
            return None

        matched_id = self.religion_edit.matched_id()
        if matched_id is not None:
            return matched_id

        entity = find_or_create_by_name(
            self.controller, "Religion", "religion_name", name, self._known_religions
        )
        return entity.religion_id if entity else None

    def load(self, artist):
        self.artist = artist
        self.name_edit.setText(artist.artist_name or "")
        self.types_widget.load(artist)
        self.religion_edit.setText(artist.religion.religion_name if artist.religion else "")

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

        self._set_pic_path(artist.profile_pic_path or "")

        # Read-only computed stats
        age = getattr(artist, "age", None)
        self.age_label.setText(f"Age: {age}" if age is not None else "Age: —")

        career_span = getattr(artist, "career_span", None)
        self.career_span_label.setText(
            f"Career: {career_span}" if career_span else "Career: —"
        )

        track_count = getattr(artist, "track_count", None)
        self.track_count_label.setText(
            f"Tracks: {track_count}" if track_count is not None else "Tracks: —"
        )

        # Wikipedia link is read-only here: display if present, otherwise
        # hide the section entirely. Nothing in this tab ever writes it.
        self._wiki_link = getattr(artist, "wikipedia_url", None) or ""
        self.wiki_open_btn.setVisible(bool(self._wiki_link))

        mbid = getattr(artist, "MBID", None)
        self._mb_link = f"https://musicbrainz.org/artist/{mbid}" if mbid else ""
        self.mb_open_btn.setVisible(bool(self._mb_link))

        self._links_group.setVisible(bool(self._wiki_link or self._mb_link))

    def collect_changes(self):
        """Return a dict of only the basic fields whose value actually
        differs from the loaded artist — untouched fields are omitted."""
        candidates = dict(
            artist_name=self.name_edit.text().strip(),
            isgroup=1 if self.isgroup_check.isChecked() else 0,
            gender=self.gender_combo.currentText() or None,
            religion_id=self._resolve_religion_id(),
            begin_year=self.begin_year_edit.get_value_or_none(),
            begin_month=self.begin_month_edit.get_value_or_none(),
            begin_day=self.begin_day_edit.get_value_or_none(),
            end_year=self.end_year_edit.get_value_or_none(),
            end_month=self.end_month_edit.get_value_or_none(),
            end_day=self.end_day_edit.get_value_or_none(),
            profile_pic_path=self._pic_path or None,
        )
        return {
            field: new
            for field, new in candidates.items()
            if value_changed(getattr(self.artist, field, None), new)
        }

    def set_if_empty(self, values: dict):
        """Fill fields from a MusicBrainz enrichment dict, but only where
        this tab's own widget is currently blank -- never overwrites
        something the user already filled in or typed moments ago.

        isgroup has no blank widget state (it's always Person or Group), so
        it uses the originally-loaded artist's value as the "unset" signal
        instead -- applied only when the widget is still at the default
        (Person/unchecked), so a deliberate manual toggle just before the
        lookup is never clobbered.
        """
        if (
            "isgroup" in values
            and getattr(self.artist, "isgroup", None) is None
            and not self.isgroup_check.isChecked()
        ):
            self.isgroup_check.setChecked(bool(values["isgroup"]))
            self._on_isgroup_changed(bool(values["isgroup"]))

        if "gender" in values and not self.gender_combo.currentText():
            g_idx = self.gender_combo.findText(values["gender"])
            if g_idx >= 0:
                self.gender_combo.setCurrentIndex(g_idx)

        for field_name, edit in (
            ("begin_year", self.begin_year_edit),
            ("begin_month", self.begin_month_edit),
            ("begin_day", self.begin_day_edit),
            ("end_year", self.end_year_edit),
            ("end_month", self.end_month_edit),
            ("end_day", self.end_day_edit),
        ):
            if field_name in values and edit.get_value_or_none() is None:
                edit.set_from_db(values[field_name])

        # An end date implies the artist isn't active -- surface the end
        # date fields the same way toggling "Alive/Active" off would.
        if any(k in values for k in ("end_year", "end_month", "end_day")):
            if self.end_year_edit.get_value_or_none() is not None:
                self.is_active_check.setChecked(False)

    # ── Internal slots ─────────────────────────────────────────────────────

    def _on_isgroup_changed(self, is_group: bool):
        # Toggle gender row visibility
        self.gender_combo.setVisible(not is_group)
        self._gender_label.setVisible(not is_group)

        if is_group:
            self.begin_date_label.setText("Founded:")
            self.end_date_label.setText("Disbanded:")
            self.is_active_check.setText("Still together / Active")
        else:
            self.begin_date_label.setText("Born:")
            self.end_date_label.setText("Died:")
            self.is_active_check.setText("Currently alive / active")

    def _on_active_toggled(self, is_active: bool):
        self.end_year_edit.setEnabled(not is_active)
        self.end_month_edit.setEnabled(not is_active)
        self.end_day_edit.setEnabled(not is_active)
        self.end_date_label.setVisible(not is_active)
        self.end_date_row.setVisible(not is_active)
        if is_active:
            self.end_year_edit.clear()
            self.end_month_edit.clear()
            self.end_day_edit.clear()

    def _browse_pic(self):
        # Always start from wherever the user last browsed to for a picture,
        # regardless of which artist/picture is currently loaded. This keeps
        # repeated edits within a session (or across restarts) pointed at
        # the same folder, e.g. a shared "artist pictures" directory.
        start_dir = self._settings.value(_SETTINGS_LAST_PIC_DIR, "", type=str)
        if not start_dir or not os.path.isdir(start_dir):
            start_dir = ""

        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Profile Picture",
            start_dir,
            "Images (*.png *.jpg *.jpeg *.webp *.bmp *.gif)",
        )
        if path:
            logger.debug(f"Profile picture selected for artist editor: {path}")
            self._settings.setValue(_SETTINGS_LAST_PIC_DIR, os.path.dirname(path))
            managed_path = move_to_artist_images_dir(
                self.artist.artist_id, self.artist.artist_name, path
            )
            self._set_pic_path(managed_path)

    def _clear_pic(self):
        self._set_pic_path("")

    def _set_pic_path(self, path: str):
        """Update internal path, tooltip, and preview."""
        self._pic_path = path
        self.pic_label.setToolTip(path if path else "No image set")
        self._refresh_pic_preview(path)

    def _refresh_pic_preview(self, path: str):
        self.pic_label.setText("")
        self.pic_label.setPixmap(load_pixmap_with_fallback(path, PIC_MAX_SIZE))

    def _open_wiki_link(self):
        if self._wiki_link:
            QDesktopServices.openUrl(QUrl(self._wiki_link))

    def _open_mb_link(self):
        if self._mb_link:
            QDesktopServices.openUrl(QUrl(self._mb_link))


class SegmentedToggle(QWidget):
    """A two-option Person/Group segmented control that mimics enough of the
    QCheckBox interface (toggled signal, isChecked/setChecked, blockSignals)
    to be a drop-in replacement wherever the old checkbox was used."""

    toggled = Signal(bool)

    def __init__(self, left_text: str, right_text: str, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._left_btn = QPushButton(left_text)
        self._right_btn = QPushButton(right_text)
        for btn in (self._left_btn, self._right_btn):
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setMinimumWidth(70)

        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._group.addButton(self._left_btn)
        self._group.addButton(self._right_btn)
        self._left_btn.setChecked(True)

        layout.addWidget(self._left_btn)
        layout.addWidget(self._right_btn)
        layout.addStretch()

        self._apply_style()
        self._right_btn.toggled.connect(self._on_right_toggled)

    def _apply_style(self):
        self._left_btn.setStyleSheet(
            """
            QPushButton {
                border: 1px solid palette(mid);
                border-top-left-radius: 4px;
                border-bottom-left-radius: 4px;
                border-top-right-radius: 0px;
                border-bottom-right-radius: 0px;
                padding: 4px 12px;
            }
            QPushButton:checked {
                background: palette(highlight);
                color: palette(highlighted-text);
                font-weight: 600;
            }
            """
        )
        self._right_btn.setStyleSheet(
            """
            QPushButton {
                border: 1px solid palette(mid);
                border-left: none;
                border-top-right-radius: 4px;
                border-bottom-right-radius: 4px;
                border-top-left-radius: 0px;
                border-bottom-left-radius: 0px;
                padding: 4px 12px;
            }
            QPushButton:checked {
                background: palette(highlight);
                color: palette(highlighted-text);
                font-weight: 600;
            }
            """
        )

    def _on_right_toggled(self, checked: bool):
        self.toggled.emit(checked)

    def isChecked(self) -> bool:
        return self._right_btn.isChecked()

    def setChecked(self, checked: bool):
        if checked:
            self._right_btn.setChecked(True)
        else:
            self._left_btn.setChecked(True)

    def blockSignals(self, block: bool):
        self._left_btn.blockSignals(block)
        self._right_btn.blockSignals(block)
        return super().blockSignals(block)


class OptionalIntEdit(QLineEdit):
    """A QLineEdit that only accepts integers and returns None when empty."""

    def __init__(self, placeholder="", parent=None):
        super().__init__(parent)
        self.setPlaceholderText(placeholder)
        self.setFixedWidth(60)
        self.setAlignment(Qt.AlignCenter)
        self.setValidator(QIntValidator(0, 9999, self))

    def get_value_or_none(self):
        text = self.text().strip()
        return int(text) if text else None

    def set_from_db(self, val):
        self.setText(str(int(val)) if val is not None else "")
