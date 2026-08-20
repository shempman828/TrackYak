from collections import defaultdict

from PySide6.QtCore import QObject, Qt, QThread, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)
from sqlalchemy.exc import SQLAlchemyError

from src.common.base_split_dialog import SplitDBDialog
from src.common.hierarchy_tree_style import (
    collect_expanded_ids,
    configure_hierarchy_tree,
    filter_tree_widget,
    icon_for_depth,
    insert_as_new_child,
    insert_as_new_parent,
    is_hierarchy_descendant,
    restore_expanded_ids_or_expand_all,
)
from src.core.logger_config import logger
from src.core.status_utility import show_status_message
from src.role.role_detail_tab import RoleDetailTab
from src.role.role_edit_dialog import RoleEditDialog
from src.role.role_merge import RoleMergeDialog

# ---------------------------------------------------------------------------
# Background worker — does ALL the expensive database work on a separate thread
# so the UI never freezes.
# ---------------------------------------------------------------------------


class RoleLoaderWorker(QObject):
    """
    Runs on a background thread.
    Fetches all roles and association counts in just 3 queries total,
    then emits the results back to the main thread.
    """

    # Emitted when loading succeeds.
    # Payload: (all_roles, album_counts_by_role_id, track_counts_by_role_id)
    # Uses `object` rather than `list`/`dict` because PySide6's queued
    # cross-thread delivery can fail to copy-convert plain dict/list
    # signal args, logging "_pythonToCppCopy" errors (or worse, dropping
    # the payload). `object` passes the Python object through untouched.
    finished = Signal(object, object, object)

    # Emitted if something goes wrong.
    error = Signal(str)

    def __init__(self, controller):
        super().__init__()
        self.controller = controller

    def run(self):
        """Fetch everything we need in as few queries as possible."""
        try:
            # --- Query 1: all roles ---
            all_roles = self.controller.get.get_all_entities("Role") or []

            # --- Query 2: ALL album associations at once ---
            all_album_links = (
                self.controller.get.get_all_entities("AlbumRoleAssociation") or []
            )

            # --- Query 3: ALL track associations at once ---
            all_track_links = (
                self.controller.get.get_all_entities("TrackArtistRole") or []
            )

            # Build count lookup dicts  {role_id: count}
            # This replaces the previous per-role loop that made 1000+ queries.
            album_counts: dict[int, int] = defaultdict(int)
            for link in all_album_links:
                album_counts[link.role_id] += 1

            track_counts: dict[int, int] = defaultdict(int)
            for link in all_track_links:
                track_counts[link.role_id] += 1

            self.finished.emit(all_roles, dict(album_counts), dict(track_counts))

        except Exception as e:
            # Intentional broad boundary catch: this runs on a QThread and must
            # not let an exception kill the thread silently.
            logger.exception("Role count scan failed")
            self.error.emit(str(e))


# ---------------------------------------------------------------------------
# Main view
# ---------------------------------------------------------------------------


