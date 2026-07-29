"""Shared helpers for Qt layout management."""

from PySide6.QtWidgets import QLayout


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
