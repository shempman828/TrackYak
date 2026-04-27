# ══════════════════════════════════════════════════════════════════════════════
# Tab: Basic
# ══════════════════════════════════════════════════════════════════════════════
import os
import tempfile

from PySide6.QtCore import Qt
from PySide6.QtGui import QIntValidator, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QCompleter,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.wikipedia_seach import (
    download_wikipedia_image,
    search_wikipedia,
    select_wikipedia_image,
)

# ── Constants ──────────────────────────────────────────────────────────────────

ARTIST_TYPE_SUGGESTIONS = ["Person", "Band", "Orchestra", "Choir", "Ensemble"]
GENDERS = ["", "Male", "Female", "Other"]


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
        self._wiki_link = ""  # Stores the last fetched Wikipedia URL
        self._pic_path = ""  # Stores the current profile picture path
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

        # Gender row — hidden when isgroup is checked
        self.gender_combo = QComboBox()
        self.gender_combo.addItems(GENDERS)
        self._gender_label = QLabel("Gender:")
        form.addRow(self._gender_label, self.gender_combo)

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

        # Wikipedia buttons
        wiki_box = QHBoxLayout()
        self.wiki_search_btn = QPushButton("🔍 Search Wikipedia")
        self.wiki_search_btn.setToolTip("Search Wikipedia for this artist")
        self.wiki_search_btn.clicked.connect(self._search_wikipedia)
        wiki_box.addWidget(self.wiki_search_btn)

        self.wiki_open_btn = QPushButton("🌐 Open Wikipedia Page")
        self.wiki_open_btn.setToolTip("Open the Wikipedia page in your browser")
        self.wiki_open_btn.setEnabled(False)
        self.wiki_open_btn.clicked.connect(self._open_wiki_link)
        wiki_box.addWidget(self.wiki_open_btn)

        wiki_box.addStretch()
        form.addRow("Wikipedia:", wiki_box)

        layout.addWidget(form_widget, 1)

        # ── Right: profile picture ───────────────────────────────────────────
        pic_grp = QGroupBox("Profile Picture")
        pic_layout = QVBoxLayout(pic_grp)

        self.pic_label = QLabel()
        self.pic_label.setFixedSize(180, 180)
        self.pic_label.setAlignment(Qt.AlignCenter)
        self.pic_label.setStyleSheet("border: 1px solid #888; background: #222;")
        self.pic_label.setText("No Image")
        self.pic_label.setToolTip("No image set")
        pic_layout.addWidget(self.pic_label)

        browse_btn = QPushButton("Browse...")
        browse_btn.clicked.connect(self._browse_pic)
        pic_layout.addWidget(browse_btn)

        self.wiki_pic_btn = QPushButton("Import from Wikipedia")
        self.wiki_pic_btn.setToolTip("Search Wikipedia and import a profile picture")
        self.wiki_pic_btn.clicked.connect(self._import_wikipedia_image)
        pic_layout.addWidget(self.wiki_pic_btn)

        clear_pic_btn = QPushButton("Clear")
        clear_pic_btn.clicked.connect(self._clear_pic)
        pic_layout.addWidget(clear_pic_btn)

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

        self._set_pic_path(artist.profile_pic_path or "")

        # Reset Wikipedia link when loading a new artist
        self._wiki_link = ""
        self.wiki_open_btn.setEnabled(False)

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
            profile_pic_path=self._pic_path or None,
        )

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
            self._set_pic_path(path)

    def _clear_pic(self):
        self._set_pic_path("")

    def _set_pic_path(self, path: str):
        """Update internal path, tooltip, and preview."""
        self._pic_path = path
        self.pic_label.setToolTip(path if path else "No image set")
        self._refresh_pic_preview(path)

    def _refresh_pic_preview(self, path: str):
        if not path:
            self.pic_label.setPixmap(QPixmap())
            self.pic_label.setText("No Image")
            return
        px = QPixmap(path)
        if px.isNull():
            self.pic_label.setPixmap(QPixmap())
            self.pic_label.setText("Invalid image")
        else:
            self.pic_label.setPixmap(
                px.scaled(
                    self.pic_label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation
                )
            )

    # ── Wikipedia ──────────────────────────────────────────────────────────

    def _get_search_query(self) -> str:
        """Use the artist name as the search query, falling back to a prompt."""
        return self.name_edit.text().strip() or ""

    def _search_wikipedia(self):
        """Open Wikipedia search dialog and store the resulting link."""
        query = self._get_search_query()
        if not query:
            QMessageBox.information(
                self, "Wikipedia Search", "Please enter an artist name first."
            )
            return

        title, summary, full_content, link, images = search_wikipedia(query, self)

        if not link:
            return  # User cancelled or no result

        self._wiki_link = link
        self.wiki_open_btn.setEnabled(True)
        self.wiki_open_btn.setToolTip(link)

    def _open_wiki_link(self):
        """Open the stored Wikipedia link in the default browser."""
        if self._wiki_link:
            import webbrowser

            webbrowser.open(self._wiki_link)

    def _import_wikipedia_image(self):
        """Search Wikipedia and let the user pick an image to use as the profile picture."""
        query = self._get_search_query()
        if not query:
            QMessageBox.information(
                self, "Wikipedia Image Import", "Please enter an artist name first."
            )
            return

        title, summary, full_content, link, images = search_wikipedia(query, self)

        if not images:
            if link:
                QMessageBox.information(
                    self,
                    "Wikipedia Image Import",
                    "No images found on the selected Wikipedia page.",
                )
            return

        # Store the link as a side-effect if we got one
        if link:
            self._wiki_link = link
            self.wiki_open_btn.setEnabled(True)
            self.wiki_open_btn.setToolTip(link)

        selected_url = select_wikipedia_image(images, self)
        if not selected_url:
            return

        image_bytes = download_wikipedia_image(selected_url)
        if not image_bytes:
            QMessageBox.warning(
                self,
                "Download Failed",
                "Could not download the selected image. Please try another.",
            )
            return

        # Save to a temp file so QPixmap can load it
        ext = os.path.splitext(selected_url.split("?")[0])[-1] or ".jpg"
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=ext)
        try:
            tmp.write(image_bytes)
            tmp.flush()
            tmp.close()
            self._set_pic_path(tmp.name)
        except OSError as e:
            QMessageBox.warning(
                self,
                "Save Failed",
                f"Could not save the downloaded image:\n{e}",
            )


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
