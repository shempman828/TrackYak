"""Shared manual-match and clear-match handlers for the chart tables' context menus."""

from collections.abc import Callable

from PySide6.QtWidgets import QDialog, QMessageBox, QWidget

from src.charts.chart_recommendations import MissingChartItem
from src.charts.ui.chart_manual_match_dialog import ChartManualMatchDialog
from src.core.status_widget import show_status_message


def _notify(parent: QWidget | None, message: str) -> None:
    """Show a status toast for parent, if there is one."""
    if parent is not None:
        show_status_message(parent, message)


def handle_manual_match_requested(parent: QWidget, controller, chart_entry_id: int, on_done: Callable[[], None]) -> None:
    """Let the user pick a match for one chart entry and save it."""
    entry = controller.get.get_entity_object("ChartEntry", chart_entry_id=chart_entry_id)
    if entry is None:
        return
    dialog = ChartManualMatchDialog(controller, entry.chart.matched_entity_type, entry.raw_title, entry.raw_performer, parent)
    if dialog.exec() == QDialog.Accepted:
        # Not update_entity(): its `entity_id` parameter collides with ChartEntry's entity_id column.
        controller.update.update_entity_by_filter("ChartEntry", {"chart_entry_id": chart_entry_id}, entity_type=dialog.matched_entity_type(), entity_id=dialog.matched_entity_id(), match_score=1.0)
        _notify(parent, f'Matched "{entry.raw_title}"')
        on_done()


def handle_bulk_manual_match_requested(parent: QWidget, controller, item: MissingChartItem, on_done: Callable[[], None]) -> None:
    """Match every unmatched entry that shares item's chart, title and performer at once."""
    # Matching only one row would leave the item's other weeks unmatched and listed.
    dialog = ChartManualMatchDialog(controller, item.entity_type, item.raw_title, item.raw_performer, parent)
    if dialog.exec() == QDialog.Accepted:
        controller.update.update_entity_by_filter(
            "ChartEntry",
            {"chart_id": item.chart_id, "raw_title": item.raw_title, "raw_performer": item.raw_performer, "entity_id": None},
            entity_type=dialog.matched_entity_type(),
            entity_id=dialog.matched_entity_id(),
            match_score=1.0,
        )
        _notify(parent, f'Matched "{item.raw_title}"')
        on_done()


def handle_clear_match_requested(parent: QWidget, controller, chart_entry_id: int, on_done: Callable[[], None]) -> None:
    """Ask for confirmation, then clear one chart entry's match."""
    reply = QMessageBox.question(
        parent, "Clear Match", "Clear this entry's match? It will become eligible for auto-matching again on the next Match Now run.", QMessageBox.Yes | QMessageBox.No, QMessageBox.No
    )
    if reply != QMessageBox.Yes:
        return
    controller.update.update_entity_by_filter("ChartEntry", {"chart_entry_id": chart_entry_id}, entity_type=None, entity_id=None, match_score=None, last_match_attempt_at=None)
    _notify(parent, "Match cleared")
    on_done()
