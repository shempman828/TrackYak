"""Inline detail panel for the place list: parent-chain breadcrumb, name,
metadata badges, actions, description, and the music connected to the
place. Replaces the modal details and associations dialogs in the List tab.

The panel only shows data; the owning ListView wires its buttons to the
list's actions. Associations are queried lazily -- after a short debounce,
and only while the panel is visible -- so arrow-keying through the tree
does not run a query per row passed.
"""

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QTextDocument
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea, QStackedWidget, QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget
from sqlalchemy.exc import SQLAlchemyError

from src.common.widgets.detail_card import DetailCard
from src.common.widgets.flow_layout import ChipArea, FlowLayout
from src.common.widgets.layout_utils import clear_layout
from src.common.widgets.qt_text import esc_amp
from src.common.widgets.segmented_control import SegmentedControl
from src.common.widgets.style_utils import refresh_style
from src.foundation.logger_config import logger
from src.place.place_assoc_details import GROUP_AUTO_EXPAND_THRESHOLD, entity_display_name, entity_tooltip, fetch_entity, fetch_place_associations
from src.place.place_types import NO_TYPE_LABEL, type_label

_LOAD_DEBOUNCE_MS = 120
# Show the music filter box only when there is enough to filter.
_FILTER_MIN_ROWS = 10
# Lines of description shown before "Show more".
_DESCRIPTION_PREVIEW_LINES = 6

_PAGE_EMPTY, _PAGE_PLACE, _PAGE_MULTI = range(3)


