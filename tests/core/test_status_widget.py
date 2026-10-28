"""StatusBarWidget toast sizing and accessibility."""

from PySide6.QtWidgets import QWidget

from src.core.status_widget import StatusBarWidget


def test_close_button_has_accessible_name(qapp):
    parent = QWidget()
    toast = StatusBarWidget(parent)
    assert toast.close_btn.accessibleName() == "Dismiss"
    assert toast.close_btn.toolTip() == "Dismiss"


def test_show_status_message_uses_local_toast_on_dialog(qapp):
    from PySide6.QtWidgets import QDialog

    from src.core.status_widget import show_status_message

    dialog = QDialog()
    child = QWidget(dialog)

    show_status_message(child, "Saved", 3000)

    toast = dialog._local_status_widget
    assert isinstance(toast, StatusBarWidget)
    assert toast.message_label.toolTip() in ("", "Saved")
    assert toast._full_message == "Saved"
