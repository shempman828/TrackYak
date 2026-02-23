from PySide6.QtCore import QPoint, QRect, QSize, Qt
from PySide6.QtWidgets import (
    QLayout,
)


class FlowLayout(QLayout):
    """
    Custom flow layout that arranges child widgets in a wrapping layout.
    It calculates its preferred size by overriding sizeHint and minimumSize.
    """

    def __init__(self, parent=None, margin=0, spacing=20):
        """
        Initialize the FlowLayout.

        Args:
            parent: Parent widget.
            margin: Layout margin.
            spacing: Spacing between items (both horizontal and vertical).
        """
        super().__init__(parent)
        self.setContentsMargins(margin, margin, margin, margin)
        self._h_space = spacing
        self._v_space = spacing
        self.item_list = []

    def addItem(self, item):
        """Add an item to the layout."""
        self.item_list.append(item)
        self.invalidate()

    def count(self):
        """Return the number of items in the layout."""
        return len(self.item_list)

    def itemAt(self, index):
        """Return the item at the given index."""
        if 0 <= index < len(self.item_list):
            return self.item_list[index]
        return None

    def takeAt(self, index):
        """Remove and return the item at the given index."""
        if 0 <= index < len(self.item_list):
            item = self.item_list.pop(index)
            self.invalidate()  # Mark layout as needing recalculation
            return item
        return None

    def expandingDirections(self):
        """Return the expanding directions."""
        return Qt.Orientations(Qt.Orientation(0))

    def hasHeightForWidth(self):
        """Indicate that the layout's height depends on its width."""
        return True

    def heightForWidth(self, width):
        """Calculate height based on the given width."""
        return self._do_layout(QRect(0, 0, width, 0), test_only=True)

    def setGeometry(self, rect):
        """Set the geometry of the layout and arrange items."""
        super().setGeometry(rect)
        self._do_layout(rect, test_only=False)

    def sizeHint(self):
        """Return the preferred size of the layout."""
        return self.minimumSize()

    def minimumSize(self):
        """Return the minimum size needed by the layout."""
        size = QSize()
        for item in self.item_list:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        size += QSize(
            margins.left() + margins.right(), margins.top() + margins.bottom()
        )
        return size

    def _do_layout(self, rect, test_only):
        """
        Arrange items within the given rectangle.

        Args:
            rect: The rectangle available for layout.
            test_only: If True, only calculate the required height without setting positions.

        Returns:
            The total height required by the layout.
        """
        x = rect.x()
        y = rect.y()
        line_height = 0
        margins = self.contentsMargins()
        rect.width() - margins.left() - margins.right()

        for item in self.item_list:
            widget = item.widget()
            if widget is None or not widget.isVisible():
                continue

            space_x = self._h_space
            space_y = self._v_space
            item_width = item.sizeHint().width()

            # If the widget does not fit in the current row, move to the next row.
            if x + item_width > rect.right() - margins.right() and line_height > 0:
                x = rect.x() + margins.left()
                y += line_height + space_y
                line_height = 0

            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), item.sizeHint()))

            x += item_width + space_x
            line_height = max(line_height, item.sizeHint().height())

        total_height = y + line_height - rect.y() + margins.bottom()
        return total_height

    def update(self):
        """Force a complete layout update."""
        self.invalidate()
        if self.parentWidget():
            self.parentWidget().updateGeometry()
        super().update()
