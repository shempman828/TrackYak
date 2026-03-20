"""
artist_edit.py

Tabbed dialog for editing every field and relationship of an Artist.
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QMessageBox,
    QTabWidget,
    QVBoxLayout,
)

from src.artist_edit_advanced import AdvancedTab
from src.artist_edit_alias import AliasesTab
from src.artist_edit_basic import BasicTab
from src.artist_edit_bio import BiographyTab
from src.artist_edit_discog import DiscographyTab
from src.artist_edit_influences import InfluencesTab
from src.artist_edit_member import MembersTab
from src.artist_edit_placesawards import PlacesAwardsTab
from src.logger_config import logger

# ══════════════════════════════════════════════════════════════════════════════
# Main dialog shell
# ══════════════════════════════════════════════════════════════════════════════


class ArtistEditor(QDialog):
    """
    Thin shell dialog that assembles the tab widgets and handles Save/Cancel.

    All UI logic, loading, and relationship editing lives inside the tab classes.
    Save collects changes from BasicTab, BiographyTab, and AdvancedTab and
    commits them in a single update_entity call.
    """

    def __init__(self, controller, artist, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.artist = artist

        self.setWindowTitle(f"Edit Artist: {artist.artist_name}")
        self.setMinimumSize(920, 680)
        # NonModal = dialog is fully independent; user can move it freely
        self.setWindowModality(Qt.NonModal)

        self._init_ui()
        self._load_all()

    def _init_ui(self):
        root = QVBoxLayout(self)
        root.setSpacing(8)

        hdr = QLabel(
            f"<b style='font-size:15px'>Editing: {self.artist.artist_name}</b>"
        )
        root.addWidget(hdr)

        # Instantiate tabs
        self.tab_basic = BasicTab(self.controller, self.artist)
        self.tab_biography = BiographyTab(self.controller, self.artist)
        self.tab_aliases = AliasesTab(self.controller, self.artist)
        self.tab_members = MembersTab(self.controller, self.artist)
        self.tab_influences = InfluencesTab(self.controller, self.artist)
        self.tab_places_awards = PlacesAwardsTab(self.controller, self.artist)
        self.tab_discography = DiscographyTab(self.controller, self.artist)
        self.tab_advanced = AdvancedTab(self.controller, self.artist)

        # Wire isgroup checkbox -> MembersTab so the right panel shows immediately
        self.tab_basic.isgroup_check.toggled.connect(self.tab_members.update_visibility)

        tabs = QTabWidget()
        tabs.addTab(self.tab_basic, "Basic")
        tabs.addTab(self.tab_biography, "Biography")
        tabs.addTab(self.tab_aliases, "Aliases")
        tabs.addTab(self.tab_members, "Members")
        tabs.addTab(self.tab_influences, "Influences")
        tabs.addTab(self.tab_places_awards, "Places & Awards")
        tabs.addTab(self.tab_discography, "Discography")
        tabs.addTab(self.tab_advanced, "Advanced")
        root.addWidget(tabs)

        btns = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        btns.accepted.connect(self._save)
        btns.rejected.connect(self.reject)
        root.addWidget(btns)

    def _load_all(self):
        self.tab_basic.load(self.artist)
        self.tab_biography.load(self.artist)
        self.tab_aliases.load(self.artist)
        self.tab_members.load(self.artist)
        self.tab_influences.load(self.artist)
        self.tab_places_awards.load(self.artist)
        self.tab_discography.load(self.artist)
        self.tab_advanced.load(self.artist)

    def _save(self):
        name = self.tab_basic.name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "Validation", "Artist name cannot be empty.")
            return

        kwargs = {}
        kwargs.update(self.tab_basic.collect_changes())
        kwargs.update(self.tab_biography.collect_changes())
        kwargs.update(self.tab_advanced.collect_changes())

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
