from PySide6.QtCore import Qt
from PySide6.QtWidgets import QTreeWidgetItem

from src.common.widgets.hierarchy_tree_style import icon_for_depth


class _GenreTreeItem(QTreeWidgetItem):
    """Sorts the Tracks column numerically by recursive track count instead of lexicographically."""

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


class GenreTreeBuilder:
    """Builds GenreView's tree/flat-list items from pre-fetched genre and count data."""

    def build_genre_tree(self, parent_item, children_map, genre_map, direct_counts, recursive_counts, depth, tree_widget, _visited=None):
        """Recursively build the tree structure; native Qt sorting orders each sibling group."""
        parent_id = parent_item.data(0, Qt.UserRole) if parent_item else None
        if parent_id is not None:
            if _visited and parent_id in _visited:
                # Cyclic parent_id chain (shouldn't happen); stop descending
                # instead of recursing forever.
                return
            _visited = (_visited or set()) | {parent_id}

        for genre in children_map.get(parent_id, []):
            own_count = direct_counts.get(genre.genre_id, 0)
            recursive_count = recursive_counts.get(genre.genre_id, own_count)
            item = self._make_genre_item(genre, own_count, recursive_count, depth, genre_map)

            if parent_item:
                parent_item.addChild(item)
            else:
                tree_widget.addTopLevelItem(item)

            self.build_genre_tree(item, children_map, genre_map, direct_counts, recursive_counts, depth + 1, tree_widget, _visited)

    def build_genre_flat(self, genres, genre_map, direct_counts, recursive_counts, tree_widget):
        """Populate the tree as a single unnested list; native Qt sorting orders it."""
        for genre in genres:
            own_count = direct_counts.get(genre.genre_id, 0)
            recursive_count = recursive_counts.get(genre.genre_id, own_count)
            item = self._make_genre_item(genre, own_count, recursive_count, 0, genre_map)
            tree_widget.addTopLevelItem(item)

    @staticmethod
    def _format_track_count(own_count: int, recursive_count: int) -> str:
        """Build the compact track-count text for the Tracks column."""
        if recursive_count != own_count:
            # Has subgenres contributing additional tracks, e.g. "12 · 42"
            return f"{own_count} · {recursive_count}"
        # Counts match — just the one number, e.g. "5"
        return str(own_count)

    @staticmethod
    def _style_count_cell(item: QTreeWidgetItem) -> None:
        """Right-align and de-emphasize the Tracks column."""
        item.setTextAlignment(1, Qt.AlignRight | Qt.AlignVCenter)
        font = item.font(1)
        font.setItalic(True)
        item.setFont(1, font)
        if "·" in item.text(1):
            item.setToolTip(1, "Own tracks · total including subgenres")

    def _make_genre_item(self, genre, own_count, recursive_count, depth, genre_map):
        """Build a single genre's tree item, shared by the tree and flat builders."""
        # `genre` was fetched on GenreLoaderWorker's background thread and is
        # detached by the time it reaches here (its session was released
        # right after the query), so looking up its parent through
        # `genre_map` (by `parent_id`) rather than the ORM `.parent`
        # relationship avoids a lazy-load DetachedInstanceError.
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
