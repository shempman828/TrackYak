from collections import defaultdict

from PySide6.QtCore import QMimeData, Qt, Signal
from PySide6.QtGui import QDrag
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from src.common.base_split_dialog import SplitDBDialog
from src.common.cancellable_worker import CancellableWorker
from src.common.hierarchy_tree_style import (
    collect_expanded_ids,
    configure_hierarchy_tree,
    filter_tree_widget,
    handle_insert_as_new_relative,
    icon_for_depth,
    is_hierarchy_descendant,
    render_hierarchy_as_text,
    restore_expanded_ids_or_expand_all,
)
from src.core.logger_config import logger
from src.core.status_utility import show_status_message
from src.db.db_tables import TrackGenre
from src.genre.genre_edit import GenreEditDialog, GenreSetParentDialog
from src.genre.genre_merge import GenreMergeDialog
from src.genre.genre_tracks import GenreTracksWindow
from src.track.base_track_view import BaseTrackView


class GenreLoaderWorker(CancellableWorker):
    """Runs on a background thread. Fetches all genres plus their direct and
    recursive (own + all descendants) track counts in two queries total,
    then emits the results back to the main thread.

    Recursive counts are computed with one ungrouped TrackGenre query plus a
    memoized bottom-up Python union of per-genre track-id sets over the
    parent/child structure -- the same approach as
    PublisherTreeWidget.calculate_recursive_album_counts -- instead of
    Genre.all_track_count's per-object Python recursion, which has no
    batching and an O(n^2)/N+1-query risk on a tree of any size. Sets (not
    a plain integer sum) are required because a track tagged with both a
    genre and one of its descendants must only count once toward that
    genre's recursive total.
    """

    # Payload: (genres, direct_counts_by_genre_id, recursive_counts_by_genre_id)
    # Uses `object` rather than `list`/`dict`, matching RoleLoaderWorker --
    # PySide6's queued cross-thread delivery can otherwise fail to
    # copy-convert plain dict/list signal args.
    finished = Signal(object, object, object)
    error = Signal(str)

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller

    def run(self):
        try:
            genres = self.controller.get.get_all_entities("Genre") or []

            direct_track_ids = defaultdict(set)
            for genre_id, track_id in self.controller.get.session.execute(
                select(TrackGenre.genre_id, TrackGenre.track_id)
            ).all():
                direct_track_ids[genre_id].add(track_id)

            direct_counts = {
                genre_id: len(track_ids)
                for genre_id, track_ids in direct_track_ids.items()
            }

            children_map = defaultdict(list)
            for genre in genres:
                children_map[genre.parent_id].append(genre.genre_id)

            recursive_track_ids: dict = {}

            def track_ids_for(genre_id):
                if genre_id not in recursive_track_ids:
                    ids = set(direct_track_ids.get(genre_id, ()))
                    for child_id in children_map.get(genre_id, []):
                        ids |= track_ids_for(child_id)
                    recursive_track_ids[genre_id] = ids
                return recursive_track_ids[genre_id]

            for genre in genres:
                track_ids_for(genre.genre_id)

            recursive_totals = {
                genre_id: len(track_ids)
                for genre_id, track_ids in recursive_track_ids.items()
            }

        except Exception as e:  # ruff: ignore[blind-except]
            logger.exception("Genre count scan failed")
            self.error.emit(str(e))
            self._release_db_session()
            return

        self._release_db_session()
        self.finished.emit(genres, direct_counts, recursive_totals)


class _ReverseStr:
    """Wraps a string so `sorted()` orders it in reverse -- used to sort the
    Genre column descending while still tie-breaking a numeric column
    ascending (plain `reverse=True` would flip both)."""

    __slots__ = ("value",)

    def __init__(self, value: str):
        self.value = value

    def __lt__(self, other):
        return other.value < self.value

    def __eq__(self, other):
        return self.value == other.value


class _GenreTreeItem(QTreeWidgetItem):
    """QTreeWidgetItem that sorts the Tracks column numerically by recursive
    track count instead of lexicographically comparing its "own · recursive"
    display string (which would put "9" after "12 · 42"). The Genre column
    falls back to the default (case-insensitive) text comparison."""

    _SORT_COUNT_ROLE = Qt.UserRole + 1

    def __lt__(self, other):
        tree = self.treeWidget()
        column = tree.sortColumn() if tree else 0
        if column == 1:
            self_count = self.data(1, self._SORT_COUNT_ROLE) or 0
            other_count = other.data(1, self._SORT_COUNT_ROLE) or 0
            if self_count != other_count:
                return self_count < other_count
        # Ties on the Tracks column, and any sort on the Genre column
        # itself, fall back to a deterministic case-insensitive name
        # comparison.
        return self.text(0).lower() < other.text(0).lower()


