"""role_tree_builder.py"""

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import QTreeWidgetItem

from src.common.widgets.hierarchy_tree_style import icon_for_depth


class RoleTreeBuilder:
    """Builds RoleView's tree/flat-list items from pre-fetched role and
    count data. No database calls happen here — counts are looked up from
    the dicts RoleLoaderWorker already fetched.

    Holds a back-reference to the view for `sort_mode`, the one piece of
    view state that changes how a rebuild orders its rows.
    """

    def __init__(self, view) -> None:
        self.view = view

    def build_role_tree(
        self,
        parent_item,
        children_map,
        role_map,
        depth,
        tree_widget,
        album_counts: dict,
        track_counts: dict,
        recursive_counts: dict,
    ):
        """Recursively build the tree structure using pre-fetched count dicts."""
        parent_id = parent_item.data(0, Qt.UserRole) if parent_item else None
        roles = children_map.get(parent_id, [])

        def _total(role):
            return recursive_counts.get(role.role_id, 0)

        if self.view.sort_mode == "count":
            sorted_roles = sorted(roles, key=_total, reverse=True)
        else:
            sorted_roles = sorted(roles, key=lambda r: r.role_name.lower())

        for role in sorted_roles:
            item = self._make_role_item(
                role, depth, role_map, album_counts, track_counts, recursive_counts
            )

            if parent_item:
                parent_item.addChild(item)
            else:
                tree_widget.addTopLevelItem(item)

            # Recursively build children
            self.build_role_tree(
                item,
                children_map,
                role_map,
                depth + 1,
                tree_widget,
                album_counts,
                track_counts,
                recursive_counts,
            )

        return len(roles)

    @staticmethod
    def _format_role_count(own_count: int, recursive_count: int) -> str:
        """Build the compact count text for a role's display label, mirroring
        GenreView._format_track_count / PlaylistView._format_track_count."""
        if recursive_count != own_count:
            # Has sub-roles contributing additional assignments, e.g. "12 · 42"
            return f"{own_count} · {recursive_count}"
        # Counts match — just the one number, e.g. "5"
        return str(own_count)

    def _make_role_item(self, role, depth, role_map, album_counts, track_counts, recursive_counts):
        """Build a single role's tree item, shared by the tree and flat builders."""
        # Look up counts from the pre-fetched dicts (O(1), no DB call).
        # Album and track assignments are collapsed into one flat count per
        # the genre/playlist convention -- the breakdown is kept in the
        # tooltip only.
        album_count = album_counts.get(role.role_id, 0)
        track_count = track_counts.get(role.role_id, 0)
        own_count = album_count + track_count
        recursive_count = recursive_counts.get(role.role_id, own_count)

        count_text = self._format_role_count(own_count, recursive_count)
        display_text = f"{role.role_name} ({count_text})"

        item = QTreeWidgetItem([display_text])
        item.setData(0, Qt.UserRole, role.role_id)
        item.setFlags(item.flags() | Qt.ItemIsEditable)

        # Store original name and counts for editing / potential future use
        item.setData(1, Qt.UserRole, role.role_name)
        item.setData(0, Qt.UserRole + 1, track_count)
        item.setData(0, Qt.UserRole + 2, album_count)

        item.setIcon(0, icon_for_depth(depth))

        # Gray out roles with no assignments anywhere in their subtree
        if recursive_count == 0:
            item.setForeground(0, QBrush(QColor(128, 128, 128)))

        # Tooltip with detailed information (album/track breakdown kept here)
        tooltip = f"ID: {role.role_id}"
        if role.role_description:
            tooltip += f"\nDescription: {role.role_description}"

        tooltip += f"\nTrack assignments: {track_count}"
        tooltip += f"\nAlbum assignments: {album_count}"
        if recursive_count != own_count:
            tooltip += f"\nTotal including sub-roles: {recursive_count}"

        if role.parent_id:
            parent_role = role_map.get(role.parent_id)
            if parent_role:
                tooltip += f"\nParent: {parent_role.role_name}"

        item.setToolTip(0, tooltip)
        return item

    def build_role_flat(
        self, all_roles, role_map, tree_widget, album_counts, track_counts, recursive_counts
    ):
        """Populate the tree as a single alphabetical list with no nesting."""
        if self.view.sort_mode == "count":
            sorted_roles = sorted(
                all_roles, key=lambda r: recursive_counts.get(r.role_id, 0), reverse=True
            )
        else:
            sorted_roles = sorted(all_roles, key=lambda r: r.role_name.lower())

        for role in sorted_roles:
            item = self._make_role_item(
                role, 0, role_map, album_counts, track_counts, recursive_counts
            )
            tree_widget.addTopLevelItem(item)

        return len(all_roles)
