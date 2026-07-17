# ══════════════════════════════════════════════════════════════════════════════
# Widget: Types (embedded in BasicTab, not its own dialog tab)
# ══════════════════════════════════════════════════════════════════════════════
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.album.album_flowlayout import FlowLayout
from src.artist.artist_type_manager import ArtistTypeManagerDialog
from src.common.entity_completer_edit import EntityCompleterEdit, find_or_create_by_name
from src.core.logger_config import logger


class ArtistTypesWidget(QWidget):
    """
    Compact tag editor for an artist's canonical type(s) -- Composer, Producer,
    Pianist, etc. Each type is a shared ArtistType row (like Genre), so
    multiple artists can carry the same type. Lives directly on BasicTab (the
    main edit-artist tab) rather than as its own dialog tab: a small
    QCompleter search bar to add a type, plus removable pill chips for the
    types already assigned.

    Writes are immediate (add/remove commit right away via ArtistTypeAssociation
    rows), same as Aliases/Members/Influences -- not batched into ArtistEditor's
    Save.
    """

    def __init__(self, controller, artist, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.artist = artist
        self._chips = {}
        self._build_ui()
        self._refresh_completer_index()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        search_row = QHBoxLayout()
        self._search = EntityCompleterEdit("Search or add a type…")
        self._search.textChanged.connect(self._on_search_text_changed)
        self._search.returnPressed.connect(self._add)
        search_row.addWidget(self._search, 1)

        self._add_btn = QPushButton("Add")
        self._add_btn.setEnabled(False)
        self._add_btn.clicked.connect(self._add)
        search_row.addWidget(self._add_btn)

        self._manage_btn = QPushButton("Manage…")
        self._manage_btn.setFlat(True)
        self._manage_btn.setToolTip("Rename or delete artist types")
        self._manage_btn.clicked.connect(self._open_manager)
        search_row.addWidget(self._manage_btn)

        layout.addLayout(search_row)

        self._chip_area = QWidget()
        self._chip_flow = FlowLayout(self._chip_area, margin=0, h_spacing=6, v_spacing=4)
        layout.addWidget(self._chip_area)

        self._empty_label = QLabel("No types assigned")
        self._empty_label.setProperty("textRole", "note")
        layout.addWidget(self._empty_label)

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
        """Full rebuild -- used for the initial load and after the Manage
        dialog, where types/names may have changed wholesale. Add/remove of
        a single type instead update the chip row incrementally (see
        _add_chip/_remove_chip) rather than going through here, since
        tearing down and recreating every chip on every keystroke-triggered
        add caused a reentrant partial layout pass that left the row
        blank for a frame."""
        self.artist = artist
        self._clear_chips()

        types = sorted(artist.types, key=lambda t: t.type_name.lower())
        # _add_chip() calls chip.show() per widget so FlowLayout doesn't skip
        # laying it out (see its comment) -- but doing that N times in one
        # loop, back-to-back, made Qt reflow/repaint reentrantly after each
        # show() and left the last chip stuck blank for a frame. Suppressing
        # updates for the whole loop and repainting once at the end avoids
        # that entirely.
        self._chip_area.setUpdatesEnabled(False)
        try:
            for t in types:
                self._add_chip(t.artist_type_id, t.type_name)
        finally:
            self._chip_area.setUpdatesEnabled(True)

        self._empty_label.setVisible(not types)
        self._relayout()

    def _clear_chips(self):
        while self._chip_flow.count():
            item = self._chip_flow.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        self._chips = {}

    def _relayout(self):
        # FlowLayout only marks its geometry dirty (invalidate()) on
        # add/remove; without forcing activate() here the chip area is left
        # showing stale/blank geometry until something else (e.g. a window
        # resize) triggers a real layout pass.
        self._chip_flow.activate()
        self._chip_area.updateGeometry()
        self.updateGeometry()

    def _add_chip(self, artist_type_id, type_name):
        chip = QPushButton(f"{type_name}  ×")
        chip.setFlat(True)
        chip.setStyleSheet(
            "QPushButton {"
            "  border: 1px solid palette(mid);"
            "  border-radius: 10px;"
            "  padding: 2px 8px;"
            "}"
            "QPushButton:hover { background: palette(alternate-base); }"
        )
        chip.setToolTip(f"Remove '{type_name}'")
        chip.clicked.connect(lambda _checked, tid=artist_type_id: self._remove(tid))
        self._chip_flow.addWidget(chip)
        # A freshly reparented widget's "show" is deferred to the next
        # event-loop turn, so isVisible() is still False when activate()
        # runs synchronously right after -- FlowLayout skips positioning
        # invisible widgets, leaving it stuck at (0,0,640,480). Force it
        # visible now so the next layout pass actually places it.
        chip.show()
        self._chips[artist_type_id] = chip

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

        added = entity.artist_type_id not in self._chips
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
            added = False

        self._search.reset()
        self._refresh_completer_index()
        if added:
            self._add_chip(entity.artist_type_id, entity.type_name)
            self._empty_label.setVisible(False)
            self._relayout()

    def _remove(self, artist_type_id):
        try:
            self.controller.delete.delete_entity(
                "ArtistTypeAssociation",
                artist_id=self.artist.artist_id,
                artist_type_id=artist_type_id,
            )
        except Exception as e:
            logger.error(f"Failed to remove type from artist: {e}")
            return

        chip = self._chips.pop(artist_type_id, None)
        if chip is not None:
            self._chip_flow.removeWidget(chip)
            chip.setParent(None)
            chip.deleteLater()
        self._empty_label.setVisible(not self._chips)
        self._relayout()

    def _open_manager(self):
        dialog = ArtistTypeManagerDialog(self.controller, self)
        dialog.exec()
        self._refresh_completer_index()
        self.load(self.artist)
