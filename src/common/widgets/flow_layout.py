"""Wrapping flow layout and the container widgets that size themselves from it."""

from PySide6.QtCore import QPoint, QRect, QSize, Qt
from PySide6.QtWidgets import QLayout, QWidget

from src.foundation.logger_config import logger


class FlowLayout(QLayout):
    """Left-to-right layout whose rows wrap when the width runs out (like CSS flex-wrap)."""

    def __init__(self, parent=None, margin: int = 0, spacing: int = 20, h_spacing: int | None = None, v_spacing: int | None = None):
        """Build a flow layout, with optional per-axis spacing overriding *spacing*."""
        super().__init__(parent)
        self.setContentsMargins(margin, margin, margin, margin)
        self._h_space: int = h_spacing if h_spacing is not None else spacing
        self._v_space: int = v_spacing if v_spacing is not None else spacing
        self._item_list: list = []
        logger.debug(f"FlowLayout created (h_spacing={self._h_space}, v_spacing={self._v_space})")

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
        value = max(0, value)  # a negative gap would overlap items instead of spacing them
        if value != self._h_space:
            self._h_space = value
            self.invalidate()

    @property
    def v_spacing(self) -> int:
        return self._v_space

    @v_spacing.setter
    def v_spacing(self, value: int) -> None:
        value = max(0, value)  # a negative gap would overlap items instead of spacing them
        if value != self._v_space:
            self._v_space = value
            self.invalidate()

    # ------------------------------------------------------------------
    # Core layout algorithm
    # ------------------------------------------------------------------

    def _do_layout(self, rect: QRect, *, test_only: bool) -> int:
        """Arrange (or measure) items within *rect*; returns the total height required."""
        m = self.contentsMargins()
        left = rect.x() + m.left()
        top = rect.y() + m.top()
        right = rect.x() + rect.width() - m.right()

        x = left
        y = top
        row_height = 0

        for item in self._item_list:
            widget = item.widget()
            # Skip only widgets hidden on purpose. isVisible() is also False
            # before a parent is shown and while a newly added child's show is
            # still queued, and skipping those collapses heightForWidth() to the
            # margins and leaves the widget stacked on slot (0, 0).
            if widget is not None and widget.isHidden() and widget.testAttribute(Qt.WidgetAttribute.WA_WState_ExplicitShowHide):
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
                # test_only=True (from heightForWidth) only measures the
                # required height for a given width; it must not move any
                # widget, since that call can happen mid-layout-pass.
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

    def update(self) -> None:
        """Invalidate cached geometry and request a re-layout."""
        self.invalidate()
        parent = self.parentWidget()
        if parent is not None:
            parent.updateGeometry()
            parent.update()


class ChipArea(QWidget):
    """Widget whose minimumHeight tracks its FlowLayout's wrapped height at its current width."""

    # FlowLayout's sizeHint/minimumSize only cover one row; without this the
    # parent reserves a single row and wrapped rows get squashed/overlapped.

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.refresh_height()

    def refresh_height(self):
        """Recompute minimumHeight from heightForWidth(); call after adding/removing items."""
        layout = self.layout()
        if layout is None:
            return
        height = layout.heightForWidth(self.width())
        if height != self.minimumHeight():
            self.setMinimumHeight(height)


class ChipCell(QWidget):
    """Table cell that holds a FlowLayout and reports its height at its real width."""

    # QWidget.sizeHint() evaluates heightForWidth() at the layout's minimum width
    # (one chip), which makes rows far too tall; resizeRowsToContents() cannot fix that.

    def _hint(self) -> QSize:
        """Return the size hint at the cell's current width."""
        lay = self.layout()
        width = self.width()
        if lay is not None and lay.hasHeightForWidth() and width > 0:
            return QSize(width, lay.heightForWidth(width))
        return super().sizeHint()

    def sizeHint(self) -> QSize:
        return self._hint()

    def minimumSizeHint(self) -> QSize:
        return self._hint()

    def resizeEvent(self, event):
        # A stretched column changing width changes how many chip lines fit,
        # hence this cell's preferred height -- let Qt know the hint is stale.
        super().resizeEvent(event)
        self.updateGeometry()
