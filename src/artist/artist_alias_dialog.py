"""
artist_alias_dialog.py

Alias utilities shared across the app:
  - SUGGESTED_ALIAS_TYPES — autocomplete suggestions for the alias "type"
                             field (see artist_edit_alias.AliasesTab).
  - ArtistAliasDialog — kept for backwards compatibility; wraps AliasesTab
                        in a standalone QDialog if ever needed.
"""

from PySide6.QtWidgets import QDialog, QDialogButtonBox, QVBoxLayout

# ── Well-known alias types offered as autocomplete suggestions ─────────────
SUGGESTED_ALIAS_TYPES = [
    "Legal Name",
    "Stylized Name",
    "Project Name",
    "Persona",
    "Birth Name",
    "Former Name",
    "Localized Name",
    "Romanized Name",
    "Phonetic Name",
    "Nickname",
    "Other",
]


# ── Standalone dialog wrapper (backwards-compatible) ──────────────────────


class ArtistAliasDialog(QDialog):
    """
    Wraps the embedded AliasesTab in a standalone QDialog.

    Kept for any call-sites that still open a separate window.  New code
    should embed AliasesTab directly (see artist_edit_alias.py).
    """

    def __init__(self, controller, artist, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Manage Aliases — {artist.artist_name}")
        self.setMinimumSize(640, 460)
        self.setModal(True)

        # Import here to avoid circular imports at module load time
        from src.artist.artist_edit_alias import AliasesTab

        layout = QVBoxLayout(self)
        self.tab = AliasesTab(controller, artist, parent=self)
        self.tab.load(artist)
        layout.addWidget(self.tab)

        btn_box = QDialogButtonBox(QDialogButtonBox.Close)
        btn_box.rejected.connect(self.accept)
        layout.addWidget(btn_box)
