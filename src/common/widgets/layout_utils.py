"""Shared helpers for Qt layout management."""

from PySide6.QtWidgets import QLayout, QWidget


class FlowLayoutContainer(QWidget):
    """Widget whose minimumHeight tracks its FlowLayout's wrapped height.

    FlowLayout.sizeHint()/minimumSize() can only account for a single row --
    a flow layout's real height depends on how many rows it wraps into at a
    given width, which isn't knowable until it's actually given that width.
    Left alone, the enclosing layout reserves space for just one row, so
    once content wraps onto a second or third row (e.g. from a larger UI
    font), it gets squished/overlapped instead of this container growing to
    fit it. Recomputing minimumHeight from heightForWidth() on every resize
    keeps the reserved space accurate.
    """

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.refresh_height()

    def refresh_height(self):
        layout = self.layout()
        if layout is None:
            return
        height = layout.heightForWidth(self.width())
        if height != self.minimumHeight():
            self.setMinimumHeight(height)


def clear_layout(layout: QLayout) -> None:
    """Remove every item from *layout*, recursing into nested layouts.

    Widgets are hidden before ``deleteLater()`` is called. ``deleteLater()``
    only destroys the widget on a future event-loop pass, so without hiding
    it first, a stale widget stays visible at its old geometry and can
    overlap whatever gets placed in that same slot immediately after this
    call returns.
    """
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.hide()
            widget.deleteLater()
        elif item.layout() is not None:
            clear_layout(item.layout())
