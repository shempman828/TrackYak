"""Helpers for toggling QSS dynamic properties on live widgets.

Widgets should prefer setting a property that a selector in
themes/dark_mode.qss matches (e.g. widget.setProperty("active", True))
over calling setStyleSheet() directly. Qt caches style evaluation, so
any property change made after a widget has been shown must be followed
by an unpolish/polish cycle for the new selector to take effect. Child
widgets are repolished too, since QSS selectors that key off an
ancestor's property (e.g. "Foo[selected='true'] QLabel") don't get
re-evaluated just by repolishing the ancestor itself.
"""

from PySide6.QtWidgets import QWidget


def refresh_style(widget: QWidget) -> None:
    """Force Qt to re-evaluate the stylesheet for widget and its descendants."""
    for w in (widget, *widget.findChildren(QWidget)):
        w.style().unpolish(w)
        w.style().polish(w)


def set_style_property(widget, name: str, value) -> None:
    """Set a QSS-matched dynamic property on widget and refresh its style."""
    widget.setProperty(name, value)
    refresh_style(widget)
