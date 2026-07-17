# ══════════════════════════════════════════════════════════════════════════════
# Tab: Types
# ══════════════════════════════════════════════════════════════════════════════
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.artist.artist_type_manager import ArtistTypeManagerDialog
from src.common.entity_completer_edit import EntityCompleterEdit, find_or_create_by_name
from src.core.logger_config import logger


class ArtistTypesTab(QWidget):
    """
    Editor for an artist's canonical type(s) -- Composer, Producer, Pianist,
    etc. Each type is a shared ArtistType row (like Genre), so multiple
    artists can carry the same type and it's edited via a search+add bar
    plus a removable chip list, mirroring GenresTab's shape but for a single
    artist rather than a list of tracks.

    Writes are immediate (add/remove commit right away via ArtistTypeAssociation
    rows), same as Aliases/Members/Influences -- not batched into ArtistEditor's
    Save.
    """

    def __init__(self, controller, artist, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.artist = artist
        self._build_ui()
        self._refresh_completer_index()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        search_row = QHBoxLayout()
        self._search = EntityCompleterEdit("Search or add a type…")
        self._search.textChanged.connect(self._on_search_text_changed)
        self._search.returnPressed.connect(self._add)
        search_row.addWidget(self._search)

        self._add_btn = QPushButton("Add Type")
        self._add_btn.setEnabled(False)
        self._add_btn.clicked.connect(self._add)
        search_row.addWidget(self._add_btn)
        layout.addLayout(search_row)

        self._list = QListWidget()
        layout.addWidget(self._list)

        manage_row = QHBoxLayout()
        manage_row.addStretch()
        self._manage_btn = QPushButton("Manage All Types…")
        self._manage_btn.clicked.connect(self._open_manager)
        manage_row.addWidget(self._manage_btn)
        layout.addLayout(manage_row)

    def _on_search_text_changed(self, text: str):
        self._add_btn.setEnabled(bool(text.strip()))

    def _fetch_known_types(self):
        try:
            return self.controller.get.get_all_entities("ArtistType") or []
        except Exception as e:
            logger.warning(f"Could not fetch ArtistType for completer: {e}")
            return []

    def _refresh_completer_index(self):
        self._known_types = self._fetch_known_types()
        index = {
            t.type_name: t.artist_type_id for t in self._known_types if t.type_name
        }
        self._search.set_index(index)

    def load(self, artist):
        self.artist = artist
        self._list.clear()
        for t in sorted(artist.types, key=lambda t: t.type_name.lower()):
            item = QListWidgetItem(t.type_name)
            item.setData(Qt.UserRole, t.artist_type_id)
            self._list.addItem(item)

    def _add(self):
        name = self._search.text().strip()
        if not name:
            return

        matched_id = self._search.matched_id()
        try:
            if matched_id is not None:
                entity = self.controller.get.get_entity_object(
                    "ArtistType", artist_type_id=matched_id
                )
            else:
                entity = find_or_create_by_name(
                    self.controller,
                    "ArtistType",
                    "type_name",
                    name,
                    self._known_types,
                )
        except Exception as e:
            logger.error(f"Failed to find/create ArtistType: {e}")
            return
        if not entity:
            return

        try:
            self.controller.add.add_entities(
                "ArtistTypeAssociation",
                [
                    {
                        "artist_id": self.artist.artist_id,
                        "artist_type_id": entity.artist_type_id,
                    }
                ],
            )
        except Exception as e:
            logger.error(f"Failed to add type to artist: {e}")
        self._search.reset()
        self._refresh_completer_index()
        self.load(self.artist)

    def _remove_selected(self):
        item = self._list.currentItem()
        if not item:
            return
        artist_type_id = item.data(Qt.UserRole)
        try:
            self.controller.delete.delete_entity(
                "ArtistTypeAssociation",
                artist_id=self.artist.artist_id,
                artist_type_id=artist_type_id,
            )
        except Exception as e:
            logger.error(f"Failed to remove type from artist: {e}")
        self.load(self.artist)

    def _open_manager(self):
        dialog = ArtistTypeManagerDialog(self.controller, self)
        dialog.exec()
        self._refresh_completer_index()
        self.load(self.artist)

    def contextMenuEvent(self, event):
        if self._list.currentItem():
            menu = QMenu(self)
            menu.addAction("Remove", self._remove_selected)
            menu.exec(event.globalPos())
