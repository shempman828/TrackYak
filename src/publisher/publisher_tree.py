from collections import defaultdict

from PySide6.QtCore import QMimeData, Qt, QTimer, Signal
from PySide6.QtGui import QDrag
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QTreeWidget, QTreeWidgetItem
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from src.db.db_tables import AlbumPublisher
from src.foundation.asset_paths import icon
from src.foundation.logger_config import logger
from src.foundation.status_utility import show_status_message
from src.publisher.publisher_hierarchy import get_descendant_publisher_ids

# Filter keys shared with PublisherView's combo boxes (stored as their item data).
FILTER_ANY = "any"
MBID_LINKED = "linked"
MBID_NOT_LINKED = "not_linked"
TIER_NOT_STARTED = "not_started"
TIER_FIRST_PASS = "first_pass"
TIER_SECOND_PASS = "second_pass"

_MBID_ROLE = Qt.UserRole + 1
_FIRST_PASS_ROLE = Qt.UserRole + 2
_SECOND_PASS_ROLE = Qt.UserRole + 3
_LINK_MARKER = " \U0001f517"


class _PublisherItem(QTreeWidgetItem):
    """Tree item that shows a link marker for MBID-linked publishers but edits the plain name."""

    def data(self, column, role):
        value = super().data(column, role)
        # Only DisplayRole carries the marker, so the inline editor (EditRole) and saves see the real name.
        if column == 0 and role == Qt.DisplayRole and super().data(0, _MBID_ROLE):
            return f"{value or ''}{_LINK_MARKER}"
        return value


def publisher_name(item):
    """Return the plain publisher name stored on a tree item."""
    return item.data(0, Qt.EditRole) or ""