class RoleView(QWidget):
    """Main view for displaying and interacting with role data with hierarchy support."""

    role_updated = Signal()

    def __init__(self, controller):
        super().__init__()
        self.controller = controller
        self.current_role_id: int | None = None

        # These are populated after background loading finishes.
        # We keep them on the instance so _on_role_selected can use them
        # without hitting the database again.
        self._album_counts: dict[int, int] = {}
        self._track_counts: dict[int, int] = {}
        self._all_roles: list = []

        # "name" (alphabetical) or "count" (total assignments, descending)
        self.sort_mode = "name"
        self.flat_view = False

        # Keep a reference to the running thread so it isn't garbage-collected.
        self._loader_thread: QThread | None = None

        self._setup_ui()
        self._connect_signals()
        self.load_roles()
        logger.info("RoleView initialized with unified role tree")

    def _setup_ui(self):
        """Initialize all UI components with unified role tree."""
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(5, 5, 5, 5)

        # Top row: Search bar + New Role + Sort + Expand/Collapse
        top_row = QHBoxLayout()

        # Search bar with clear button
        self.search_field = QLineEdit()
        self.search_field.setPlaceholderText("Filter roles...")
        self.search_field.setClearButtonEnabled(True)
        top_row.addWidget(self.search_field)

        # New Role button
        self.new_role_button = QPushButton("New Role")
        self.new_role_button.clicked.connect(self.create_role)
        top_row.addWidget(self.new_role_button)

        # Sort mode selector
        self.sort_combo = QComboBox()
        self.sort_combo.addItem("Sort: Name (A-Z)", "name")
        self.sort_combo.addItem("Sort: Count", "count")
        self.sort_combo.currentIndexChanged.connect(self._on_sort_mode_changed)
        top_row.addWidget(self.sort_combo)

        # Expand All / Collapse All buttons
        self.expand_all_button = QPushButton("Expand All")
        self.expand_all_button.clicked.connect(lambda: self.role_tree.expandAll())
        top_row.addWidget(self.expand_all_button)

        self.collapse_all_button = QPushButton("Collapse All")
        self.collapse_all_button.clicked.connect(lambda: self.role_tree.collapseAll())
        top_row.addWidget(self.collapse_all_button)

        self.flat_view_button = QPushButton("Flat View")
        self.flat_view_button.setCheckable(True)
        self.flat_view_button.setChecked(False)
        self.flat_view_button.setToolTip(
            "Toggle between the hierarchical tree and a flat alphabetical list"
        )
        self.flat_view_button.clicked.connect(self.toggle_flat_view)
        top_row.addWidget(self.flat_view_button)

        self.main_layout.addLayout(top_row)

        # Loading indicator — shown while the background thread is running
        self.loading_label = QLabel("Loading roles…")
        self.loading_label.setAlignment(Qt.AlignCenter)
        self.loading_label.setProperty("textRole", "note")
        self.loading_label.hide()  # Hidden by default
        self.main_layout.addWidget(self.loading_label)

        # Create splitter for resizable panels
        self.splitter = QSplitter(Qt.Horizontal)
        self.main_layout.addWidget(self.splitter, 1)  # 1 = stretch factor

        # Left panel: Role tree
        left_container = QWidget()
        left_layout = QVBoxLayout(left_container)
        left_layout.setContentsMargins(0, 0, 0, 0)

        self.role_tree = self._create_role_tree()
        left_layout.addWidget(self.role_tree)

        self.splitter.addWidget(left_container)

        # Right panel: Detail area
        self.right_panel = QWidget()
        self.right_panel.setMinimumWidth(250)
        self.right_layout = QVBoxLayout(self.right_panel)
        self.right_layout.setContentsMargins(10, 0, 0, 0)

        # Placeholder shown when no role is selected
        self.detail_placeholder = QLabel("Select a role to view details")
        self.detail_placeholder.setAlignment(Qt.AlignCenter)
        self.detail_placeholder.setProperty("textRole", "placeholder")
        self.right_layout.addWidget(self.detail_placeholder)

        self.splitter.addWidget(self.right_panel)

        # Set initial splitter sizes (2:1 ratio)
        self.splitter.setSizes([400, 200])

        # Status bar
        self.status_bar = QLabel()
        self.status_bar.setAlignment(Qt.AlignCenter)
        self.main_layout.addWidget(self.status_bar)

        # Initialize detail_tab as None
        self.detail_tab = None

    def _create_role_tree(self):
        """Create a unified role tree with common configuration."""
        tree = QTreeWidget()
        configure_hierarchy_tree(tree)
        tree.itemChanged.connect(self.on_item_edited)

        # Use a wrapper to maintain self context in drop event
        tree.dropEvent = lambda event: self.on_drop_event(event)

        tree.customContextMenuRequested.connect(self.show_context_menu)

        tree.setAlternatingRowColors(True)

        return tree

    def _connect_signals(self):
        """Connect UI signals to their handlers."""
        self.search_field.textChanged.connect(self._filter_roles)
        self.role_tree.itemSelectionChanged.connect(self._on_role_selected)

    def _on_sort_mode_changed(self, index):
        """Re-render the tree in the newly selected sort order without re-querying the DB."""
        self.sort_mode = self.sort_combo.itemData(index)
        if self._all_roles:
            self._rebuild_tree()

    # -----------------------------------------------------------------------
    # Loading — runs database work on a background thread
    # -----------------------------------------------------------------------

    def load_roles(self):
        """
        Kick off background loading.

        1. Show the spinner and disable the tree so the user knows loading is happening.
        2. Spawn a QThread running RoleLoaderWorker.
        3. When the worker finishes, _on_roles_loaded() is called on the main thread
           to safely update the UI.
        """
        # If a load is already in progress, don't start another one.
        try:
            if self._loader_thread and self._loader_thread.isRunning():
                return
        except RuntimeError:
            self._loader_thread = None

        # Show spinner, disable interaction
        self._set_loading_state(True)

        # Create the worker and thread
        self._loader_thread = QThread(self)
        self._worker = RoleLoaderWorker(self.controller)
        self._worker.moveToThread(self._loader_thread)

        # Wire up signals
        self._loader_thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._on_roles_loaded)
        self._worker.error.connect(self._on_roles_load_error)

        # Clean up the thread when done
        self._worker.finished.connect(self._loader_thread.quit)
        self._worker.error.connect(self._loader_thread.quit)
        self._loader_thread.finished.connect(self._loader_thread.deleteLater)

        self._loader_thread.start()

    def _set_loading_state(self, is_loading: bool):
        """Show or hide the spinner and enable/disable the tree accordingly."""
        self.loading_label.setVisible(is_loading)
        self.role_tree.setEnabled(not is_loading)
        self.search_field.setEnabled(not is_loading)
        if is_loading:
            self.status_bar.setText("Loading…")

    def _on_roles_loaded(self, all_roles: list, album_counts: dict, track_counts: dict):
        """
        Called on the MAIN thread once the background worker finishes.
        Safe to update UI here.
        """
        try:
            # Store data for use in _on_role_selected and sort-mode changes
            # (avoids re-querying the DB)
            self._album_counts = album_counts
            self._track_counts = track_counts
            self._all_roles = all_roles

            self._rebuild_tree()

        except (AttributeError, RuntimeError) as e:
            logger.error(f"Failed to populate role tree: {e!s}", exc_info=True)
            self.role_tree.clear()
            error_item = QTreeWidgetItem(["Error loading roles"])
            error_item.setFlags(error_item.flags() & ~Qt.ItemIsEditable)
            error_item.setData(0, Qt.UserRole, None)
            self.role_tree.addTopLevelItem(error_item)

        finally:
            # Always hide the spinner when we're done, success or failure
            self._set_loading_state(False)

    def _rebuild_tree(self):
        """
        Rebuild the tree widget from cached role/count data (no DB calls).
        Used both after loading and when the sort mode changes.
        Preserves which items were expanded across the rebuild.
        """
        all_roles = self._all_roles
        album_counts = self._album_counts
        track_counts = self._track_counts

        # Remember which roles were expanded so the rebuild doesn't collapse
        # everything the user had opened.
        expanded_ids = collect_expanded_ids(self.role_tree)
        is_initial_load = self.role_tree.topLevelItemCount() == 0

        self.role_tree.clear()

        if not all_roles:
            logger.warning("No roles found in the database")
            placeholder = QTreeWidgetItem(["No roles found"])
            placeholder.setFlags(placeholder.flags() & ~Qt.ItemIsEditable)
            placeholder.setData(0, Qt.UserRole, None)
            self.role_tree.addTopLevelItem(placeholder)
            return

        # Build lookup structures
        role_map = {role.role_id: role for role in all_roles}

        children_map: dict = defaultdict(list)
        for role in all_roles:
            if role.parent_id in role_map:
                children_map[role.parent_id].append(role)
            elif role.parent_id is None:
                children_map[None].append(role)

        # Build the tree — uses pre-fetched counts, zero extra queries
        if self.flat_view:
            root_count = self._build_role_flat(
                all_roles, role_map, self.role_tree, album_counts, track_counts
            )
        else:
            root_count = self._build_role_tree(
                None,
                children_map,
                role_map,
                0,
                self.role_tree,
                album_counts,
                track_counts,
            )

        # Restore expansion state, or expand everything on first build
        restore_expanded_ids_or_expand_all(self.role_tree, expanded_ids, is_initial_load)

        # Build status bar summary from the cached counts (no DB calls)
        total = len(all_roles)
        unassigned = track_only = album_only = mixed = 0

        for role in all_roles:
            has_album = album_counts.get(role.role_id, 0) > 0
            has_track = track_counts.get(role.role_id, 0) > 0

            if not has_album and not has_track:
                unassigned += 1
            elif has_track and not has_album:
                track_only += 1
            elif has_album and not has_track:
                album_only += 1
            else:
                mixed += 1

        self.status_bar.setText(
            f"Showing {total} roles total: "
            f"{mixed} mixed, {track_only} track-only, "
            f"{album_only} album-only, {unassigned} unassigned"
        )

        logger.info(
            f"Loaded {total} roles into unified tree with hierarchy "
            f"({root_count} root items)"
        )

    def toggle_flat_view(self):
        """Toggle between the nested hierarchy and a flat alphabetical list."""
        self.flat_view = self.flat_view_button.isChecked()
        self.flat_view_button.setText("Tree View" if self.flat_view else "Flat View")
        self.expand_all_button.setEnabled(not self.flat_view)
        self.collapse_all_button.setEnabled(not self.flat_view)
        # Drag-and-drop reparenting doesn't make sense against a flat,
        # always-sorted list.
        self.role_tree.setDragEnabled(not self.flat_view)
        if self._all_roles:
            self._rebuild_tree()
            # Reapply any active search filter, since the tree was just
            # rebuilt from scratch.
            self._filter_roles(self.search_field.text())

    def _on_roles_load_error(self, error_message: str):
        """Called on the main thread if the background worker hits an exception."""
        logger.error(f"RoleLoaderWorker error: {error_message}")
        self.role_tree.clear()
        error_item = QTreeWidgetItem(["Error loading roles"])
        error_item.setFlags(error_item.flags() & ~Qt.ItemIsEditable)
        error_item.setData(0, Qt.UserRole, None)
        self.role_tree.addTopLevelItem(error_item)
        self._set_loading_state(False)

    # -----------------------------------------------------------------------
    # Tree building — now accepts pre-fetched count dicts, never queries DB
    # -----------------------------------------------------------------------

    def _build_role_tree(
        self,
        parent_item,
        children_map,
        role_map,
        depth,
        tree_widget,
        album_counts: dict,
        track_counts: dict,
    ):
        """
        Recursively build the tree structure using pre-fetched count dicts.
        No database calls happen here — counts are looked up from the dicts.
        """
        parent_id = parent_item.data(0, Qt.UserRole) if parent_item else None
        roles = children_map.get(parent_id, [])

        def _total(role):
            return album_counts.get(role.role_id, 0) + track_counts.get(role.role_id, 0)

        if self.sort_mode == "count":
            sorted_roles = sorted(roles, key=_total, reverse=True)
        else:
            sorted_roles = sorted(roles, key=lambda r: r.role_name.lower())

        for role in sorted_roles:
            item = self._make_role_item(role, depth, role_map, album_counts, track_counts)

            if parent_item:
                parent_item.addChild(item)
            else:
                tree_widget.addTopLevelItem(item)

            # Recursively build children
            self._build_role_tree(
                item,
                children_map,
                role_map,
                depth + 1,
                tree_widget,
                album_counts,
                track_counts,
            )

        return len(roles)

    def _make_role_item(self, role, depth, role_map, album_counts, track_counts):
        """Build a single role's tree item, shared by the tree and flat builders."""
        # Look up counts from the pre-fetched dicts (O(1), no DB call)
        album_count = album_counts.get(role.role_id, 0)
        track_count = track_counts.get(role.role_id, 0)
        total_count = album_count + track_count

        # Display text mirrors the genre tree's "Name (count)" style
        display_text = f"{role.role_name} ({total_count})"

        item = QTreeWidgetItem([display_text])
        item.setData(0, Qt.UserRole, role.role_id)
        item.setFlags(item.flags() | Qt.ItemIsEditable)

        # Store original name and counts for editing / potential future use
        item.setData(1, Qt.UserRole, role.role_name)
        item.setData(0, Qt.UserRole + 1, track_count)
        item.setData(0, Qt.UserRole + 2, album_count)

        item.setIcon(0, icon_for_depth(depth))

        # Gray out unassigned roles
        if total_count == 0:
            item.setForeground(0, QBrush(QColor(128, 128, 128)))

        # Tooltip with detailed information (album/track breakdown kept here)
        tooltip = f"ID: {role.role_id}"
        if role.role_description:
            tooltip += f"\nDescription: {role.role_description}"

        tooltip += f"\nTrack assignments: {track_count}"
        tooltip += f"\nAlbum assignments: {album_count}"

        if role.parent_id:
            parent_role = role_map.get(role.parent_id)
            if parent_role:
                tooltip += f"\nParent: {parent_role.role_name}"

        item.setToolTip(0, tooltip)
        return item

    def _build_role_flat(self, all_roles, role_map, tree_widget, album_counts, track_counts):
        """Populate the tree as a single alphabetical list with no nesting."""
        if self.sort_mode == "count":
            sorted_roles = sorted(
                all_roles,
                key=lambda r: album_counts.get(r.role_id, 0) + track_counts.get(r.role_id, 0),
                reverse=True,
            )
        else:
            sorted_roles = sorted(all_roles, key=lambda r: r.role_name.lower())

        for role in sorted_roles:
            item = self._make_role_item(role, 0, role_map, album_counts, track_counts)
            tree_widget.addTopLevelItem(item)

        return len(all_roles)

    # -----------------------------------------------------------------------
    # Role selection
    # -----------------------------------------------------------------------

    def _on_role_selected(self):
        """Handle role selection and update detail view."""
        try:
            selected = self.role_tree.currentItem()

            # Skip placeholder items
            if not selected or not selected.data(0, Qt.UserRole):
                self._clear_detail_view()
                return

            role_id = selected.data(0, Qt.UserRole)
            role = self.controller.get.get_entity_object("Role", role_id=role_id)
            if not role:
                logger.error(f"Role with ID {role_id} not found")
                self._clear_detail_view()
                return

            self.current_role_id = role_id

            # Remove the placeholder
            if self.detail_placeholder:
                self.detail_placeholder.setParent(None)
                self.detail_placeholder = None

            # Create or update the detail tab. It always loads both album and
            # track assignments for the role, regardless of which are present.
            if not self.detail_tab:
                self.detail_tab = RoleDetailTab(self.controller, role.role_id)
                self.right_layout.addWidget(self.detail_tab)
            else:
                # If detail tab exists but is in wrong parent, move it
                if self.detail_tab.parent() != self.right_panel:
                    self.detail_tab.setParent(None)
                    self.right_layout.addWidget(self.detail_tab)

                self.detail_tab.role_id = role.role_id
                self.detail_tab._load_data()

        except (SQLAlchemyError, RuntimeError) as e:
            logger.error(f"Error handling role selection: {e!s}", exc_info=True)
            self._clear_detail_view()

    def _get_hierarchy_info(self, role):
        """Get hierarchy information for display."""
        info_parts = []

        if role.parent_id:
            parent = self.controller.get.get_entity_object(
                "Role", role_id=role.parent_id
            )
            if parent:
                info_parts.append(f"Parent: {parent.role_name}")

        # Count children
        children = self.controller.get.get_all_entities("Role", parent_id=role.role_id)
        if children:
            info_parts.append(f"Children: {len(children)}")

        return " | ".join(info_parts) if info_parts else "Root role"

    def _clear_detail_view(self):
        """Reset the detail view to empty state."""
        self.current_role_id = None

        # Remove the detail tab if it exists
        if self.detail_tab:
            self.detail_tab.setParent(None)
            self.detail_tab = None

        # Restore the placeholder if not already present
        if not self.detail_placeholder:
            self.detail_placeholder = QLabel("Select a role to view details")
            self.detail_placeholder.setAlignment(Qt.AlignCenter)
            self.detail_placeholder.setProperty("textRole", "placeholder")
            self.right_layout.addWidget(self.detail_placeholder)

    # -----------------------------------------------------------------------
    # Search / filter
    # -----------------------------------------------------------------------

    def _filter_roles(self, text):
        """Filter visible roles based on search text."""
        filter_tree_widget(self.role_tree, text)

    # -----------------------------------------------------------------------
    # Edit / rename / delete / drag-drop
    # -----------------------------------------------------------------------

    def on_item_edited(self, item, column):
        """Handle role name updates."""
        role_id = item.data(0, Qt.UserRole)
        new_text = item.text(column).strip()

        # Extract just the role name (remove counts)
        import re

        # Remove everything after the first parenthesis if present
        match = re.match(r"^(.*?)(?:\s*\(.*\))?$", new_text)
        if match:
            new_name = match.group(1).strip()
        else:
            new_name = new_text

        old_text = item.text(column)  # Store old text in case we need to revert

        try:
            if not new_name:
                raise ValueError("Role name cannot be empty")

            # Check if name already exists
            if self.controller.get.get_entity_object("Role", role_name=new_name):
                raise ValueError("Role name already exists")

            self.controller.update.update_entity("Role", role_id, role_name=new_name)
            self.role_updated.emit()

            # Reload to update counts
            self.load_roles()
            self.status_bar.setText(f"Renamed to {new_name}")

        except ValueError as e:
            show_status_message(self, str(e))
            item.setText(0, old_text)  # Revert to old text
        except (SQLAlchemyError, RuntimeError) as e:
            logger.error(f"Error renaming role: {e!s}")
            QMessageBox.critical(self, "Error", "Failed to rename role")
            item.setText(0, old_text)  # Revert to old text

    def on_drop_event(self, event):
        """Handle parent changes through drag-and-drop, supporting multi-selection."""
        selected_items = self.role_tree.selectedItems()

        if not selected_items:
            event.ignore()
            return

        parent_item = self.role_tree.itemAt(event.pos())
        parent_id = parent_item.data(0, Qt.UserRole) if parent_item else None

        try:
            moved_any = False
            for child_item in selected_items:
                child_id = child_item.data(0, Qt.UserRole)
                if child_id is None or child_id == parent_id:
                    continue

                # Prevent circular references
                if is_hierarchy_descendant(
                    child_id, parent_id, self._all_roles, id_attr="role_id"
                ):
                    show_status_message(
                        self,
                        f"Moving '{child_item.text(0)}' there would create a "
                        "circular reference in the hierarchy.",
                    )
                    continue

                logger.info(f"Moving role {child_id} to parent {parent_id}")
                self.controller.update.update_entity(
                    "Role", child_id, parent_id=parent_id
                )
                moved_any = True

            if moved_any:
                self.load_roles()  # Refresh tree
                self.role_updated.emit()
                event.accept()
            else:
                event.ignore()

        except (SQLAlchemyError, RuntimeError) as e:
            logger.error(f"Error moving role: {e!s}")
            event.ignore()

    def show_context_menu(self, pos):
        """Display context menu for role operations."""
        item = self.role_tree.itemAt(pos)
        menu = QMenu()

        # If clicking on empty space, show "New Role" option
        if not item or not item.data(0, Qt.UserRole):
            menu.addAction("New Role", lambda: self.create_role(pos))
            menu.exec_(self.role_tree.viewport().mapToGlobal(pos))
            return

        self.current_role_id = item.data(0, Qt.UserRole)

        # If clicking on a valid role item, show role operations
        menu.addAction("Rename", lambda: self.role_tree.editItem(item, 0))
        menu.addAction("Edit", lambda: self.edit_role(self.current_role_id))
        menu.addAction("Merge…", lambda: self.merge_role(self.current_role_id))
        menu.addAction("Split…", lambda: self.split_role(self.current_role_id))
        menu.addSeparator()
        menu.addAction(
            "New Parent Role", lambda: self.create_new_parent_role(self.current_role_id)
        )
        menu.addAction(
            "New Child Role", lambda: self.create_new_child_role(self.current_role_id)
        )
        menu.addSeparator()
        menu.addAction("Delete", lambda: self.delete_role(self.current_role_id))

        menu.exec_(self.role_tree.viewport().mapToGlobal(pos))

    def create_role(self, pos=None):
        """Create a new role."""
        try:
            dialog = RoleEditDialog(self.controller, None, self)
            if dialog.exec_() == QDialog.Accepted:
                self.load_roles()
                self.role_updated.emit()

        except RuntimeError as e:
            logger.error(f"Error creating role: {e!s}")
            QMessageBox.critical(self, "Error", "Failed to create role")

    def edit_role(self, role_id):
        """Open edit dialog for selected role."""
        try:
            role = (
                self.controller.get.get_entity_object("Role", role_id=role_id)
                if role_id
                else None
            )
            dialog = RoleEditDialog(self.controller, role, self)
            if dialog.exec_() == QDialog.Accepted:
                self.load_roles()
                self.role_updated.emit()
        except (SQLAlchemyError, RuntimeError) as e:
            logger.error(f"Error editing role: {e!s}")
            QMessageBox.critical(self, "Error", "Failed to edit role")

    def merge_role(self, source_role_id):
        """Open the merge dialog for the selected role (e.g. combine 'Guitar' and 'Guitarist')."""
        try:
            role_obj = self.controller.get.get_entity_object(
                "Role", role_id=source_role_id
            )
            if not role_obj:
                show_status_message(self, "The selected role no longer exists.")
                return

            merge_dialog = RoleMergeDialog(self.controller, self, role_obj=role_obj)

            if merge_dialog.exec_() == QDialog.Accepted:
                self.load_roles()
                self.role_updated.emit()
                self.status_bar.setText("Role merge completed successfully")

        except (SQLAlchemyError, RuntimeError) as e:
            logger.error(f"Error merging role: {e!s}")
            QMessageBox.critical(self, "Error", f"Failed to merge role: {e!s}")

    def split_role(self, role_id):
        """Open the split dialog for the selected role (e.g. split 'Viola &
        Violin' into 'Viola' and 'Violin')."""
        try:
            role_obj = self.controller.get.get_entity_object("Role", role_id=role_id)
            if not role_obj:
                show_status_message(self, "The selected role no longer exists.")
                return

            split_dialog = SplitDBDialog(
                self.controller.split, "Role", role_obj, self, get_helper=self.controller.get
            )

            if split_dialog.exec_() == QDialog.Accepted:
                self.load_roles()
                self.role_updated.emit()
                self.status_bar.setText("Role split completed successfully")

        except (SQLAlchemyError, RuntimeError) as e:
            logger.error(f"Error splitting role: {e!s}")
            QMessageBox.critical(self, "Error", f"Failed to split role: {e!s}")

    def create_new_parent_role(self, role_id):
        """Create a new role and insert it as the parent of the given role.

        The new role takes over the role's old parent slot (preserving the
        grandparent chain), and the role becomes a child of the new role.
        """
        role = self.controller.get.get_entity_object("Role", role_id=role_id)
        if not role:
            show_status_message(self, "The selected role no longer exists.")
            return

        dialog = RoleEditDialog(self.controller, None, self)
        if dialog.exec_() != QDialog.Accepted or not dialog.result_role:
            return

        new_role = dialog.result_role
        try:
            insert_as_new_parent(self.controller, "Role", "role_id", role, new_role)
            self.load_roles()
            self.role_updated.emit()
            self.status_bar.setText(
                f"Created '{new_role.role_name}' as parent of '{role.role_name}'"
            )
        except (SQLAlchemyError, RuntimeError) as e:
            logger.error(f"Error creating new parent role: {e!s}")
            QMessageBox.critical(self, "Error", "Failed to create new parent role")

    def create_new_child_role(self, role_id):
        """Create a new role and set it as a child of the given role."""
        role = self.controller.get.get_entity_object("Role", role_id=role_id)
        if not role:
            show_status_message(self, "The selected role no longer exists.")
            return

        dialog = RoleEditDialog(self.controller, None, self)
        if dialog.exec_() != QDialog.Accepted or not dialog.result_role:
            return

        new_role = dialog.result_role
        try:
            insert_as_new_child(self.controller, "Role", "role_id", role, new_role)
            self.load_roles()
            self.role_updated.emit()
            self.status_bar.setText(
                f"Created '{new_role.role_name}' as child of '{role.role_name}'"
            )
        except (SQLAlchemyError, RuntimeError) as e:
            logger.error(f"Error creating new child role: {e!s}")
            QMessageBox.critical(self, "Error", "Failed to create new child role")

    def delete_role(self, role_id):
        """Delete selected role after confirmation."""
        try:
            role = self.controller.get.get_entity_object("Role", role_id=role_id)

            # Check if role has children
            children = self.controller.get.get_all_entities("Role", parent_id=role_id)
            if children:
                QMessageBox.warning(
                    self,
                    "Cannot Delete",
                    f"Cannot delete '{role.role_name}' because it has {len(children)} child roles. "
                    "Please move or delete the child roles first.",
                )
                return

            reply = QMessageBox.question(
                self,
                "Confirm Delete",
                f"Are you sure you want to delete '{role.role_name}'?",
                QMessageBox.Yes | QMessageBox.No,
            )

            if reply == QDialog.Yes:
                self.controller.delete.delete_entity("Role", role_id)
                self.load_roles()
                self.role_updated.emit()
                self.status_bar.setText(f"Deleted {role.role_name}")

        except (AttributeError, SQLAlchemyError, RuntimeError) as e:
            logger.error(f"Error deleting role: {e!s}")
            QMessageBox.critical(self, "Error", "Failed to delete role")
