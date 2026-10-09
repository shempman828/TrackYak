"""StatusBarWidget toast sizing and accessibility."""

from PySide6.QtWidgets import QWidget

from src.core.status_widget import StatusBarWidget


def test_long_message_never_makes_toast_wider_than_parent(qapp):
    parent = QWidget()
    parent.resize(400, 300)
    toast = StatusBarWidget(parent)
    message = "Importing " + "very long file name " * 30

    toast.show_message(message, 3000)

    assert toast.x() >= 0
    assert toast.width() <= parent.width()
    assert toast.message_label.toolTip() == message


def test_close_button_has_accessible_name(qapp):
    parent = QWidget()
    toast = StatusBarWidget(parent)
    assert toast.close_btn.accessibleName() == "Dismiss"
    assert toast.close_btn.toolTip() == "Dismiss"