class PublisherTreeWidget(QTreeWidget):
    """Publisher hierarchy tree with drag-and-drop reparenting, inline rename, and filtering."""

    # Emitted when the Delete key is pressed on the tree.
    delete_requested = Signal()
    # Emitted after the tree itself changed publisher data (rename, reparent).
    publishers_changed = Signal()

    _MBID_ROLE = _MBID_ROLE
    _FIRST_PASS_ROLE = _FIRST_PASS_ROLE
    _SECOND_PASS_ROLE = _SECOND_PASS_ROLE

    def __init__(self, controller):
        super().__init__()
        self.controller = controller
        self.flat_view = False
        self.setHeaderHidden(False)
        self.setColumnCount(2)
        self.setHeaderLabels(["Publisher", "Albums"])
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QTreeWidget.InternalMove)
        self.setSortingEnabled(True)
        self.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)

        # Allow selecting multiple items at once (Ctrl+click, Shift+click)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)

        self.itemChanged.connect(self.on_item_changed)

    def load_publishers(self):
        """Rebuild the tree from the DB, keeping sort order and scroll position."""
        sort_column = self.header().sortIndicatorSection()
        sort_order = self.header().sortIndicatorOrder()
        scrollbar = self.verticalScrollBar()
        scroll_value = scrollbar.value() if scrollbar else 0

        try:
            publishers = self.controller.get.get_all_entities("Publisher")
        except SQLAlchemyError as e:
            logger.error(f"Failed loading publishers: {e!s}")
            show_status_message(self, "Could not load publishers.")
            return

        # Building items must not fire itemChanged (that path saves renames).
        self.blockSignals(True)
        try:
            self.clear()
            recursive_counts = self.calculate_recursive_album_counts(publishers)

            if self.flat_view:
                root_items = [self._make_publisher_item(p, recursive_counts.get(p.publisher_id, 0)) for p in sorted(publishers, key=lambda p: (p.publisher_name or "").lower())]
            else:
                root_items = self._build_hierarchy(publishers, recursive_counts)

            self.addTopLevelItems(root_items)
        finally:
            self.blockSignals(False)

        self.sortByColumn(sort_column, sort_order)
        self.expandAll()

        if scrollbar:
            scrollbar.setValue(scroll_value)

    def _build_hierarchy(self, publishers, recursive_counts):
        """Create one item per publisher, nest children under parents, and return the root items."""
        items = {p.publisher_id: self._make_publisher_item(p, recursive_counts.get(p.publisher_id, 0)) for p in publishers}

        root_items = []
        for publisher in publishers:
            item = items[publisher.publisher_id]
            parent_item = items.get(publisher.parent_id) if publisher.parent_id is not None else None
            # A dangling parent_id or a parent_id cycle in the data makes the item a root, so it stays visible.
            if parent_item is None or self._is_ancestor_or_self(item, parent_item):
                root_items.append(item)
            else:
                parent_item.addChild(item)
        return root_items

    @staticmethod
    def _is_ancestor_or_self(candidate, item):
        """Return True if candidate is item or one of item's ancestors."""
        current = item
        while current is not None:
            if current is candidate:
                return True
            current = current.parent()
        return False

    def toggle_flat_view(self):
        """Toggle between the nested hierarchy and a flat alphabetical list."""
        self.flat_view = not self.flat_view
        # Drag-and-drop reparenting doesn't make sense against a flat,
        # always-sorted list.
        self.setDragEnabled(not self.flat_view)
        self.load_publishers()

    def _make_publisher_item(self, publisher, album_count):
        """Build a single publisher's tree item, shared by the tree and flat builders."""
        item = _PublisherItem()
        item.setData(0, _MBID_ROLE, bool(publisher.MBID))
        item.setText(0, publisher.publisher_name or "")
        if publisher.MBID:
            item.setToolTip(0, "Linked to MusicBrainz")
        if publisher.second_pass:
            item.setIcon(0, icon("checkmark_green.svg"))
        elif publisher.first_pass:
            item.setIcon(0, icon("checkmark.svg"))
        item.setFlags(item.flags() | Qt.ItemIsEditable)
        item.setData(1, Qt.DisplayRole, album_count)
        item.setData(0, Qt.UserRole, publisher.publisher_id)
        item.setData(0, _FIRST_PASS_ROLE, bool(publisher.first_pass))
        item.setData(0, _SECOND_PASS_ROLE, bool(publisher.second_pass))
        return item

    def keyPressEvent(self, event):
        """Emit delete_requested on the Delete key; otherwise use the default handling."""
        if event.key() == Qt.Key_Delete and self.state() != QAbstractItemView.EditingState:
            self.delete_requested.emit()
            return
        super().keyPressEvent(event)

    def on_item_changed(self, item, column):
        """Save an inline rename after the user finishes editing."""
        if column != 0:
            return

        publisher_id = item.data(0, Qt.UserRole)
        new_name = publisher_name(item).strip()
        if not new_name:
            show_status_message(self, "Publisher name cannot be blank.")
            # Deferred: rebuilding the tree inside the edit commit would delete the item under Qt's feet.
            QTimer.singleShot(0, self.publishers_changed.emit)
            return

        try:
            self.controller.update.update_entity("Publisher", publisher_id, publisher_name=new_name)
            logger.info(f"Publisher renamed to: {new_name}")
        except SQLAlchemyError as e:
            logger.error(f"Failed to rename publisher: {e!s}")
            show_status_message(self, "Could not rename publisher.")
        QTimer.singleShot(0, self.publishers_changed.emit)

    def calculate_recursive_album_counts(self, publishers):
        """Return {publisher_id: unique album count including all descendant publishers}."""
        try:
            # One query for all direct links; sets (not sums) so an album tagged at
            # two levels of one branch counts once toward the ancestor's total.
            direct_album_ids = defaultdict(set)
            for publisher_id, album_id in self.controller.get.session.execute(select(AlbumPublisher.publisher_id, AlbumPublisher.album_id)).all():
                direct_album_ids[publisher_id].add(album_id)

            children_map = defaultdict(list)
            for publisher in publishers:
                children_map[publisher.parent_id].append(publisher.publisher_id)

            recursive_album_ids: dict = {}
            in_progress = set()  # guards against a parent_id cycle in the data

            def album_ids_for(publisher_id):
                if publisher_id in recursive_album_ids:
                    return recursive_album_ids[publisher_id]
                if publisher_id in in_progress:
                    return set()
                in_progress.add(publisher_id)
                ids = set(direct_album_ids.get(publisher_id, ()))
                for child_id in children_map.get(publisher_id, []):
                    ids |= album_ids_for(child_id)
                in_progress.discard(publisher_id)
                recursive_album_ids[publisher_id] = ids
                return ids

            for publisher in publishers:
                album_ids_for(publisher.publisher_id)

            return {publisher_id: len(album_ids) for publisher_id, album_ids in recursive_album_ids.items()}

        except SQLAlchemyError as e:
            logger.error(f"Error calculating album counts: {e!s}")
            return {}

    def filter_items(self, search_text, mbid_filter=FILTER_ANY, tier_filter=FILTER_ANY):
        """Show items matching all criteria (plus their ancestors) and return (visible, total)."""
        text_lower = search_text.lower()
        has_criteria = bool(search_text) or mbid_filter != FILTER_ANY or tier_filter != FILTER_ANY
        counts = [0, 0]  # visible, total

        def item_matches(item):
            if text_lower and text_lower not in publisher_name(item).lower():
                return False
            linked = item.data(0, _MBID_ROLE)
            if mbid_filter == MBID_LINKED and not linked:
                return False
            if mbid_filter == MBID_NOT_LINKED and linked:
                return False
            first_pass = item.data(0, _FIRST_PASS_ROLE)
            second_pass = item.data(0, _SECOND_PASS_ROLE)
            if tier_filter == TIER_NOT_STARTED and first_pass:
                return False
            if tier_filter == TIER_FIRST_PASS and not (first_pass and not second_pass):
                return False
            return not (tier_filter == TIER_SECOND_PASS and not second_pass)

        def filter_item(item):
            matches = item_matches(item)

            child_matches = False
            for i in range(item.childCount()):
                if filter_item(item.child(i)):
                    child_matches = True

            should_show = matches or child_matches
            item.setHidden(not should_show)
            counts[1] += 1
            if should_show:
                counts[0] += 1
                # Children are visited first, so expanding each shown item reveals the whole path.
                if has_criteria:
                    item.setExpanded(True)

            return should_show

        for i in range(self.topLevelItemCount()):
            filter_item(self.topLevelItem(i))
        return counts[0], counts[1]

    def startDrag(self, supportedActions):
        """Start drag operation for parent-child relationships."""
        items = self.selectedItems()
        if not items:
            return

        mime_data = QMimeData()
        mime_data.setText(f"publisher:{items[0].data(0, Qt.UserRole)}")

        drag = QDrag(self)
        drag.setMimeData(mime_data)
        drag.exec_(Qt.MoveAction)

    def dropEvent(self, event):
        """Reparent the dragged publisher under the drop target, or make it a root on empty space."""
        source_item = self.currentItem()
        if not source_item:
            return

        target_item = self.itemAt(event.position().toPoint())
        if not target_item:
            self.remove_parent(source_item)
            return

        source_id = source_item.data(0, Qt.UserRole)
        target_id = target_item.data(0, Qt.UserRole)

        if source_id == target_id:
            return

        try:
            if target_id in get_descendant_publisher_ids(self.controller, source_id):
                show_status_message(self, "Cannot move a publisher under one of its own descendants.")
                return
            self.controller.update.update_entity("Publisher", source_id, parent_id=target_id)
            logger.info("Parent relationship updated successfully.")
        except SQLAlchemyError as e:
            logger.error(f"Error updating parent: {e!s}")
            show_status_message(self, "Could not change the parent publisher.")
        self.publishers_changed.emit()

    def remove_parent(self, item):
        """Make the given publisher a top-level publisher."""
        publisher_id = item.data(0, Qt.UserRole)
        try:
            self.controller.update.update_entity("Publisher", publisher_id, parent_id=None)
        except SQLAlchemyError as e:
            logger.error(f"Error removing parent: {e!s}")
            show_status_message(self, "Could not remove the parent publisher.")
        self.publishers_changed.emit()
