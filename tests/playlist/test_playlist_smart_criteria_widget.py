"""Regression test for _DateRangeEdit swapping an inverted date range."""

from PySide6.QtCore import QDate

from src.playlist.playlist_smart_criteria_widget import _DateRangeEdit


def test_inverted_range_is_swapped(qapp):
    widget = _DateRangeEdit()
    widget.start_edit.setDate(QDate(2026, 6, 1))
    widget.end_edit.setDate(QDate(2026, 1, 1))

    value = widget.get_value()

    assert value == "2026-01-01 00:00:00|2026-06-01 23:59:59"


def test_normal_range_is_unchanged(qapp):
    widget = _DateRangeEdit()
    widget.start_edit.setDate(QDate(2026, 1, 1))
    widget.end_edit.setDate(QDate(2026, 6, 1))

    value = widget.get_value()

    assert value == "2026-01-01 00:00:00|2026-06-01 23:59:59"
