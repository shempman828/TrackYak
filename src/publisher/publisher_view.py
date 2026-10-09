from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QComboBox, QDialog, QHBoxLayout, QLabel, QLineEdit, QMenu, QMessageBox, QProgressDialog, QPushButton, QSplitter, QVBoxLayout, QWidget
from sqlalchemy.exc import SQLAlchemyError

from src.common.dialogs.base_split_dialog import SplitDBDialog
from src.foundation.logger_config import logger
from src.foundation.status_utility import show_status_message
from src.publisher.publisher_detail import PublisherDetailTab
from src.publisher.publisher_edit_dialog import PublisherEditDialog
from src.publisher.publisher_fuzzy_match import FUZZY_THRESHOLD, PublisherFuzzyMatchDialog, PublisherFuzzyScanWorker
from src.publisher.publisher_merge_dialog import PublisherMergeDialog
from src.publisher.publisher_tree import FILTER_ANY, MBID_LINKED, MBID_NOT_LINKED, TIER_FIRST_PASS, TIER_NOT_STARTED, TIER_SECOND_PASS, PublisherTreeWidget, publisher_name

# Delay before a search keystroke re-filters the tree.
_SEARCH_DEBOUNCE_MS = 150


def plan_child_reparenting(publishers, deleting_ids):
    """Return {child_id: new_parent_id} that moves orphaned children up to their nearest surviving ancestor."""
    parent_of = {p.publisher_id: p.parent_id for p in publishers}
    moves = {}
    for publisher_id, parent_id in parent_of.items():
        if publisher_id in deleting_ids or parent_id not in deleting_ids:
            continue
        new_parent = parent_id
        seen = set()  # guards against a parent_id cycle in the data
        while new_parent in deleting_ids and new_parent not in seen:
            seen.add(new_parent)
            new_parent = parent_of.get(new_parent)
        moves[publisher_id] = None if new_parent in deleting_ids else new_parent
    return moves


