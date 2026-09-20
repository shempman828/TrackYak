# ══════════════════════════════════════════════════════════════════════════════
# Widget: Tags (embedded in BasicTab, not its own dialog tab)
# ══════════════════════════════════════════════════════════════════════════════
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)
from sqlalchemy.exc import SQLAlchemyError

from src.album.album_flowlayout import FlowLayout
from src.artist.tag_manager import TagManagerDialog
from src.artist.tag_type_manager import TagTypeManagerDialog
from src.common.widgets.entity_completer_edit import EntityCompleterEdit
from src.common.widgets.qt_text import esc_amp
from src.foundation.logger_config import logger


class _ChipArea(QWidget):
    """See ArtistTypesWidget._ChipArea -- same FlowLayout height-tracking fix."""

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.refresh_height()

    def refresh_height(self):
        layout = self.layout()
        if layout is None:
            return
        height = layout.heightForWidth(self.width())
        if height != self.minimumHeight():
            self.setMinimumHeight(height)


class ArtistTagsWidget(QWidget):
    """
    Compact tag editor for an artist's user-defined tags. Each tag belongs to
    a TagType (chosen from the combo box before adding) and may be nested
    under a parent tag within that type. Lives directly on BasicTab, next to
    the Types group -- a Tag Type combo, a search bar to add a tag of that
    type, and removable pill chips (labeled "Type: full path") for the tags
    already assigned.

    Writes are immediate (add/remove commit right away via
    ArtistTagAssociation rows), same as Types/Aliases/Members/Influences --
    not batched into ArtistEditor's Save.
    """

    def __init__(self, controller, artist, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.artist = artist
        self._chips: dict[int, QPushButton] = {}
        self._known_tags: list = []
        self._build_ui()
        self._refresh_type_combo()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        type_row = QHBoxLayout()
        self._type_combo = QComboBox()
        self._type_combo.currentIndexChanged.connect(self._on_type_changed)
        type_row.addWidget(self._type_combo, 1)

        self._manage_types_btn = QPushButton("Manage Types…")
        self._manage_types_btn.setFlat(True)
        self._manage_types_btn.setToolTip("Add, rename, or delete tag types")
        self._manage_types_btn.clicked.connect(self._open_type_manager)
        type_row.addWidget(self._manage_types_btn)
        layout.addLayout(type_row)

        search_row = QHBoxLayout()
        self._search = EntityCompleterEdit("Search or add a tag…")
        self._search.textChanged.connect(self._on_search_text_changed)
        self._search.returnPressed.connect(self._add)
        search_row.addWidget(self._search, 1)

        self._add_btn = QPushButton("Add")
        self._add_btn.setEnabled(False)
        self._add_btn.clicked.connect(self._add)
        search_row.addWidget(self._add_btn)

        self._manage_tags_btn = QPushButton("Manage…")
        self._manage_tags_btn.setFlat(True)
        self._manage_tags_btn.setToolTip("Rename, describe, reparent, or delete tags")
        self._manage_tags_btn.clicked.connect(self._open_tag_manager)
        search_row.addWidget(self._manage_tags_btn)

        layout.addLayout(search_row)

        self._chip_area = _ChipArea()
        self._chip_flow = FlowLayout(self._chip_area, margin=0, h_spacing=6, v_spacing=4)
        layout.addWidget(self._chip_area)

        self._empty_label = QLabel("No tags assigned")
        self._empty_label.setProperty("textRole", "note")
        layout.addWidget(self._empty_label)

        self._no_types_label = QLabel("No tag types yet — click Manage Types… to create one.")
        self._no_types_label.setProperty("textRole", "note")
        self._no_types_label.setVisible(False)
        layout.addWidget(self._no_types_label)

    # ── Tag Type combo ──────────────────────────────────────────────────────

    def _fetch_tag_types(self):
        try:
            return sorted(
                self.controller.get.get_all_entities("TagType") or [],
                key=lambda t: t.type_name.lower(),
            )
        except SQLAlchemyError as e:
            logger.warning(f"Could not fetch TagType for tag editor: {e}")
            return []

    def _refresh_type_combo(self, *, keep_selection: bool = True):
        previous_id = self.current_type_id() if keep_selection else None
        tag_types = self._fetch_tag_types()

        self._type_combo.blockSignals(True)
        self._type_combo.clear()
        for t in tag_types:
            self._type_combo.addItem(t.type_name, t.tag_type_id)
        self._type_combo.blockSignals(False)

        has_types = bool(tag_types)
        self._type_combo.setEnabled(has_types)
        self._search.setEnabled(has_types)
        self._no_types_label.setVisible(not has_types)

        if has_types:
            ids = [t.tag_type_id for t in tag_types]
            index = ids.index(previous_id) if previous_id in ids else 0
            self._type_combo.setCurrentIndex(index)
            # setCurrentIndex(0) is a no-op (no signal) if the combo was
            # already showing row 0 -- refresh the completer index directly
            # in that case rather than relying on currentIndexChanged.
            if index == 0:
                self._refresh_completer_index()
        else:
            self._known_tags = []
            self._search.set_index({})

    def current_type_id(self):
        return self._type_combo.currentData()

    def _on_type_changed(self, _index: int):
        self._refresh_completer_index()

    def _refresh_completer_index(self):
        type_id = self.current_type_id()
        if type_id is None:
            self._known_tags = []
            self._search.set_index({})
            return
        try:
            self._known_tags = (
                self.controller.get.get_all_entities("Tag", tag_type_id=type_id) or []
            )
        except SQLAlchemyError as e:
            logger.warning(f"Could not fetch Tag for completer: {e}")
            self._known_tags = []
        index = {t.full_tag_path: t.tag_id for t in self._known_tags if t.tag_name}
        self._search.set_index(index)

    def _open_type_manager(self):
        dialog = TagTypeManagerDialog(self.controller, self)
        dialog.exec()
        self._refresh_type_combo()
        self.load(self.artist)

    def _open_tag_manager(self):
        dialog = TagManagerDialog(self.controller, self, initial_tag_type_id=self.current_type_id())
        dialog.exec()
        self._refresh_type_combo()
        self.load(self.artist)

    # ── Chips ────────────────────────────────────────────────────────────

    def _on_search_text_changed(self, text: str):
        self._add_btn.setEnabled(bool(text.strip()) and self.current_type_id() is not None)

    def load(self, artist):
        """Full rebuild -- used for the initial load and after the Manage
        dialogs, where tags/types may have changed wholesale. Add/remove of
        a single tag instead update the chip row incrementally (see
        _add_chip/_remove_chip) -- see ArtistTypesWidget.load for why."""
        self.artist = artist
        self._clear_chips()

        tags = sorted(artist.tags, key=lambda t: (t.tag_type.type_name.lower(), t.tag_name.lower()))
        self._chip_area.setUpdatesEnabled(False)
        try:
            for t in tags:
                self._add_chip(t.tag_id, self._chip_label(t))
        finally:
            self._chip_area.setUpdatesEnabled(True)

        self._empty_label.setVisible(not tags)
        self._relayout()
        if tags:
            self._flush_new_chip_paint()

    def _chip_label(self, tag) -> str:
        return f"{tag.tag_type.type_name}: {tag.full_tag_path}"

    def _clear_chips(self):
        while self._chip_flow.count():
            item = self._chip_flow.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        self._chips = {}

    def _relayout(self):
        self._chip_flow.activate()
        self._chip_area.refresh_height()
        self._chip_area.updateGeometry()
        self.updateGeometry()

    def _flush_new_chip_paint(self):
        """See ArtistTypesWidget._flush_new_chip_paint for why this is
        deferred rather than run synchronously."""
        QTimer.singleShot(0, QApplication.processEvents)

    def _add_chip(self, tag_id, label):
        chip = QPushButton(f"{esc_amp(label)}  \u00d7")
        chip.setFlat(True)
        chip.setProperty("class", "typeChip")
        chip.setToolTip(f"Remove '{label}'")
        chip.clicked.connect(lambda _checked, tid=tag_id: self._remove(tid))
        self._chip_flow.addWidget(chip)
        chip.show()
        self._chips[tag_id] = chip

    # ── Add / remove ─────────────────────────────────────────────────────

    def _find_or_create_tag(self, name: str, tag_type_id: int):
        lowered = name.strip().lower()
        for t in self._known_tags:
            if (t.tag_name or "").strip().lower() == lowered:
                return t
        return self.controller.add.add_entity("Tag", tag_name=name, tag_type_id=tag_type_id)

    def _add(self):
        type_id = self.current_type_id()
        if type_id is None:
            return

        names = self._search.split_names()
        if not names:
            return

        entities = []
        try:
            for name in names:
                entity = self._find_or_create_tag(name, type_id)
                if entity:
                    entities.append(entity)
        except SQLAlchemyError as e:
            logger.error(f"Failed to find/create Tag: {e}")
            return
        if not entities:
            return

        new_entities = [e for e in entities if e.tag_id not in self._chips]
        if new_entities:
            try:
                self.controller.add.add_entities(
                    "ArtistTagAssociation",
                    [
                        {"artist_id": self.artist.artist_id, "tag_id": e.tag_id}
                        for e in new_entities
                    ],
                )
            except SQLAlchemyError as e:
                logger.error(f"Failed to add tag to artist: {e}")
                new_entities = []

        self._search.reset()
        self._refresh_completer_index()
        if new_entities:
            for e in new_entities:
                self._add_chip(e.tag_id, self._chip_label(e))
            self._empty_label.setVisible(False)
            self._relayout()
            self._flush_new_chip_paint()

    def _remove(self, tag_id):
        try:
            self.controller.delete.delete_entity(
                "ArtistTagAssociation", artist_id=self.artist.artist_id, tag_id=tag_id
            )
        except SQLAlchemyError as e:
            logger.error(f"Failed to remove tag from artist: {e}")
            return

        chip = self._chips.pop(tag_id, None)
        if chip is not None:
            self._chip_flow.removeWidget(chip)
            chip.setParent(None)
            chip.deleteLater()
        self._empty_label.setVisible(not self._chips)
        self._relayout()
