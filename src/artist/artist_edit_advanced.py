# ══════════════════════════════════════════════════════════════════════════════
# Tab: Advanced
# ══════════════════════════════════════════════════════════════════════════════

from PySide6.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QWidget,
)

from src.common.edit_dirty import value_changed
from src.core.logger_config import logger


class AdvancedTab(QWidget):
    def __init__(self, controller, artist, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.artist = artist
        self._build_ui()

    def _build_ui(self):
        form = QFormLayout(self)

        mbid_row = QHBoxLayout()
        self.mbid_edit = QLineEdit()
        self.mbid_edit.setPlaceholderText("MusicBrainz ID")
        self.mbid_edit.textChanged.connect(self._update_lookup_button_mode)
        mbid_row.addWidget(self.mbid_edit)
        self.lookup_button = QPushButton()
        mbid_row.addWidget(self.lookup_button)
        form.addRow("MBID:", mbid_row)
        self._update_lookup_button_mode()

        self.wiki_edit = QLineEdit()
        self.wiki_edit.setPlaceholderText("https://en.wikipedia.org/...")
        form.addRow("Wikipedia:", self.wiki_edit)

        self.website_edit = QLineEdit()
        self.website_edit.setPlaceholderText("https://...")
        form.addRow("Website:", self.website_edit)

        self.first_pass_check = QCheckBox("Initial metadata review complete")
        form.addRow("First Pass:", self.first_pass_check)

        self.second_pass_check = QCheckBox("Final metadata review complete")
        form.addRow("Second Pass:", self.second_pass_check)

        self.artist_id_label = QLabel()
        self.artist_id_label.setProperty("textRole", "muted")
        form.addRow("Artist ID (read-only):", self.artist_id_label)

    def is_import_mode(self) -> bool:
        """True once an MBID is present -- the lookup button switches from
        'search by name' to 're-fetch this exact MusicBrainz record'."""
        return bool(self.mbid_edit.text().strip())

    def current_mbid(self) -> str:
        return self.mbid_edit.text().strip()

    def _update_lookup_button_mode(self):
        if self.is_import_mode():
            self.lookup_button.setText("⬇ Import from MusicBrainz")
            self.lookup_button.setToolTip(
                "Re-fetch this exact MusicBrainz record by its MBID and fill "
                "in blank fields, plus review new aliases, birthplace/"
                "deathplace, and band-membership matches. Never overwrites "
                "fields you've already filled in. Clear the MBID field to "
                "search by name instead."
            )
        else:
            self.lookup_button.setText("🔍 Search MusicBrainz")
            self.lookup_button.setToolTip(
                "Search MusicBrainz by artist name and fill in blank fields "
                "(MBID, links, dates, gender) from the selected match. Never "
                "overwrites fields you've already filled in."
            )

    def load(self, artist):
        self.artist = artist
        self.mbid_edit.setText(artist.MBID or "")
        self.wiki_edit.setText(artist.wikipedia_link or "")
        self.website_edit.setText(artist.website_link or "")
        self.first_pass_check.setChecked(bool(artist.first_pass))
        self.second_pass_check.setChecked(bool(artist.second_pass))
        self.artist_id_label.setText(str(artist.artist_id))

    def collect_changes(self):
        candidates = dict(
            MBID=self.mbid_edit.text().strip() or None,
            wikipedia_link=self.wiki_edit.text().strip() or None,
            website_link=self.website_edit.text().strip() or None,
            first_pass=1 if self.first_pass_check.isChecked() else 0,
            second_pass=1 if self.second_pass_check.isChecked() else 0,
        )
        return {
            field: new
            for field, new in candidates.items()
            if value_changed(getattr(self.artist, field, None), new)
        }

    def set_if_empty(self, values: dict):
        """Fill MBID/link fields from a MusicBrainz enrichment dict, but only
        where this tab's own field is currently blank -- never overwrites
        something the user already filled in or typed moments ago."""
        if "MBID" in values and not self.mbid_edit.text().strip():
            self.mbid_edit.setText(values["MBID"])
        if "wikipedia_link" in values and not self.wiki_edit.text().strip():
            self.wiki_edit.setText(values["wikipedia_link"])
        if "website_link" in values and not self.website_edit.text().strip():
            self.website_edit.setText(values["website_link"])
