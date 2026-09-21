# ══════════════════════════════════════════════════════════════════════════════
# Tab: Tags
# ══════════════════════════════════════════════════════════════════════════════
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QFrame, QGroupBox, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget
from sqlalchemy.exc import SQLAlchemyError

from src.album.album_flowlayout import ChipArea, FlowLayout
from src.artist.tag_manager import TagManagerDialog
from src.artist.tag_type_manager import TagTypeManagerDialog
from src.common.widgets.entity_completer_edit import EntityCompleterEdit
from src.common.widgets.qt_text import esc_amp
from src.foundation.logger_config import logger


class _TagTypeSection(QGroupBox):
    """One TagType's slice of the Tags tab: assigned-tag chips, suggestion
    pills for the type's other known tags, and a search box to add or
    find-or-create a tag. Writes are immediate, not batched into
    ArtistEditor's Save (same as Types/Aliases/Members/Influences)."""

    def __init__(self, controller, artist, tag_type, parent=None):
        super().__init__(esc_amp(tag_type.type_name), parent)
        self.controller = controller
        self.artist = artist
        self.tag_type = tag_type
        self._known_tags: list = []
        self._assigned_chips: dict[int, QPushButton] = {}
        self._suggestion_pills: dict[int, QPushButton] = {}
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(6)

        search_row = QHBoxLayout()
        self._search = EntityCompleterEdit(f"Search or add a {self.tag_type.type_name} tag…")
        self._search.textChanged.connect(self._on_search_text_changed)
        self._search.returnPressed.connect(self._add)
        search_row.addWidget(self._search, 1)

        self._add_btn = QPushButton("Add")
        self._add_btn.setEnabled(False)
        self._add_btn.clicked.connect(self._add)
        search_row.addWidget(self._add_btn)

        self._manage_btn = QPushButton("Manage…")
        self._manage_btn.setFlat(True)
        self._manage_btn.setToolTip(f"Rename, describe, reparent, or delete {self.tag_type.type_name} tags")
        self._manage_btn.clicked.connect(self._open_tag_manager)
        search_row.addWidget(self._manage_btn)
        layout.addLayout(search_row)

        self._suggestion_area = ChipArea()
        self._suggestion_flow = FlowLayout(self._suggestion_area, margin=0, h_spacing=6, v_spacing=4)
        layout.addWidget(self._suggestion_area)

        self._no_suggestions_label = QLabel("No tags to suggest yet — add one above")
        self._no_suggestions_label.setProperty("textRole", "note")
        layout.addWidget(self._no_suggestions_label)

        self._assigned_area = ChipArea()
        self._assigned_flow = FlowLayout(self._assigned_area, margin=0, h_spacing=6, v_spacing=4)
        layout.addWidget(self._assigned_area)

        self._empty_label = QLabel("No tags assigned")
        self._empty_label.setProperty("textRole", "note")
        layout.addWidget(self._empty_label)

    # ── Loading ──────────────────────────────────────────────────────────

    def _fetch_known_tags(self):
        try:
            return sorted(self.controller.get.get_all_entities("Tag", tag_type_id=self.tag_type.tag_type_id) or [], key=lambda t: t.tag_name.lower())
        except SQLAlchemyError as e:
            logger.warning(f"Could not fetch Tag for tag editor: {e}")
            return []

    def load(self, artist):
        """Full rebuild of both chip rows -- used for the initial load and
        after the Manage dialogs; single add/remove instead update the rows
        incrementally (see _move_to_assigned and _remove)."""
        self.artist = artist
        self._known_tags = self._fetch_known_tags()

        assigned = sorted((t for t in artist.tags if t.tag_type_id == self.tag_type.tag_type_id), key=lambda t: t.tag_name.lower())
        assigned_ids = {t.tag_id for t in assigned}
        suggestions = [t for t in self._known_tags if t.tag_id not in assigned_ids]

        self._clear_area(self._assigned_flow, self._assigned_chips)
        self._assigned_area.setUpdatesEnabled(False)
        try:
            for t in assigned:
                self._add_assigned_chip(t.tag_id, t.full_tag_path)
        finally:
            self._assigned_area.setUpdatesEnabled(True)
        self._empty_label.setVisible(not assigned)

        self._clear_area(self._suggestion_flow, self._suggestion_pills)
        self._suggestion_area.setUpdatesEnabled(False)
        try:
            for t in suggestions:
                self._add_suggestion_pill(t.tag_id, t.full_tag_path)
        finally:
            self._suggestion_area.setUpdatesEnabled(True)
        self._no_suggestions_label.setVisible(not suggestions)

        self._refresh_completer_index()
        self._relayout()
        if assigned or suggestions:
            self._flush_new_chip_paint()

    def _refresh_completer_index(self):
        index = {t.full_tag_path: t.tag_id for t in self._known_tags if t.tag_name}
        self._search.set_index(index)

    def _open_tag_manager(self):
        dialog = TagManagerDialog(self.controller, self, initial_tag_type_id=self.tag_type.tag_type_id)
        dialog.exec()
        self.load(self.artist)

    # ── Chips / pills ────────────────────────────────────────────────────

    def _on_search_text_changed(self, text: str):
        self._add_btn.setEnabled(bool(text.strip()))

    def _clear_area(self, flow, chips: dict):
        while flow.count():
            item = flow.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        chips.clear()

    def _relayout(self):
        for flow, area in ((self._assigned_flow, self._assigned_area), (self._suggestion_flow, self._suggestion_area)):
            flow.activate()
            area.refresh_height()
            area.updateGeometry()
        self.updateGeometry()

    def _flush_new_chip_paint(self):
        """See ArtistTypesWidget._flush_new_chip_paint for why this is
        deferred rather than run synchronously."""
        QTimer.singleShot(0, QApplication.processEvents)

    def _add_assigned_chip(self, tag_id, label):
        chip = QPushButton(f"{esc_amp(label)}  \u00d7")
        chip.setFlat(True)
        chip.setProperty("class", "typeChip")
        chip.setToolTip(f"Remove '{label}'")
        chip.clicked.connect(lambda _checked, tid=tag_id: self._remove(tid))
        self._assigned_flow.addWidget(chip)
        chip.show()
        self._assigned_chips[tag_id] = chip

    def _add_suggestion_pill(self, tag_id, label):
        pill = QPushButton(f"+ {esc_amp(label)}")
        pill.setFlat(True)
        pill.setProperty("class", "typeChip")
        pill.setToolTip(f"Tag this artist with '{label}'")
        pill.clicked.connect(lambda _checked, tid=tag_id: self._add_existing(tid))
        self._suggestion_flow.addWidget(pill)
        pill.show()
        self._suggestion_pills[tag_id] = pill

    # ── Add / remove ─────────────────────────────────────────────────────

    def _find_or_create_tag(self, name: str):
        # Matches against full_tag_path too, not just the bare tag_name: the
        # completer index and every chip/pill label are keyed by
        # full_tag_path (e.g. "Christian > Catholic"), so picking a nested
        # tag's suggestion writes its full path into the field. Matching on
        # tag_name alone would miss it and create a bogus new top-level tag
        # literally named "Christian > Catholic".
        lowered = name.strip().lower()
        for t in self._known_tags:
            if (t.tag_name or "").strip().lower() == lowered:
                return t
            if (t.full_tag_path or "").strip().lower() == lowered:
                return t
        tag = self.controller.add.add_entity("Tag", tag_name=name, tag_type_id=self.tag_type.tag_type_id)
        if tag is not None:
            self._known_tags.append(tag)
        return tag

    def _add(self):
        names = self._search.split_names()
        if not names:
            return

        # A single typed name that matched a completer suggestion resolves
        # straight to its id, bypassing _find_or_create_tag's name-based
        # lookup entirely (see its comment for why that matters for nested
        # tags) -- same shortcut as ArtistTypesWidget._add().
        single_matched_id = self._search.matched_id() if len(names) == 1 else None
        entities = []
        try:
            for name in names:
                entity = self.controller.get.get_entity_object("Tag", tag_id=single_matched_id) if single_matched_id is not None else self._find_or_create_tag(name)
                if entity:
                    entities.append(entity)
        except SQLAlchemyError as e:
            logger.error(f"Failed to find/create Tag: {e}")
            return
        if not entities:
            return

        seen_ids: set = set()
        new_entities = []
        for e in entities:
            if e.tag_id in self._assigned_chips or e.tag_id in seen_ids:
                continue
            seen_ids.add(e.tag_id)
            new_entities.append(e)

        if new_entities:
            # add_entities() reports failure by returning fewer rows than
            # asked for, not by raising -- it catches its own DB errors
            # internally. Checking the count (rather than assuming success
            # whenever no exception surfaces) is what keeps a chip from
            # showing as assigned when the association was never persisted.
            added = self.controller.add.add_entities("ArtistTagAssociation", [{"artist_id": self.artist.artist_id, "tag_id": e.tag_id} for e in new_entities])
            added_ids = {assoc.tag_id for assoc in added}
            new_entities = [e for e in new_entities if e.tag_id in added_ids]

        self._search.reset()
        self._refresh_completer_index()
        if new_entities:
            for e in new_entities:
                self._move_to_assigned(e.tag_id, e.full_tag_path)
            self._relayout()
            self._flush_new_chip_paint()

    def _add_existing(self, tag_id):
        if tag_id in self._assigned_chips:
            return
        try:
            self.controller.add.add_entity("ArtistTagAssociation", artist_id=self.artist.artist_id, tag_id=tag_id)
        except SQLAlchemyError as e:
            logger.error(f"Failed to add tag to artist: {e}")
            return

        tag = next((t for t in self._known_tags if t.tag_id == tag_id), None)
        label = tag.full_tag_path if tag is not None else ""
        self._move_to_assigned(tag_id, label)
        self._relayout()
        self._flush_new_chip_paint()

    def _move_to_assigned(self, tag_id, label):
        pill = self._suggestion_pills.pop(tag_id, None)
        if pill is not None:
            self._suggestion_flow.removeWidget(pill)
            pill.setParent(None)
            pill.deleteLater()
        self._no_suggestions_label.setVisible(not self._suggestion_pills)

        self._add_assigned_chip(tag_id, label)
        self._empty_label.setVisible(False)

    def _remove(self, tag_id):
        try:
            self.controller.delete.delete_entity("ArtistTagAssociation", artist_id=self.artist.artist_id, tag_id=tag_id)
        except SQLAlchemyError as e:
            logger.error(f"Failed to remove tag from artist: {e}")
            return

        chip = self._assigned_chips.pop(tag_id, None)
        if chip is not None:
            self._assigned_flow.removeWidget(chip)
            chip.setParent(None)
            chip.deleteLater()
        self._empty_label.setVisible(not self._assigned_chips)

        tag = next((t for t in self._known_tags if t.tag_id == tag_id), None)
        if tag is not None:
            self._add_suggestion_pill(tag_id, tag.full_tag_path)
            self._no_suggestions_label.setVisible(False)
        self._relayout()


