"""CreditedAsDialog — lets a specific artist credit (an AlbumRoleAssociation
or TrackArtistRole row) be attributed to one of the artist's existing
aliases instead of their canonical name, e.g. crediting Prince as
"The Artist Formerly Known As" on one specific album without renaming the
artist everywhere else.
"""

from PySide6.QtWidgets import QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLabel

from src.foundation.logger_config import logger


class CreditedAsDialog(QDialog):
    """Combo-box picker: the artist's canonical name plus each of their
    existing aliases. Selecting the canonical name clears any override."""

    def __init__(self, artist, aliases, current_alias_id=None, parent=None):
        super().__init__(parent)
        logger.debug(
            f"Opening credited-as dialog for {artist.artist_name} ({len(aliases)} aliases)"
        )
        self.setWindowTitle(f"Credit {artist.artist_name} as…")
        self.setMinimumWidth(320)

        layout = QFormLayout(self)

        self._combo = QComboBox()
        self._combo.addItem(artist.artist_name, None)
        selected_index = 0
        for index, alias in enumerate(aliases, start=1):
            self._combo.addItem(alias.alias_name, alias.alias_id)
            if current_alias_id and alias.alias_id == current_alias_id:
                selected_index = index
        self._combo.setCurrentIndex(selected_index)
        layout.addRow(QLabel("Credited as:"), self._combo)

        button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addRow(button_box)

    def selected_alias_id(self):
        """The chosen alias_id, or None if the canonical name was chosen."""
        return self._combo.currentData()
