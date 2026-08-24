"""
chart_manual_match_actions.py

Shared handlers for ChartEntryTable's manual_match_requested/
clear_match_requested signals. ChartWeekBrowserTab and ChartSearchTab wire
these identically (same shared table, same DB operations) -- factored out
here, following the same "only lives in one place" reasoning as
chart_entry_table.py itself, rather than duplicated per tab.
"""

from typing import Callable

from PySide6.QtWidgets import QDialog, QMessageBox, QWidget

from src.charts.chart_manual_match_dialog import ChartManualMatchDialog


def handle_manual_match_requested(
    parent: QWidget, controller, chart_entry_id: int, on_done: Callable[[], None]
) -> None:
    entry = controller.get.get_entity_object(
        "ChartEntry", chart_entry_id=chart_entry_id
    )
    if entry is None:
        return
    dialog = ChartManualMatchDialog(controller, entry, parent)
    if dialog.exec() == QDialog.Accepted:
        # update_entity_by_filter, not update_entity: update_entity's own
        # signature names its row-identifying parameter `entity_id`, which
        # collides with ChartEntry's `entity_id` *column* -- passing both
        # (the PK to update and the entity_id value to set) raises "got
        # multiple values for argument 'entity_id'".
        controller.update.update_entity_by_filter(
            "ChartEntry",
            {"chart_entry_id": chart_entry_id},
            entity_type=dialog.matched_entity_type(),
            entity_id=dialog.matched_entity_id(),
            match_score=1.0,
        )
        on_done()


def handle_clear_match_requested(
    parent: QWidget, controller, chart_entry_id: int, on_done: Callable[[], None]
) -> None:
    reply = QMessageBox.question(
        parent,
        "Clear Match",
        "Clear this entry's match? It will become eligible for "
        "auto-matching again on the next Match Now run.",
        QMessageBox.Yes | QMessageBox.No,
        QMessageBox.No,
    )
    if reply != QMessageBox.Yes:
        return
    controller.update.update_entity_by_filter(
        "ChartEntry",
        {"chart_entry_id": chart_entry_id},
        entity_type=None,
        entity_id=None,
        match_score=None,
        last_match_attempt_at=None,
    )
    on_done()
