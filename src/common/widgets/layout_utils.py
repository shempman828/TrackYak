"""Shared helpers for Qt layout management."""

from PySide6.QtWidgets import QLayout


def clear_layout(layout: QLayout) -> None:
    """Remove every item from *layout*, recursing into nested layouts."""
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            # Hide first: deleteLater() only runs on a later event-loop pass, so the
            # stale widget would otherwise overlap whatever takes its slot next.
            widget.hide()
            widget.deleteLater()
        elif item.layout() is not None:
            clear_layout(item.layout())