class PublisherView(QWidget):
    """Publisher browser: filterable hierarchy tree on the left, detail panel on the right."""

    def __init__(self, controller):
        super().__init__()
        self.controller = controller
        self.publishers_tree = None
        self.detail_tab = None
        self._fuzzy_worker = None
        self.init_ui()
        self.load_publishers()

    def init_ui(self):
        """Build the filter bar, publisher tree, and detail panel."""
        self.setWindowTitle("Publishers")
        main_layout = QVBoxLayout(self)

        splitter = QSplitter(Qt.Horizontal)

        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)

        # Filter bar: search box + MusicBrainz/fixed status combos
        filter_bar = QHBoxLayout()

        self.search_bar = QLineEdit()
        self.search_bar.setPlaceholderText("Search publishers...")
        self.search_bar.setClearButtonEnabled(True)
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(_SEARCH_DEBOUNCE_MS)
        self._search_timer.timeout.connect(self.filter_publishers)
        self.search_bar.textChanged.connect(self._search_timer.start)
        filter_bar.addWidget(self.search_bar, stretch=1)

        self.mbid_combo = QComboBox()
        for label, key in (("Any", FILTER_ANY), ("Linked", MBID_LINKED), ("Not Linked", MBID_NOT_LINKED)):
            self.mbid_combo.addItem(label, key)
        self.mbid_combo.setToolTip("Filter by MusicBrainz link status")
        self.mbid_combo.currentIndexChanged.connect(self.filter_publishers)
        filter_bar.addWidget(self.mbid_combo)

        self.fixed_combo = QComboBox()
        for label, key in (("Any", FILTER_ANY), ("Not Started", TIER_NOT_STARTED), ("First Pass", TIER_FIRST_PASS), ("Second Pass", TIER_SECOND_PASS)):
            self.fixed_combo.addItem(label, key)
        self.fixed_combo.setToolTip("Filter by metadata review tier")
        self.fixed_combo.currentIndexChanged.connect(self.filter_publishers)
        filter_bar.addWidget(self.fixed_combo)

        self.flat_view_button = QPushButton("Flat View")
        self.flat_view_button.setCheckable(True)
        self.flat_view_button.setChecked(False)
        self.flat_view_button.setToolTip("Toggle between the hierarchical tree and a flat alphabetical list")
        self.flat_view_button.clicked.connect(self.toggle_flat_view)
        filter_bar.addWidget(self.flat_view_button)

        left_layout.addLayout(filter_bar)

        # Count label — shows "N publishers" or "Showing X of Y" while filtering
        self.count_label = QLabel()
        self.count_label.setProperty("textRole", "muted")
        left_layout.addWidget(self.count_label)

        self.publishers_tree = PublisherTreeWidget(self.controller)
        # currentItemChanged (not itemClicked) so keyboard navigation also updates the detail panel.
        self.publishers_tree.currentItemChanged.connect(self.on_publisher_selected)
        self.publishers_tree.delete_requested.connect(self._delete_selected_publisher)
        self.publishers_tree.publishers_changed.connect(self._on_tree_changed)
        self.publishers_tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.publishers_tree.customContextMenuRequested.connect(self.show_context_menu)
        left_layout.addWidget(self.publishers_tree)

        self.detail_tab = PublisherDetailTab(self.controller)
        self.detail_tab.albums_changed.connect(self.load_publishers)

        splitter.addWidget(left_panel)
        splitter.addWidget(self.detail_tab)
        splitter.setSizes([300, 700])

        main_layout.addWidget(splitter)

    def _trigger_rename(self, item):
        """Focus the tree and start inline editing."""
        self.publishers_tree.setFocus()
        self.publishers_tree.editItem(item, 0)

    def show_context_menu(self, position):
        """Show context menu for publisher tree items."""
        item = self.publishers_tree.itemAt(position)
        selected_items = self.publishers_tree.selectedItems()
        menu = QMenu(self)

        if item:
            # Only show single-item actions when exactly one item is selected
            if len(selected_items) == 1:
                rename_action = QAction("Rename Publisher", self)
                rename_action.triggered.connect(lambda: self._trigger_rename(item))
                menu.addAction(rename_action)

                edit_action = QAction("Edit Publisher", self)
                edit_action.triggered.connect(lambda: self._edit_publisher(item))
                menu.addAction(edit_action)

                merge_action = QAction("Merge Publisher...", self)
                merge_action.triggered.connect(self.initiate_merge)
                menu.addAction(merge_action)

                split_action = QAction("Split Publisher...", self)
                split_action.triggered.connect(self._split_publisher)
                menu.addAction(split_action)

                menu.addSeparator()

                new_parent_action = QAction("New Parent Publisher", self)
                new_parent_action.triggered.connect(lambda: self.create_new_parent_publisher(item))
                menu.addAction(new_parent_action)

                new_child_action = QAction("New Child Publisher", self)
                new_child_action.triggered.connect(lambda: self.create_new_child_publisher(item))
                menu.addAction(new_child_action)

            menu.addSeparator()

            # Delete works for single or multiple selection
            count = len(selected_items)
            delete_label = f"Delete {count} Publishers" if count > 1 else "Delete Publisher"
            delete_action = QAction(delete_label, self)
            delete_action.triggered.connect(self._delete_selected_publisher)
            menu.addAction(delete_action)

            menu.addSeparator()

        else:
            # No item selected - global actions
            new_action = QAction("Add New Publisher...", self)
            new_action.triggered.connect(self._create_new_publisher)
            menu.addAction(new_action)

            merge_action = QAction("Merge Publishers...", self)
            merge_action.triggered.connect(self.initiate_merge)
            menu.addAction(merge_action)

        menu.addSeparator()
        fuzzy_action = QAction("Find Duplicate Publishers…", self)
        fuzzy_action.triggered.connect(self.find_fuzzy_matches)
        menu.addAction(fuzzy_action)

        menu.exec_(self.publishers_tree.mapToGlobal(position))

    def _fetch_publisher(self, item):
        """Return the Publisher for a tree item, or None after telling the user why."""
        try:
            publisher = self.controller.get.get_entity_object("Publisher", publisher_id=item.data(0, Qt.UserRole))
        except SQLAlchemyError as e:
            logger.error(f"Error loading publisher: {e!s}")
            show_status_message(self, "Could not load the selected publisher.")
            return None
        if not publisher:
            show_status_message(self, "The selected publisher no longer exists.")
        return publisher

    def _create_new_publisher(self):
        """Open the publisher dialog in create mode and show the new publisher."""
        dialog = PublisherEditDialog(self.controller, parent=self)
        if dialog.exec_() == QDialog.Accepted and dialog.result_publisher:
            self.load_publishers()
            self.detail_tab.load_publisher_data(dialog.result_publisher.publisher_id)

    def _delete_selected_publisher(self):
        """Delete all selected publishers after one confirmation."""
        selected_items = self.publishers_tree.selectedItems()
        if not selected_items:
            show_status_message(self, "Please select a publisher to delete.")
            return

        deleting = {item.data(0, Qt.UserRole): publisher_name(item) for item in selected_items}
        try:
            moves = plan_child_reparenting(self.controller.get.get_all_entities("Publisher") or [], set(deleting))
        except SQLAlchemyError as e:
            logger.error(f"Error loading publishers for delete: {e!s}")
            show_status_message(self, "Could not load publishers.")
            return

        count = len(deleting)
        if count == 1:
            message = f"Are you sure you want to delete '{next(iter(deleting.values()))}'?"
        else:
            names_preview = ", ".join(list(deleting.values())[:5])
            if count > 5:
                names_preview += f", … (+{count - 5} more)"
            message = f"Are you sure you want to delete {count} publishers?\n\n{names_preview}"
        if moves:
            message += f"\n\n{len(moves)} child publisher{'s' if len(moves) != 1 else ''} will move up one level."

        if QMessageBox.question(self, "Confirm Delete", message, QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
            return

        errors = []
        for child_id, new_parent_id in moves.items():
            try:
                self.controller.update.update_entity("Publisher", child_id, parent_id=new_parent_id)
            except SQLAlchemyError as e:
                logger.error(f"Failed to move child publisher {child_id}: {e!s}")
        deleted_ids = set()
        for publisher_id, name in deleting.items():
            try:
                self.controller.delete.delete_entity("Publisher", publisher_id)
                deleted_ids.add(publisher_id)
            except SQLAlchemyError as e:
                errors.append(name)
                logger.error(f"Failed to delete publisher '{name}': {e!s}")

        current = self.detail_tab.current_publisher
        if current is not None and current.publisher_id in deleted_ids:
            self.detail_tab.show_empty_state()
        self.load_publishers()

        if errors:
            QMessageBox.warning(self, "Partial Delete", "Could not delete the following publishers:\n" + "\n".join(errors))

    def _split_publisher(self):
        """Split the selected publisher."""
        item = self.publishers_tree.currentItem()
        if not item:
            show_status_message(self, "Please select a publisher to split.")
            return

        publisher_obj = self._fetch_publisher(item)
        if not publisher_obj:
            return

        try:
            split_dialog = SplitDBDialog(self.controller.split, "Publisher", publisher_obj, self, get_helper=self.controller.get)
            if split_dialog.exec_() == QDialog.Accepted:
                self.load_publishers()
        except SQLAlchemyError as e:
            logger.error(f"Error in _split_publisher(): {e}", exc_info=True)
            show_status_message(self, "Could not split the publisher.")

    def _edit_publisher(self, item):
        """Open the edit dialog for the selected publisher."""
        publisher = self._fetch_publisher(item)
        if not publisher:
            return

        dialog = PublisherEditDialog(self.controller, publisher=publisher, parent=self)
        if dialog.exec_() == QDialog.Accepted:
            self.load_publishers()
            self.detail_tab.load_publisher_data(publisher.publisher_id)

    def create_new_parent_publisher(self, item):
        """Create a new publisher in the given publisher's parent slot and nest the publisher under it."""
        publisher = self._fetch_publisher(item)
        if not publisher:
            return

        dialog = PublisherEditDialog(self.controller, parent=self)
        if dialog.exec_() != QDialog.Accepted or not dialog.result_publisher:
            return

        new_publisher = dialog.result_publisher
        try:
            # The new publisher takes the old parent slot, so the grandparent chain is kept.
            self.controller.update.update_entity("Publisher", new_publisher.publisher_id, parent_id=publisher.parent_id)
            self.controller.update.update_entity("Publisher", publisher.publisher_id, parent_id=new_publisher.publisher_id)
            logger.info("New parent publisher created and linked successfully.")
        except SQLAlchemyError as e:
            logger.error(f"Error creating new parent publisher: {e!s}")
            QMessageBox.critical(self, "Error", "Failed to create new parent publisher")
        self.load_publishers()

    def create_new_child_publisher(self, item):
        """Create a new publisher and set it as a child of the given publisher."""
        publisher = self._fetch_publisher(item)
        if not publisher:
            return

        dialog = PublisherEditDialog(self.controller, parent=self)
        if dialog.exec_() != QDialog.Accepted or not dialog.result_publisher:
            return

        new_publisher = dialog.result_publisher
        try:
            self.controller.update.update_entity("Publisher", new_publisher.publisher_id, parent_id=publisher.publisher_id)
            logger.info("New child publisher created and linked successfully.")
        except SQLAlchemyError as e:
            logger.error(f"Error creating new child publisher: {e!s}")
            QMessageBox.critical(self, "Error", "Failed to create new child publisher")
        self.load_publishers()

    def initiate_merge(self):
        """Open the merge dialog, pre-selecting the currently highlighted publisher."""
        publisher_obj = None
        item = self.publishers_tree.currentItem()
        if item:
            publisher_obj = self._fetch_publisher(item)

        merge_dialog = PublisherMergeDialog(self.controller, self, publisher_obj=publisher_obj)
        if merge_dialog.exec_() == QDialog.Accepted:
            self.load_publishers()
            logger.info("Publishers merged successfully.")

    def find_fuzzy_matches(self):
        """Scan all publisher names for likely duplicates on a worker thread, then open the review dialog."""
        if self._fuzzy_worker is not None and self._fuzzy_worker.isRunning():
            show_status_message(self, "A duplicate scan is already running.")
            return

        try:
            publishers = self.controller.get.get_all_entities("Publisher")
        except SQLAlchemyError as e:
            QMessageBox.critical(self, "Error", f"Failed to load publishers: {e}")
            return

        if not publishers:
            show_status_message(self, "No publishers found in database.")
            return

        by_id = {p.publisher_id: p for p in publishers}

        progress = QProgressDialog("Scanning for duplicate publishers…", "Cancel", 0, 1, self)
        progress.setWindowTitle("Duplicate Scan")
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)
        progress.setValue(0)
        progress.show()

        worker = PublisherFuzzyScanWorker([(p.publisher_id, p.publisher_name) for p in publishers], FUZZY_THRESHOLD)

        def _on_progress(current, total):
            progress.setRange(0, max(total, 1))
            progress.setValue(current)
            progress.setLabelText(f"Scanning for duplicate publishers… ({current:,} / {total:,})")

        def _on_finished(id_matches):
            cancelled = worker.is_cancelled
            progress.close()
            if cancelled:
                return
            matches = [(by_id[a], by_id[b], score) for a, b, score in id_matches if a in by_id and b in by_id]
            no_match_text = f"No similar publisher names found (threshold: {int(FUZZY_THRESHOLD * 100)}% similarity)."
            if not matches:
                show_status_message(self, no_match_text)
                return
            dialog = PublisherFuzzyMatchDialog(matches, self.controller, self)
            if not dialog.matches:  # every match was dismissed before
                dialog.deleteLater()
                show_status_message(self, no_match_text)
                return
            if dialog.exec_() == QDialog.Accepted:
                self.load_publishers()

        def _on_error(msg):
            progress.close()
            QMessageBox.critical(self, "Scan Error", f"Duplicate scan failed:\n{msg}")

        worker.progress.connect(_on_progress)
        worker.finished.connect(_on_finished)
        worker.error.connect(_on_error)
        progress.canceled.connect(worker.request_cancel)

        # Keep a reference so the worker isn't garbage collected
        self._fuzzy_worker = worker
        worker.start()

    def load_publishers(self):
        """Reload the tree and reapply the active filters."""
        self.publishers_tree.load_publishers()
        self.filter_publishers()

    def _on_tree_changed(self):
        """Reload after a rename or reparent in the tree, and refresh the shown publisher."""
        self.load_publishers()
        current = self.detail_tab.current_publisher
        if current is not None:
            self.detail_tab.load_publisher_data(current.publisher_id)

    def toggle_flat_view(self):
        """Toggle between the nested hierarchy and a flat alphabetical list."""
        self.publishers_tree.toggle_flat_view()
        self.flat_view_button.setText("Tree View" if self.publishers_tree.flat_view else "Flat View")
        self.filter_publishers()

    def _update_count_label(self, visible, total):
        """Refresh the "N publishers" / "X of Y publishers" count label."""
        if visible == total:
            self.count_label.setText(f"{total} publisher{'s' if total != 1 else ''}")
        else:
            self.count_label.setText(f"{visible} of {total} publishers")

    def on_publisher_selected(self, item, _previous=None):
        """Show the details of the newly current publisher."""
        if item:
            self.detail_tab.load_publisher_data(item.data(0, Qt.UserRole))

    def filter_publishers(self, *_args):
        """Filter publishers based on search text, MBID link status, and review tier."""
        self._search_timer.stop()
        visible, total = self.publishers_tree.filter_items(self.search_bar.text(), self.mbid_combo.currentData(), self.fixed_combo.currentData())
        self._update_count_label(visible, total)