class ArtistTagsTab(QWidget):
    """Dialog tab showing every configured TagType as its own section, in
    the user's configured category order."""

    def __init__(self, controller, artist, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.artist = artist
        self._sections: dict[int, _TagTypeSection] = {}
        self._build_ui()

    def _build_ui(self):
        outer = QVBoxLayout(self)

        top_row = QHBoxLayout()
        top_row.addStretch()
        self._manage_types_btn = QPushButton("Manage Types…")
        self._manage_types_btn.setFlat(True)
        self._manage_types_btn.setToolTip("Add, rename, delete, or reorder tag categories")
        self._manage_types_btn.clicked.connect(self._open_type_manager)
        top_row.addWidget(self._manage_types_btn)
        outer.addLayout(top_row)

        self._no_types_label = QLabel("No tag types yet — click Manage Types… to create one.")
        self._no_types_label.setProperty("textRole", "note")
        self._no_types_label.setVisible(False)
        outer.addWidget(self._no_types_label)

        self._sections_container = QWidget()
        self._sections_layout = QVBoxLayout(self._sections_container)
        self._sections_layout.setContentsMargins(0, 0, 0, 0)
        self._sections_layout.setSpacing(14)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(self._sections_container)
        outer.addWidget(scroll, 1)

    def load(self, artist):
        self.artist = artist
        self._rebuild_sections()

    def _fetch_tag_types(self):
        try:
            return sorted(self.controller.get.get_all_entities("TagType") or [], key=lambda t: (t.sort_order, t.type_name.lower()))
        except SQLAlchemyError as e:
            logger.warning(f"Could not fetch TagType for Tags tab: {e}")
            return []

    def _rebuild_sections(self):
        while self._sections_layout.count():
            item = self._sections_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        self._sections = {}

        tag_types = self._fetch_tag_types()
        self._no_types_label.setVisible(not tag_types)

        for tag_type in tag_types:
            section = _TagTypeSection(self.controller, self.artist, tag_type)
            section.load(self.artist)
            self._sections_layout.addWidget(section)
            self._sections[tag_type.tag_type_id] = section
        self._sections_layout.addStretch()

    def _open_type_manager(self):
        dialog = TagTypeManagerDialog(self.controller, self)
        dialog.exec()
        self._rebuild_sections()
