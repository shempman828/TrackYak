"""
chart_table_placeholder.py

install_empty_placeholder()/sync_empty_placeholder(): a small "no rows yet"
message centered over an otherwise-blank QTreeWidget, shared by
ChartEntryTable and ChartRecommendationTable so an empty result set reads
as an intentional state rather than a blank pane. Uses the app's existing
QLabel[textRole="placeholder"] convention (already used for empty-state
hints elsewhere) rather than a bespoke style.

The label is parented to the tree's viewport -- QTreeWidget paints its rows
directly onto that widget, so a plain child QLabel simply sits on top of
(and is only visible when there's nothing behind) the item area.
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QTreeWidget, QVBoxLayout


def install_empty_placeholder(tree: QTreeWidget, text: str) -> QLabel:
    label = QLabel(text, tree.viewport())
    label.setProperty("textRole", "placeholder")
    label.setAlignment(Qt.AlignCenter)
    label.setWordWrap(True)
    layout = QVBoxLayout(tree.viewport())
    layout.addWidget(label)
    label.hide()
    return label


def sync_empty_placeholder(tree: QTreeWidget, label: QLabel) -> None:
    label.setVisible(tree.topLevelItemCount() == 0)