class GenreView(QWidget):
    """Widget displaying genre hierarchy with CRUD operations and parent-child relationships."""

    genre_updated = Signal()

    def __init__(self, controller):
        super().__init__()
        self.current_genre_id: int | None = None
        self.controller = controller
        self.show_recursive_tracks = False
        self.flat_view = False

        # Populated after background loading finishes; kept on the instance
        # so re-sorting or toggling Flat View never re-queries the database.
        self._all_genres: list = []
        self._direct_counts: dict = {}
        self._recursive_counts: dict = {}
        self._loader_thread: GenreLoaderWorker | None = None

        self.init_UI()
        self.load_genres()

    def init_UI(self):
        """Initialize UI components with modern styling and layout."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)

        # Tree widget configuration (created early so top-row buttons can reference it)
        self.tree = QTreeWidget()
        configure_hierarchy_tree(self.tree)
        # Visible two-column header ("Genre"/"Tracks") with native
        # column-header-click sorting, matching PublisherTreeWidget --
        # overrides configure_hierarchy_tree's default hidden single-column
        # header.
        self.tree.setHeaderHidden(False)
        self.tree.setColumnCount(2)
        self.tree.setHeaderLabels(["Genre", "Tracks"])
        self.tree.setSortingEnabled(True)
        header = self.tree.header()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.tree.itemChanged.connect(self.on_item_edited)
        self.tree.dropEvent = self.on_drop_event

        # Context menu signals
        self.tree.customContextMenuRequested.connect(self.show_context_menu)

        # Install event filter for keyboard shortcuts
        self.tree.installEventFilter(self)

        # Top row: Search bar + Refresh button
        top_row = QHBoxLayout()

        # Search bar with clear button
        self.search_bar = QLineEdit()
        self.search_bar.setPlaceholderText("Search genres...")
        self.search_bar.setClearButtonEnabled(True)
        self.search_bar.textChanged.connect(self.filter_genres)
        top_row.addWidget(self.search_bar)

        # New Genre Button
        self.new_genre_button = QPushButton("New Genre")
        self.new_genre_button.clicked.connect(lambda: self.edit_genre(None))
        top_row.addWidget(self.new_genre_button)

        # Expand All / Collapse All buttons
        self.expand_all_button = QPushButton("Expand All")
        self.expand_all_button.clicked.connect(self.tree.expandAll)
        top_row.addWidget(self.expand_all_button)

        self.collapse_all_button = QPushButton("Collapse All")
        self.collapse_all_button.clicked.connect(self.tree.collapseAll)
        top_row.addWidget(self.collapse_all_button)

        self.flat_view_button = QPushButton("Flat View")
        self.flat_view_button.setCheckable(True)
        self.flat_view_button.setChecked(False)
        self.flat_view_button.setToolTip(
            "Toggle between the hierarchical tree and a flat alphabetical list"
        )
        self.flat_view_button.clicked.connect(self.toggle_flat_view)
        top_row.addWidget(self.flat_view_button)

        # Add horizontal layout to the main vertical layout
        layout.addLayout(top_row)

        # Loading indicator — shown while GenreLoaderWorker runs in the background
        self.loading_label = QLabel("Loading genres…")
        self.loading_label.setAlignment(Qt.AlignCenter)
        self.loading_label.hide()
        layout.addWidget(self.loading_label)

        layout.addWidget(self.tree)

        # Status bar with temporary messages
        self.status_bar = QLabel()
        self.status_bar.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.status_bar)

    def eventFilter(self, obj, event):
        """Handle keyboard shortcuts."""
        if (
            obj == self.tree
            and event.type() == event.Type.KeyPress
            and event.key() == Qt.Key_Delete
        ):
            self.delete_selected_genres()
            return True
        return super().eventFilter(obj, event)

    def _split_genre(self):
        """Open the split dialog for the selected genre."""
        try:
            # Get the currently selected item from the tree
            current_item = self.tree.currentItem()
            if not current_item:
                show_status_message(self, "Please select a genre to split.")
                return

            current_genre_id = current_item.data(0, Qt.UserRole)

            # Fetch the actual Genre ORM object from the database
            genre_obj = self.controller.get.get_entity_object(
                "Genre", genre_id=current_genre_id
            )
            if not genre_obj:
                show_status_message(self, "The selected genre no longer exists.")
                return

            # Create the split dialog with proper parameters
            split_dialog = SplitDBDialog(
                self.controller.split,  # split helper with session
                "Genre",  # entity_type
                genre_obj,  # entity object
                self,  # parent
                get_helper=self.controller.get,
            )

            # Run dialog and refresh if accepted
            if split_dialog.exec_() == QDialog.Accepted:
                self.load_genres()
                self.genre_updated.emit()
                self.status_bar.setText("Genre split completed successfully")

        except SQLAlchemyError as e:
            logger.error(f"Error in _split_genre(): {e}", exc_info=True)
            QMessageBox.critical(self, "Error", f"An unexpected error occurred:\n{e}")

    def load_genres(self):
        """Kick off background loading of genres and both track-count sets.

        Mirrors RoleView.load_roles(): runs a GenreLoaderWorker on a
        background QThread so opening/refreshing the genre tab never
        blocks the UI, then rebuilds the tree from the cached results in
        _on_genres_loaded() once it finishes.
        """
        try:
            if self._loader_thread and self._loader_thread.isRunning():
                return
        except RuntimeError:
            self._loader_thread = None

        self._set_loading_state(True)

        worker = GenreLoaderWorker(self.controller, parent=self)
        worker.finished.connect(self._on_genres_loaded)
        worker.error.connect(self._on_genres_load_error)
        self._loader_thread = worker
        worker.start()

    def _set_loading_state(self, is_loading: bool):
        """Show/hide the loading indicator and enable/disable the tree."""
        self.loading_label.setVisible(is_loading)
        self.tree.setEnabled(not is_loading)
        self.search_bar.setEnabled(not is_loading)

    def _on_genres_loaded(self, genres, direct_counts, recursive_counts):
        """Called on the main thread once GenreLoaderWorker finishes."""
        self._all_genres = genres
        self._direct_counts = direct_counts
        self._recursive_counts = recursive_counts
        self._set_loading_state(False)
        self._rebuild_tree()
        logger.info(f"Loaded {len(genres)} genres with track counts")

    def _on_genres_load_error(self, error_message: str):
        """Called on the main thread if GenreLoaderWorker hits an exception."""
        logger.error(f"GenreLoaderWorker error: {error_message}")
        self._set_loading_state(False)
        self.status_bar.setText("Failed to load genres")

    def _rebuild_tree(self):
        """Rebuild the tree widget from cached genre/count data (no DB
        calls). Used after loading and whenever Flat View is toggled, so
        neither operation re-queries the database."""
        genres = self._all_genres
        direct_counts = self._direct_counts
        recursive_counts = self._recursive_counts

        # Save which genre IDs are currently expanded, and the active sort
        # column/order, so the rebuild doesn't reset either.
        expanded_ids = collect_expanded_ids(self.tree)
        is_initial_load = self.tree.topLevelItemCount() == 0
        header = self.tree.header()
        if is_initial_load:
            # QHeaderView's own default sort order is descending (verified
            # empirically — not documented), which would silently invert
            # the tree's long-standing default alphabetical-ascending
            # order. Force it explicitly on first load only; afterward the
            # user's own sort choice (from a header click) is preserved.
            sort_column, sort_order = 0, Qt.AscendingOrder
        else:
            sort_column = header.sortIndicatorSection()
            sort_order = header.sortIndicatorOrder()

        self.tree.clear()

        # Build a mapping of genre_id to genre for quick lookup
        genre_map = {genre.genre_id: genre for genre in genres}

        # Build a parent-child mapping
        children_map = defaultdict(list)
        for genre in genres:
            children_map[genre.parent_id].append(genre)

        if self.flat_view:
            self._build_genre_flat(genres, genre_map, direct_counts, recursive_counts)
        else:
            # Build the tree recursively starting from root nodes (parent_id=None)
            self._build_genre_tree(
                None, children_map, genre_map, direct_counts, recursive_counts, 0
            )

        # Native Qt sort — no manual per-sibling sorting needed. Restoring
        # the same column/order here (rather than leaving it as whatever
        # the last click set) is what makes sort state survive a rebuild.
        self.tree.sortByColumn(sort_column, sort_order)

        restore_expanded_ids_or_expand_all(self.tree, expanded_ids, is_initial_load)

        # Reapply any active search filter, since the tree was just
        # rebuilt from scratch.
        self.filter_genres(self.search_bar.text())

    def toggle_flat_view(self):
        """Toggle between the nested hierarchy and a flat list, using
        already-loaded data — no database round-trip."""
        self.flat_view = self.flat_view_button.isChecked()
        self.flat_view_button.setText("Tree View" if self.flat_view else "Flat View")
        self.expand_all_button.setEnabled(not self.flat_view)
        self.collapse_all_button.setEnabled(not self.flat_view)
        # Drag-and-drop reparenting doesn't make sense against a flat,
        # always-sorted list.
        self.tree.setDragEnabled(not self.flat_view)
        self._rebuild_tree()

    @staticmethod
    def _format_track_count(own_count: int, recursive_count: int) -> str:
        """Build the compact track-count text for the Tracks column,
        mirroring PlaylistView._format_track_count."""
        if recursive_count != own_count:
            # Has subgenres contributing additional tracks, e.g. "12 · 42"
            return f"{own_count} · {recursive_count}"
        # Counts match — just the one number, e.g. "5"
        return str(own_count)

    @staticmethod
    def _style_count_cell(item: QTreeWidgetItem) -> None:
        """Right-align and de-emphasize the Tracks column so it reads as a
        secondary detail rather than competing with the genre name."""
        item.setTextAlignment(1, Qt.AlignRight | Qt.AlignVCenter)
        font = item.font(1)
        font.setItalic(True)
        item.setFont(1, font)
        if "·" in item.text(1):
            item.setToolTip(1, "Own tracks · total including subgenres")

    def _make_genre_item(self, genre, own_count, recursive_count, depth, genre_map):
        """Build a single genre's tree item, shared by the tree and flat builders.

        Looks up the parent genre through `genre_map` (by `parent_id`)
        rather than the ORM `.parent` relationship: `genre` was fetched on
        GenreLoaderWorker's background thread and is detached by the time
        it reaches here (its session was released right after the query),
        so a lazy-loaded relationship access would raise
        DetachedInstanceError -- a plain dict lookup by already-loaded
        column value doesn't need the session at all.
        """
        count_text = self._format_track_count(own_count, recursive_count)

        item = _GenreTreeItem([genre.genre_name, count_text])
        item.setData(0, Qt.UserRole, genre.genre_id)
        item.setData(1, _GenreTreeItem._SORT_COUNT_ROLE, recursive_count)
        item.setFlags(item.flags() | Qt.ItemIsEditable)

        item.setIcon(0, icon_for_depth(depth))
        self._style_count_cell(item)

        tooltip = f"ID: {genre.genre_id}\nTracks: {count_text}"
        if genre.description:
            tooltip += f"\nDescription: {genre.description}"
        parent = genre_map.get(genre.parent_id)
        if parent:
            tooltip += f"\nParent: {parent.genre_name}"
        item.setToolTip(0, tooltip)
        return item

    def _build_genre_tree(
        self,
        parent_item,
        children_map,
        genre_map,
        direct_counts,
        recursive_counts,
        depth,
    ):
        """Recursively build the tree structure with visual hierarchy
        indicators. Insertion order doesn't matter for display order —
        native Qt sorting (self.tree.setSortingEnabled(True)) reorders
        each sibling group by whichever column is currently sorted."""
        parent_id = parent_item.data(0, Qt.UserRole) if parent_item else None
        for genre in children_map.get(parent_id, []):
            own_count = direct_counts.get(genre.genre_id, 0)
            recursive_count = recursive_counts.get(genre.genre_id, own_count)
            item = self._make_genre_item(
                genre, own_count, recursive_count, depth, genre_map
            )

            if parent_item:
                parent_item.addChild(item)
            else:
                self.tree.addTopLevelItem(item)

            self._build_genre_tree(
                item,
                children_map,
                genre_map,
                direct_counts,
                recursive_counts,
                depth + 1,
            )

    def _build_genre_flat(self, genres, genre_map, direct_counts, recursive_counts):
        """Populate the tree as a single unnested list; native Qt sorting
        orders it (see _build_genre_tree's docstring)."""
        for genre in genres:
            own_count = direct_counts.get(genre.genre_id, 0)
            recursive_count = recursive_counts.get(genre.genre_id, own_count)
            item = self._make_genre_item(
                genre, own_count, recursive_count, 0, genre_map
            )
            self.tree.addTopLevelItem(item)

    def on_item_edited(self, item, column):
        """Handle genre name updates. The Tracks column (1) is display-only
        and never reaches here as an edit target, matching
        PublisherTreeWidget.on_item_changed's `if column != 0: return` guard."""
        if column != 0:
            return

        genre_id = item.data(0, Qt.UserRole)
        new_name = item.text(0).strip()
        old_display_text = item.text(0)  # in case we need to revert

        try:
            if not new_name:
                raise ValueError("Genre name cannot be empty")

            # Check if name already exists (excluding current genre)
            existing = self.controller.get.get_entity_object(
                "Genre", genre_name=new_name
            )
            if existing and existing.genre_id != genre_id:
                raise ValueError("Genre name already exists")

            # Update the genre name
            self.controller.update.update_entity("Genre", genre_id, genre_name=new_name)

            self.genre_updated.emit()
            self.status_bar.setText(f"Renamed to {new_name}")

        except ValueError as e:
            show_status_message(self, str(e))
            item.setText(0, old_display_text)  # Revert to old display text
        except (SQLAlchemyError, RuntimeError) as e:
            logger.error(f"Error renaming genre: {e!s}")
            QMessageBox.critical(self, "Error", "Failed to rename genre")
            item.setText(0, old_display_text)  # Revert to old display text

    def filter_genres(self, text):
        """Simple text-based filtering."""
        filter_tree_widget(self.tree, text)

    def on_drop_event(self, event):
        """Handle parent changes through drag-and-drop."""
        # Get all selected items
        selected_items = self.tree.selectedItems()

        if not selected_items:
            event.ignore()
            return

        # Determine the drop target
        target_item = self.tree.itemAt(event.pos())
        target_id = target_item.data(0, Qt.UserRole) if target_item else None

        try:
            # Prevent circular reference: reject moving a genre onto itself
            # or onto one of its own descendants.
            if target_id is not None:
                all_genres = self.controller.get.get_all_entities("Genre")
                for item in selected_items:
                    child_id = item.data(0, Qt.UserRole)
                    if child_id == target_id or is_hierarchy_descendant(
                        child_id, target_id, all_genres, id_attr="genre_id"
                    ):
                        show_status_message(
                            self,
                            "Cannot make a genre a child of itself or its descendants.",
                        )
                        event.ignore()
                        return

            # Move all selected items to the new parent
            for item in selected_items:
                child_id = item.data(0, Qt.UserRole)
                logger.info(f"Moving {child_id} to {target_id}")
                self.controller.update.update_entity(
                    "Genre", child_id, parent_id=target_id
                )

            self.load_genres()  # Refresh tree
            self.genre_updated.emit()
            event.accept()

        except (SQLAlchemyError, RuntimeError) as e:
            logger.error(f"Error moving genre: {e!s}")
            event.ignore()

    def show_context_menu(self, pos):
        """Display context menu for genre operations."""
        item = self.tree.itemAt(pos)
        menu = QMenu()

        if item:
            selected_items = self.tree.selectedItems()

            if len(selected_items) == 1:
                # Single selection - store the current genre ID
                self.current_genre_id = item.data(0, Qt.UserRole)
                menu.addAction("View Tracks", lambda: self.view_tracks_for_selected_genre())
                menu.addAction("Edit", lambda: self.edit_genre(self.current_genre_id))
                menu.addAction("Merge", lambda: self.merge_genre(self.current_genre_id))
                menu.addAction("Split", lambda: self._split_genre())
                menu.addSeparator()
                menu.addAction(
                    "Set Parent...", lambda: self.set_parent_for_selected_genres()
                )
                menu.addAction(
                    "New Parent Genre",
                    lambda: self.create_new_parent(self.current_genre_id),
                )
                menu.addAction(
                    "New Child Genre", lambda: self.create_new_child(self.current_genre_id)
                )
            else:
                # Multiple selection
                self.current_genre_id = None
                menu.addAction(
                    f"View Tracks ({len(selected_items)} genres)",
                    lambda: self.view_tracks_for_selected_genres(selected_items),
                )
                menu.addAction(
                    "Set Parent...", lambda: self.set_parent_for_selected_genres()
                )

            # Always show delete option (works for single or multiple)
            menu.addAction("Delete", lambda: self.delete_selected_genres())
            menu.addSeparator()

        # Applies to the whole hierarchy, not the clicked item -- shown even
        # when right-clicking empty tree space (item is None).
        menu.addAction("Export Hierarchy...", self.export_hierarchy)

        menu.exec_(self.tree.viewport().mapToGlobal(pos))

    def export_hierarchy(self):
        """Export the full genre hierarchy as a box-drawing tree to a
        .txt or .md file, regardless of the current Flat View toggle or
        any active search filter. Sibling order follows the tree's
        currently active sort column/direction."""
        if not self._all_genres:
            show_status_message(self, "No genres available to export.")
            return

        header = self.tree.header()
        sort_column = header.sortIndicatorSection()
        sort_order = header.sortIndicatorOrder()
        direct_counts = self._direct_counts
        recursive_counts = self._recursive_counts

        def sort_key(genre):
            name_key = genre.genre_name.lower()
            if sort_column == 1:
                count = recursive_counts.get(
                    genre.genre_id, direct_counts.get(genre.genre_id, 0)
                )
                primary = -count if sort_order == Qt.DescendingOrder else count
                return (primary, name_key)
            return (
                name_key if sort_order == Qt.AscendingOrder else _ReverseStr(name_key),
            )

        content = render_hierarchy_as_text(
            self._all_genres,
            id_attr="genre_id",
            name_attr="genre_name",
            parent_attr="parent_id",
            sort_key=sort_key,
        )

        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Genre Hierarchy",
            "genre_hierarchy.txt",
            "Text Files (*.txt);;Markdown Files (*.md)",
        )
        if not file_path:
            return

        if file_path.lower().endswith(".md"):
            content = f"```\n{content}\n```"

        try:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(content)
            show_status_message(
                self, f"Exported {len(self._all_genres)} genre(s) to {file_path}"
            )
            logger.info(f"Exported {len(self._all_genres)} genre(s) to {file_path}")
        except OSError as e:
            logger.error(f"Error exporting genre hierarchy: {e}")
            QMessageBox.critical(
                self, "Error", f"Failed to export genre hierarchy:\n{e!s}"
            )

    def view_tracks_for_selected_genre(self):
        """Open tracks view window for selected genre."""
        current_item = self.tree.currentItem()
        if not current_item:
            show_status_message(self, "Please select a genre first.")
            return

        genre_id = current_item.data(0, Qt.UserRole)
        genre = self.controller.get.get_entity_object("Genre", genre_id=genre_id)

        if genre:
            tracks_window = GenreTracksWindow(self.controller, genre, self)
            tracks_window.show()

    def view_tracks_for_selected_genres(self, items):
        """Open a combined, deduplicated tracks view for multiple selected genres."""
        genre_ids = [it.data(0, Qt.UserRole) for it in items]

        try:
            track_genres = self.controller.get.get_all_entities(
                "TrackGenre", genre_id__in=genre_ids
            )
            track_ids = list({tg.track_id for tg in track_genres})
            tracks = (
                self.controller.get.get_all_entities("Track", track_id__in=track_ids)
                if track_ids
                else []
            )
        except SQLAlchemyError as e:
            logger.error(f"Error loading tracks for selected genres: {e}")
            QMessageBox.critical(self, "Error", "Failed to load tracks for genres")
            return

        names = ", ".join(it.text(0) for it in items)
        tracks_window = BaseTrackView(
            controller=self.controller,
            tracks=tracks,
            title=f"Tracks in {len(genre_ids)} genres: {names}",
        )
        tracks_window.exec_()

    def merge_genre(self, source_genre_id):
        """Open the merge dialog for the selected genre."""
        try:
            genre_obj = self.controller.get.get_entity_object(
                "Genre", genre_id=source_genre_id
            )
            if not genre_obj:
                show_status_message(self, "The selected genre no longer exists.")
                return

            merge_dialog = GenreMergeDialog(self.controller, self, genre_obj=genre_obj)

            if merge_dialog.exec_() == QDialog.Accepted:
                self.load_genres()
                self.genre_updated.emit()
                self.status_bar.setText("Genre merge completed successfully")

        except (SQLAlchemyError, RuntimeError) as e:
            logger.error(f"Error merging genre: {e!s}")
            QMessageBox.critical(self, "Error", f"Failed to merge genre: {e!s}")

    def edit_genre(self, genre_id):
        """Open edit dialog for selected genre."""
        try:
            genre = self.controller.get.get_entity_object("Genre", genre_id=genre_id)
            dialog = GenreEditDialog(self.controller, genre)
            if dialog.exec_() == QDialog.Accepted:
                self.load_genres()
                self.genre_updated.emit()
        except (SQLAlchemyError, RuntimeError) as e:
            logger.error(f"Error editing genre: {e!s}")
            QMessageBox.critical(self, "Error", "Failed to edit genre")

    def set_parent_for_selected_genres(self):
        """Open a dialog to set the parent for all selected genres at once."""
        selected_items = self.tree.selectedItems()
        if not selected_items:
            show_status_message(self, "Please select genres to edit.")
            return

        genres = []
        for item in selected_items:
            genre_id = item.data(0, Qt.UserRole)
            genre = self.controller.get.get_entity_object("Genre", genre_id=genre_id)
            if genre:
                genres.append(genre)

        if not genres:
            return

        try:
            dialog = GenreSetParentDialog(self.controller, genres, self)
            if dialog.exec_() == QDialog.Accepted:
                self.load_genres()
                self.genre_updated.emit()
                if len(genres) == 1:
                    self.status_bar.setText(
                        f"Updated parent for '{genres[0].genre_name}'"
                    )
                else:
                    self.status_bar.setText(f"Updated parent for {len(genres)} genres")
        except (SQLAlchemyError, RuntimeError) as e:
            logger.error(f"Error setting parent for genres: {e!s}")
            QMessageBox.critical(self, "Error", "Failed to set parent")

    def create_new_parent(self, genre_id):
        """Create a new genre and insert it as the parent of the given genre.

        The new genre takes over the genre's old parent slot (preserving the
        grandparent chain), and the genre becomes a child of the new genre.
        """
        genre = self.controller.get.get_entity_object("Genre", genre_id=genre_id)
        if not genre:
            show_status_message(self, "The selected genre no longer exists.")
            return

        dialog = GenreEditDialog(self.controller, None)
        if dialog.exec_() != QDialog.Accepted or not dialog.result_genre:
            return

        new_genre = dialog.result_genre
        handle_insert_as_new_relative(
            self.controller,
            self,
            entity_type="Genre",
            id_attr="genre_id",
            name_attr="genre_name",
            is_parent=True,
            entity=genre,
            new_entity=new_genre,
            reload_fn=self.load_genres,
            emit_fn=lambda _ne: self.genre_updated.emit(),
            status_fn=self.status_bar.setText,
        )

    def create_new_child(self, genre_id):
        """Create a new genre and set it as a child of the given genre."""
        genre = self.controller.get.get_entity_object("Genre", genre_id=genre_id)
        if not genre:
            show_status_message(self, "The selected genre no longer exists.")
            return

        dialog = GenreEditDialog(self.controller, None)
        if dialog.exec_() != QDialog.Accepted or not dialog.result_genre:
            return

        new_genre = dialog.result_genre
        handle_insert_as_new_relative(
            self.controller,
            self,
            entity_type="Genre",
            id_attr="genre_id",
            name_attr="genre_name",
            is_parent=False,
            entity=genre,
            new_entity=new_genre,
            reload_fn=self.load_genres,
            emit_fn=lambda _ne: self.genre_updated.emit(),
            status_fn=self.status_bar.setText,
        )

    def _build_delete_confirmation_box(self, message):
        """Build (but don't show) the Yes/No delete confirmation box, with an
        'Also add deleted genre(s) to Excluded Genres list' checkbox attached."""
        box = QMessageBox(self)
        box.setWindowTitle("Confirm Delete")
        box.setText(message)
        box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        # Keep a Python reference on the box itself: setCheckBox() reparents
        # the checkbox in C++, but with no Python variable holding it, the
        # wrapper can be garbage-collected out from under that reparenting
        # and later box.checkBox() calls segfault on the dangling wrapper.
        checkbox = QCheckBox("Also add deleted genre(s) to Excluded Genres list")
        box.setCheckBox(checkbox)
        box._exclusion_checkbox = checkbox
        return box

    def _confirm_delete(self, message):
        """Show the delete confirmation box and return (confirmed, add_to_excluded)."""
        box = self._build_delete_confirmation_box(message)
        confirmed = box.exec_() == QMessageBox.Yes
        return confirmed, box._exclusion_checkbox.isChecked()

    def _add_to_excluded_genres(self, genre_names):
        """Add genre_names to the Excluded Genres config list, case-insensitive
        deduped against what's already there. Returns the count actually added."""
        if not genre_names:
            return 0
        config = self.controller.config
        existing = config.get_excluded_genres()
        existing_lower = {name.lower() for name in existing}
        added = [name for name in genre_names if name.lower() not in existing_lower]
        if not added:
            return 0
        config.set_excluded_genres(existing + added)
        config.save()
        return len(added)

    def delete_selected_genres(self):
        """Delete all selected genres after confirmation."""
        selected_items = self.tree.selectedItems()
        if not selected_items:
            show_status_message(self, "Please select genres to delete.")
            return

        # Get genre names for confirmation message, keeping each tree item
        # paired with its genre_id so we can target the right item on delete
        genre_names = []
        to_delete = []  # list of (item, genre_id, genre_name)

        for item in selected_items:
            genre_id = item.data(0, Qt.UserRole)
            genre = self.controller.get.get_entity_object("Genre", genre_id=genre_id)
            if genre:
                genre_names.append(genre.genre_name)
                to_delete.append((item, genre_id, genre.genre_name))

        if not to_delete:
            return

        # Confirm deletion
        if len(genre_names) == 1:
            message = f"Are you sure you want to delete '{genre_names[0]}'?"
        else:
            message = (
                f"Are you sure you want to delete {len(genre_names)} genres?\n\n"
                + "\n".join(f"• {name}" for name in genre_names)
            )

        confirmed, add_to_excluded = self._confirm_delete(message)

        if confirmed:
            try:
                # Delete each genre and remove just its tree item, rather
                # than reloading the whole tree (which would collapse/expand
                # items back to their saved state instead of leaving the
                # rest of the tree untouched).
                success_count = 0
                deleted_names = []
                for item, genre_id, genre_name in to_delete:
                    try:
                        self.controller.delete.delete_entity("Genre", genre_id)
                        self._remove_genre_tree_item(item)
                        success_count += 1
                        deleted_names.append(genre_name)
                    except SQLAlchemyError as e:
                        logger.error(f"Error deleting genre {genre_id}: {e!s}")

                self.genre_updated.emit()

                if success_count == len(to_delete):
                    status = f"Deleted {success_count} genre(s)"
                else:
                    status = f"Deleted {success_count} of {len(to_delete)} genre(s)"

                if add_to_excluded:
                    excluded_count = self._add_to_excluded_genres(deleted_names)
                    status += f", added {excluded_count} to Excluded Genres"

                self.status_bar.setText(status)

            except (SQLAlchemyError, RuntimeError) as e:
                logger.error(f"Error in bulk delete: {e!s}")
                QMessageBox.critical(
                    self, "Error", "Failed to delete one or more genres"
                )

    def delete_genre(self, genre_id):
        """Delete single genre after confirmation (kept for backward compatibility)."""
        try:
            genre = self.controller.get.get_entity_object("Genre", genre_id=genre_id)
            confirmed, add_to_excluded = self._confirm_delete(
                f"Are you sure you want to delete '{genre.genre_name}'?"
            )

            if confirmed:
                self.controller.delete.delete_entity("Genre", genre_id)
                item = self._find_genre_item(genre_id)
                if item:
                    self._remove_genre_tree_item(item)
                self.genre_updated.emit()
                status = f"Deleted {genre.genre_name}"
                if add_to_excluded:
                    excluded_count = self._add_to_excluded_genres([genre.genre_name])
                    status += f", added {excluded_count} to Excluded Genres"
                self.status_bar.setText(status)

        except (AttributeError, SQLAlchemyError, RuntimeError) as e:
            logger.error(f"Error deleting genre: {e!s}")
            QMessageBox.critical(self, "Error", "Failed to delete genre")

    def _find_genre_item(self, genre_id, container=None):
        """Recursively find the tree item for a genre_id."""
        container = container or self.tree.invisibleRootItem()
        for i in range(container.childCount()):
            child = container.child(i)
            if child.data(0, Qt.UserRole) == genre_id:
                return child
            found = self._find_genre_item(genre_id, child)
            if found:
                return found
        return None

    def _remove_genre_tree_item(self, item):
        """Remove a single genre's tree item without reloading the whole tree.

        Deleting a genre nullifies its children's parent_id in the DB
        (they become top-level genres), so their tree items are promoted
        to the top level rather than deleted along with their parent.
        The rest of the tree - including every other item's expanded or
        collapsed state - is left untouched.
        """
        children = item.takeChildren()
        container = item.parent() or self.tree.invisibleRootItem()
        container.removeChild(item)

        root = self.tree.invisibleRootItem()
        for child in children:
            root.addChild(child)
            self._reindent_subtree(child, 0)

        if children:
            # Native sorting doesn't reposition items on insert (verified
            # empirically) -- re-apply the currently active sort column/
            # order so the promoted items land in the right spot, whether
            # that's alphabetical or by track count.
            header = self.tree.header()
            self.tree.sortByColumn(
                header.sortIndicatorSection(), header.sortIndicatorOrder()
            )

    def _reindent_subtree(self, item, depth):
        """Refresh depth-based icons after an item moves to a new tree level."""
        item.setIcon(0, icon_for_depth(depth))
        for i in range(item.childCount()):
            self._reindent_subtree(item.child(i), depth + 1)

    def startDrag(self, supportedActions):
        """Override to handle multi-selection drag better."""
        selected_items = self.tree.selectedItems()
        if len(selected_items) > 1:
            # Show a count of selected items during drag
            drag = QDrag(self.tree)
            mime_data = QMimeData()
            # You could customize the drag icon/text here
            drag.setMimeData(mime_data)
            drag.exec_(supportedActions)
        else:
            super().startDrag(supportedActions)
