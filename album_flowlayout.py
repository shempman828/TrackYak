"""Flow layout for the album grid view.

Arranges child widgets in left-to-right rows that wrap onto the next line
when the available width is exhausted, similar to CSS flexbox wrap.
"""

from PySide6.QtCore import QPoint, QRect, QSize, Qt
from PySide6.QtWidgets import QLayout


class FlowLayout(QLayout):
    """Custom wrapping flow layout.

    Items are placed left-to-right; when a new item would exceed the available
    width the row wraps.  Both horizontal and vertical spacing are configurable
    independently either at construction time or via the property setters.
    """

    def __init__(
        self,
        parent=None,
        margin: int = 0,
        spacing: int = 20,
        h_spacing: int | None = None,
        v_spacing: int | None = None,
    ):
        """
        Args:
            parent:     Parent widget.
            margin:     Uniform content margin applied to all four sides.
            spacing:    Default spacing used for both axes when the axis-specific
                        value is not supplied.
            h_spacing:  Horizontal gap between items (overrides *spacing*).
            v_spacing:  Vertical gap between rows (overrides *spacing*).
        """
        super().__init__(parent)
        self.setContentsMargins(margin, margin, margin, margin)
        self._h_space: int = h_spacing if h_spacing is not None else spacing
        self._v_space: int = v_spacing if v_spacing is not None else spacing
        self._item_list: list = []

    # ------------------------------------------------------------------
    # QLayout required interface
    # ------------------------------------------------------------------

    def addItem(self, item) -> None:
        self._item_list.append(item)
        self.invalidate()

    def count(self) -> int:
        return len(self._item_list)

    def itemAt(self, index: int):
        if 0 <= index < len(self._item_list):
            return self._item_list[index]
        return None

    def takeAt(self, index: int):
        if 0 <= index < len(self._item_list):
            item = self._item_list.pop(index)
            self.invalidate()
            return item
        return None

    def expandingDirections(self) -> Qt.Orientations:
        # Does not expand in either direction on its own
        return Qt.Orientations(Qt.Orientation(0))

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._do_layout(QRect(0, 0, width, 0), test_only=True)

    def setGeometry(self, rect: QRect) -> None:
        super().setGeometry(rect)
        self._do_layout(rect, test_only=False)

    def sizeHint(self) -> QSize:
        return self.minimumSize()

    def minimumSize(self) -> QSize:
        """Minimum size is large enough to hold the widest / tallest single item."""
        size = QSize()
        for item in self._item_list:
            size = size.expandedTo(item.minimumSize())
        m = self.contentsMargins()
        return size + QSize(m.left() + m.right(), m.top() + m.bottom())

    # ------------------------------------------------------------------
    # Spacing properties
    # ------------------------------------------------------------------

    @property
    def h_spacing(self) -> int:
        return self._h_space

    @h_spacing.setter
    def h_spacing(self, value: int) -> None:
        if value != self._h_space:
            self._h_space = value
            self.invalidate()

    @property
    def v_spacing(self) -> int:
        return self._v_space

    @v_spacing.setter
    def v_spacing(self, value: int) -> None:
        if value != self._v_space:
            self._v_space = value
            self.invalidate()

    def set_spacing(self, h: int, v: int | None = None) -> None:
        """Set horizontal and (optionally) vertical spacing in one call."""
        self._h_space = h
        self._v_space = v if v is not None else h
        self.invalidate()

    # ------------------------------------------------------------------
    # Core layout algorithm
    # ------------------------------------------------------------------

    def _do_layout(self, rect: QRect, *, test_only: bool) -> int:
        """Arrange (or measure) items within *rect*.

        Args:
            rect:      Available rectangle.  Only ``rect.width()`` is used
                       when *test_only* is True.
            test_only: When True, positions are not applied — only the total
                       required height is calculated and returned.

        Returns:
            Total height required to lay out all visible items.
        """
        m = self.contentsMargins()
        left = rect.x() + m.left()
        top = rect.y() + m.top()
        right = rect.x() + rect.width() - m.right()

        x = left
        y = top
        row_height = 0

        for item in self._item_list:
            widget = item.widget()
            if widget is not None and not widget.isVisible():
                continue

            hint = item.sizeHint()
            item_w = hint.width()
            item_h = hint.height()

            # Wrap to a new row when the item would overflow — but only if
            # something has already been placed on the current row.
            if x + item_w > right and row_height > 0:
                x = left
                y += row_height + self._v_space
                row_height = 0

            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), hint))

            x += item_w + self._h_space
            if item_h > row_height:
                row_height = item_h

        total_height = (y + row_height - rect.y()) + m.bottom()
        # Ensure we never report zero height (avoids scroll-area collapsing)
        return max(total_height, m.top() + m.bottom())

    # ------------------------------------------------------------------
    # Convenience / back-compat
    # ------------------------------------------------------------------

    def update(self) -> None:  # noqa: A003
        """Invalidate cached geometry and request a re-layout."""
        self.invalidate()
        parent = self.parentWidget()
        if parent is not None:
            parent.updateGeometry()
            parent.update()

    # Keep old attribute name readable for any code that pokes at internals
    @property
    def item_list(self) -> list:
        return self._item_list
