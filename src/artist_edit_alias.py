# ══════════════════════════════════════════════════════════════════════════════
# Tab: Aliases
# ══════════════════════════════════════════════════════════════════════════════
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QPushButton, QVBoxLayout, QWidget

from src.artist_alias_dialog import ArtistAliasDialog


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
