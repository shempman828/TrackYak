"""Shared "delete from DB only, or also delete the file(s) from disk" prompt."""

from typing import Literal

from PySide6.QtWidgets import QMessageBox

DeleteChoice = Literal["db_only", "db_and_file"]


def confirm_delete_with_file_option(
    parent, title: str, message: str, informative_text: str | None = ("Remove from Library deletes the DB entry only.\nDelete File(s) Too also removes the audio file(s) from disk.")
) -> DeleteChoice | None:
    """Show a 3-way delete prompt; returns "db_only", "db_and_file", or None if cancelled."""
    msg = QMessageBox(parent)
    msg.setWindowTitle(title)
    msg.setText(message)
    if informative_text:
        msg.setInformativeText(informative_text)
    msg.setIcon(QMessageBox.Warning)
    btn_db_only = msg.addButton("Remove from Library", QMessageBox.AcceptRole)
    btn_and_file = msg.addButton("Delete File(s) Too", QMessageBox.DestructiveRole)
    btn_cancel = msg.addButton("Cancel", QMessageBox.RejectRole)
    msg.setDefaultButton(btn_db_only)
    msg.exec_()

    clicked = msg.clickedButton()
    if clicked is None or clicked is btn_cancel:
        return None
    return "db_and_file" if clicked is btn_and_file else "db_only"