class PlaceDetailPanel(QWidget):
    """Detail pane for the place selected in the list (or a multi-selection summary)."""

    ancestor_clicked = Signal(int)  # place_id of a breadcrumb entry

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.setObjectName("PlaceDetailPanel")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.controller = controller
        self.place = None
        self._counts = (0, 0)
        self._load_pending = False
        self._description_expanded = False

        self._load_timer = QTimer(self)
        self._load_timer.setSingleShot(True)
        self._load_timer.setInterval(_LOAD_DEBOUNCE_MS)
        self._load_timer.timeout.connect(self._load_associations)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.pages = QStackedWidget()
        layout.addWidget(self.pages)

        self.pages.addWidget(self._build_empty_page())
        self.pages.addWidget(self._build_place_page())
        self.pages.addWidget(self._build_multi_page())
        self.pages.setCurrentIndex(_PAGE_EMPTY)

    # ── construction ──────────────────────────────────────────────────────

    def _build_empty_page(self):
        page = QWidget()
        page_layout = QVBoxLayout(page)
        label = QLabel("Select a place to see its details and connected music.")
        label.setProperty("textRole", "placeholder")
        label.setAlignment(Qt.AlignCenter)
        label.setWordWrap(True)
        page_layout.addWidget(label, alignment=Qt.AlignCenter)
        return page

    def _build_place_page(self):
        page = QWidget()
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(16, 12, 16, 12)
        page_layout.setSpacing(10)

        self.breadcrumb = ChipArea()
        self.breadcrumb.setObjectName("PlaceBreadcrumb")
        self.breadcrumb_layout = FlowLayout(self.breadcrumb, spacing=2)
        page_layout.addWidget(self.breadcrumb)

        self.title_label = QLabel()
        self.title_label.setObjectName("PlaceTitle")
        self.title_label.setWordWrap(True)
        self.title_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        page_layout.addWidget(self.title_label)

        meta_row = QHBoxLayout()
        meta_row.setSpacing(6)
        self.type_badge = QLabel()
        self.type_badge.setProperty("badgeState", "neutral")
        self.coords_label = QLabel()
        self.coords_label.setProperty("textRole", "muted")
        self.coords_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.mbid_badge = QLabel()
        self.coords_badge = QLabel("No coordinates")
        self.coords_badge.setProperty("badgeState", "warn")
        for widget in (self.type_badge, self.coords_label, self.coords_badge, self.mbid_badge):
            meta_row.addWidget(widget)
        meta_row.addStretch()
        page_layout.addLayout(meta_row)

        actions = QHBoxLayout()
        actions.setSpacing(6)
        self.edit_button = QPushButton("Edit")
        self.map_button = QPushButton("Show on Map")
        self.more_button = QToolButton()
        self.more_button.setObjectName("PlaceMoreButton")
        self.more_button.setText("⋯")
        self.more_button.setToolTip("More actions")
        actions.addWidget(self.edit_button)
        actions.addWidget(self.map_button)
        actions.addWidget(self.more_button)
        actions.addStretch()
        page_layout.addLayout(actions)

        # Description
        self.description_card = DetailCard("Description")
        self.description_toggle = QPushButton("Show more")
        self.description_toggle.setProperty("linkButton", True)
        self.description_toggle.clicked.connect(self._toggle_description)
        self.description_card.add_header_action(self.description_toggle)
        self.description_scroll = QScrollArea()
        self.description_scroll.setWidgetResizable(True)
        self.description_scroll.setFrameShape(QFrame.NoFrame)
        self.description_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.description_label = QLabel()
        self.description_label.setObjectName("PlaceDescription")
        self.description_label.setWordWrap(True)
        self.description_label.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.description_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.description_scroll.setWidget(self.description_label)
        self.description_card.body.addWidget(self.description_scroll)
        page_layout.addWidget(self.description_card)

        # Connected music
        self.music_card = DetailCard("Connected Music")
        self.scope_control = SegmentedControl(["Direct", "With children"])
        self.scope_control.setItemToolTip(0, "Music connected to this place only")
        self.scope_control.setItemToolTip(1, "Also include music connected to places inside this one")
        self.scope_control.currentIndexChanged.connect(self._on_scope_changed)
        self.music_card.add_header_action(self.scope_control)

        self.music_filter = QLineEdit()
        self.music_filter.setPlaceholderText("Filter connected music…")
        self.music_filter.setClearButtonEnabled(True)
        self.music_filter.textChanged.connect(self._filter_music)
        self.music_card.body.addWidget(self.music_filter)

        self.music_tree = QTreeWidget()
        self.music_tree.setObjectName("PlaceMusicTree")
        self.music_tree.setHeaderLabels(["Name", "Connection", "Via"])
        self.music_tree.setRootIsDecorated(True)
        self.music_tree.setUniformRowHeights(True)
        self.music_card.body.addWidget(self.music_tree, 1)

        self.music_message = QLabel()
        self.music_message.setProperty("textRole", "note")
        self.music_message.setAlignment(Qt.AlignCenter)
        self.music_message.setWordWrap(True)
        self.music_card.body.addWidget(self.music_message)
        page_layout.addWidget(self.music_card, 1)
        return page

    def _build_multi_page(self):
        page = QWidget()
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(16, 12, 16, 12)
        page_layout.addStretch()
        self.multi_title = QLabel()
        self.multi_title.setObjectName("PlaceTitle")
        self.multi_title.setAlignment(Qt.AlignCenter)
        page_layout.addWidget(self.multi_title)
        hint = QLabel("Edit them together, or drag them onto a new parent in the tree.")
        hint.setProperty("textRole", "muted")
        hint.setAlignment(Qt.AlignCenter)
        hint.setWordWrap(True)
        page_layout.addWidget(hint)
        buttons = QHBoxLayout()
        buttons.addStretch()
        self.multi_edit_button = QPushButton()
        self.multi_delete_button = QPushButton()
        self.multi_delete_button.setProperty("danger", True)
        buttons.addWidget(self.multi_edit_button)
        buttons.addWidget(self.multi_delete_button)
        buttons.addStretch()
        page_layout.addLayout(buttons)
        page_layout.addStretch()
        return page

    # ── public API ────────────────────────────────────────────────────────

    def clear(self):
        """Show the empty state."""
        self.place = None
        self._load_timer.stop()
        self._load_pending = False
        self.pages.setCurrentIndex(_PAGE_EMPTY)

    def show_multi(self, count):
        """Show the multi-selection summary for `count` selected places."""
        self.place = None
        self._load_timer.stop()
        self._load_pending = False
        self.multi_title.setText(f"{count} places selected")
        self.multi_edit_button.setText(f"Edit {count} Places…")
        self.multi_delete_button.setText(f"Delete {count} Places")
        self.pages.setCurrentIndex(_PAGE_MULTI)

    def set_place(self, place, ancestors, counts):
        """Show `place`. `ancestors` is its parent chain, root first;
        `counts` is its (direct, recursive) association counts."""
        same_place = self.place is not None and self.place.place_id == place.place_id
        self.place = place
        self._counts = counts
        self.pages.setCurrentIndex(_PAGE_PLACE)

        self._fill_breadcrumb(ancestors)
        self.title_label.setText(place.place_name or "")
        label = type_label(place.place_type)
        self.type_badge.setText(label)
        self.type_badge.setProperty("badgeState", "warn" if label == NO_TYPE_LABEL else "neutral")
        refresh_style(self.type_badge)

        has_coords = place.place_latitude is not None and place.place_longitude is not None
        self.coords_label.setVisible(has_coords)
        self.coords_badge.setVisible(not has_coords)
        if has_coords:
            self.coords_label.setText(f"{place.place_latitude:.4f}, {place.place_longitude:.4f}")
        self.map_button.setEnabled(has_coords)
        self.map_button.setToolTip("" if has_coords else "This place has no coordinates")

        if place.MBID:
            self.mbid_badge.setText("MusicBrainz")
            self.mbid_badge.setToolTip(f"MBID: {place.MBID}")
            self.mbid_badge.setProperty("badgeState", "good")
        else:
            self.mbid_badge.setText("No MBID")
            self.mbid_badge.setToolTip("This place is not linked to MusicBrainz")
            self.mbid_badge.setProperty("badgeState", "warn")
        refresh_style(self.mbid_badge)

        description = (place.place_description or "").strip()
        self.description_card.setVisible(bool(description))
        self.description_label.setText(description)
        if not same_place:
            self._description_expanded = False
        self._apply_description_height()

        direct, recursive = counts
        # "With children" only means something when children add music.
        self.scope_control.setVisible(recursive > direct)
        if recursive <= direct and self.scope_control.currentIndex() != 0:
            self.scope_control.blockSignals(True)
            self.scope_control.setCurrentIndex(0)
            self.scope_control.blockSignals(False)
        self._update_music_count()

        if not same_place:
            self.music_tree.clear()
            self.music_filter.clear()
            self.music_filter.hide()
            self.music_tree.hide()
            self.music_message.setText("Loading…")
            self.music_message.show()
        self._schedule_load()

    # ── associations ──────────────────────────────────────────────────────

    def _recursive(self):
        return self.scope_control.currentIndex() == 1

    def _update_music_count(self):
        direct, recursive = self._counts
        self.music_card.set_count(recursive if self._recursive() else direct, "item")

    def _on_scope_changed(self, _index):
        self._update_music_count()
        self._schedule_load()

    def _schedule_load(self):
        if self.isVisible():
            self._load_timer.start()
        else:
            self._load_pending = True

    def showEvent(self, event):
        super().showEvent(event)
        if self._load_pending and self.place is not None:
            self._load_pending = False
            self._load_timer.start()

    def _load_associations(self):
        place = self.place
        if place is None:
            return
        recursive = self._recursive()
        self.music_tree.clear()
        self.music_tree.setColumnHidden(2, not recursive)
        try:
            associations = fetch_place_associations(self.controller, place.place_id, recursive=recursive)
            groups = {}
            for assoc in associations:
                entity_type = assoc.entity_type or "Unknown"
                entity = fetch_entity(self.controller, assoc.entity_type, assoc.entity_id)
                groups.setdefault(entity_type, []).append((assoc, entity))
        except (SQLAlchemyError, RuntimeError) as e:
            logger.exception("Error loading place associations")
            self._show_music_message(f"Could not load connected music: {e}")
            return

        if not groups:
            self._show_music_message("No music is connected to this place." if not recursive else "No music is connected to this place or the places inside it.")
            return

        total = 0
        for entity_type, rows in sorted(groups.items()):
            group = QTreeWidgetItem([f"{entity_type.title()}s ({len(rows)})"])
            font = group.font(0)
            font.setBold(True)
            group.setFont(0, font)
            group.setFirstColumnSpanned(False)
            self.music_tree.addTopLevelItem(group)
            for assoc, entity in sorted(rows, key=lambda r: str(entity_display_name(r[1], r[0].entity_type) if r[1] else "").lower()):
                name = entity_display_name(entity, assoc.entity_type) if entity else f"Unknown {assoc.entity_type} (ID: {assoc.entity_id})"
                connection = assoc.association_type.type_name if assoc.association_type else ""
                via = getattr(assoc, "place_path", "")
                child = QTreeWidgetItem([str(name), connection, via])
                if entity:
                    child.setToolTip(0, entity_tooltip(entity, assoc.entity_type) + (f"\nVia: {via}" if via else ""))
                group.addChild(child)
            group.setExpanded(len(rows) <= GROUP_AUTO_EXPAND_THRESHOLD)
            total += len(rows)

        for column in range(3):
            self.music_tree.resizeColumnToContents(column)
        self.music_message.hide()
        self.music_tree.show()
        self.music_filter.setVisible(total > _FILTER_MIN_ROWS)
        if self.music_filter.text():
            self._filter_music(self.music_filter.text())

    def _show_music_message(self, text):
        self.music_tree.hide()
        self.music_filter.hide()
        self.music_message.setText(text)
        self.music_message.show()

    def _filter_music(self, text):
        needle = text.strip().lower()
        for i in range(self.music_tree.topLevelItemCount()):
            group = self.music_tree.topLevelItem(i)
            any_visible = False
            for c in range(group.childCount()):
                child = group.child(c)
                matches = not needle or needle in " ".join(child.text(col) for col in range(3)).lower()
                child.setHidden(not matches)
                any_visible = any_visible or matches
            group.setHidden(not any_visible)
            if needle:
                group.setExpanded(any_visible)
            else:
                group.setExpanded(group.childCount() <= GROUP_AUTO_EXPAND_THRESHOLD)

    # ── helpers ───────────────────────────────────────────────────────────

    def _fill_breadcrumb(self, ancestors):
        clear_layout(self.breadcrumb_layout)
        self.breadcrumb.setVisible(bool(ancestors))
        for i, ancestor in enumerate(ancestors):
            if i:
                separator = QLabel("\u203a")
                separator.setProperty("textRole", "muted")
                self._add_crumb(separator)
            link = QPushButton(esc_amp(ancestor.place_name or ""))
            link.setProperty("linkButton", True)
            link.setCursor(Qt.PointingHandCursor)
            link.setToolTip(f"Go to {ancestor.place_name}")
            link.clicked.connect(lambda _checked=False, pid=ancestor.place_id: self.ancestor_clicked.emit(pid))
            self._add_crumb(link)
        self.breadcrumb.refresh_height()

    def _add_crumb(self, widget):
        # FlowLayout skips widgets that are not visible yet, and a new child
        # only becomes visible on a later event-loop pass -- show it now so
        # the layout places it.
        self.breadcrumb_layout.addWidget(widget)
        if self.breadcrumb.isVisible():
            widget.show()

    def _toggle_description(self):
        self._description_expanded = not self._description_expanded
        self._apply_description_height()

    def _apply_description_height(self):
        self.description_label.ensurePolished()  # the theme sets this label's font size
        # Measure a line the way the word-wrapped label lays it out (a text
        # document), which is taller than fontMetrics().lineSpacing().
        probe = QTextDocument()
        probe.setDocumentMargin(0)
        probe.setDefaultFont(self.description_label.font())
        probe.setPlainText("Xg")
        preview = int(probe.size().height() * _DESCRIPTION_PREVIEW_LINES)
        width = max(self.description_scroll.viewport().width(), 200)
        full = self.description_label.heightForWidth(width)
        overflows = full > preview
        self.description_toggle.setVisible(overflows)
        self.description_toggle.setText("Show less" if self._description_expanded else "Show more")
        if self._description_expanded:
            # Cap so a very long description cannot push the music list off-screen.
            self.description_scroll.setFixedHeight(min(full + 4, max(int(self.height() * 0.45), preview)))
            self.description_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        else:
            self.description_scroll.setFixedHeight(min(full + 4, preview) if full > 0 else preview)
            self.description_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.place is not None and self.description_card.isVisible():
            self._apply_description_height()
