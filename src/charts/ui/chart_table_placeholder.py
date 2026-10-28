"""Shared helpers for the chart result trees: empty-state placeholder and numeric column sort."""

from collections.abc import Callable
from contextlib import contextmanager

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QTreeWidget, QTreeWidgetItem, QVBoxLayout

SORT_VALUE_ROLE = Qt.UserRole + 1


class NumericSortTreeItem(QTreeWidgetItem):
    """Tree item that sorts its tree's numeric columns by SORT_VALUE_ROLE and the rest case-insensitively."""

    def __lt__(self, other):
        tree = self.treeWidget()
        column = tree.sortColumn() if tree else 0
        if tree is not None and column in getattr(tree, "numeric_columns", ()):
            return self.data(column, SORT_VALUE_ROLE) < other.data(column, SORT_VALUE_ROLE)
        return self.text(column).lower() < other.text(column).lower()


@contextmanager
def sorting_suspended(tree: QTreeWidget):
    """Turn sorting off while rows are added, so each insert does not sort the list again."""
    tree.setSortingEnabled(False)
    try:
        yield
    finally:
        # setSortingEnabled(True) sorts once by the current indicator (none until a header click).
        tree.setSortingEnabled(True)


def install_empty_placeholder(tree: QTreeWidget, text: str) -> QLabel:
    """Add a hidden, centered "no rows" label over the tree's viewport and return it."""
    # Parented to the viewport, so it shows only where no rows are painted.
    label = QLabel(text, tree.viewport())
    label.setProperty("textRole", "placeholder")
    label.setAlignment(Qt.AlignCenter)
    label.setWordWrap(True)
    layout = QVBoxLayout(tree.viewport())
    layout.addWidget(label)
    label.hide()
    return label


def sync_empty_placeholder(tree: QTreeWidget, label: QLabel) -> None:
    """Show the placeholder label only when the tree has no rows."""
    label.setVisible(tree.topLevelItemCount() == 0)


def populate_rows(tree: QTreeWidget, label: QLabel, rows, add_row: Callable) -> None:
    """Replace the tree's rows by calling add_row(row) for each row, then sync the placeholder."""
    with sorting_suspended(tree):
        tree.clear()
        for row in rows:
            add_row(row)
    sync_empty_placeholder(tree, label)
