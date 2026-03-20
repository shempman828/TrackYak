# ══════════════════════════════════════════════════════════════════════════════
# Tab: Advanced
# ══════════════════════════════════════════════════════════════════════════════

from PySide6.QtWidgets import QCheckBox, QFormLayout, QLabel, QLineEdit, QWidget


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
