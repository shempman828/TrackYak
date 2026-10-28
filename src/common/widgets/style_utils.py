"""Helpers for toggling QSS dynamic properties on live widgets."""

from PySide6.QtWidgets import QWidget


def refresh_style(widget: QWidget) -> None:
    """Force Qt to re-evaluate the stylesheet for widget and its descendants."""
    # Children too: selectors keyed off an ancestor's property
    # ("Foo[selected='true'] QLabel") are not re-evaluated by the ancestor's repolish.
    for w in (widget, *widget.findChildren(QWidget)):
        w.style().unpolish(w)
        w.style().polish(w)


def set_style_property(widget, name: str, value) -> None:
    """Set a QSS-matched dynamic property on widget and refresh its style."""
    widget.setProperty(name, value)
    refresh_style(widget)
