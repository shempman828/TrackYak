# ══════════════════════════════════════════════════════════════════════════════
# Tab: Biography
# ══════════════════════════════════════════════════════════════════════════════

from PySide6.QtWidgets import QLabel, QTextEdit, QVBoxLayout, QWidget


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
