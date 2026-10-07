"""Regression test for _DateRangeEdit swapping an inverted date range."""

from PySide6.QtCore import QDate

from src.playlist.smart.playlist_smart_criteria_widget import _DateRangeEdit


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


def _select_operator(widget, operator):
    combo = widget.operator_combo
    combo.setCurrentIndex(combo.findData(operator))


def test_numeric_between_uses_a_two_bound_range(qapp):
    from src.playlist.smart.playlist_smart_criteria_widget import CriteriaWidget, _NumberRangeEdit

    widget = CriteriaWidget()
    widget.set_criteria({"field": "bpm", "comparison": "range", "value": "140|90", "type": "Integer"})

    assert isinstance(widget.value_widget, _NumberRangeEdit)
    assert widget.get_criteria()["value"] == "90|140"

    _select_operator(widget, "gt")
    assert not isinstance(widget.value_widget, _NumberRangeEdit)


def test_integer_value_is_restored_from_stored_text(qapp):
    from src.playlist.smart.playlist_smart_criteria_widget import CriteriaWidget

    widget = CriteriaWidget()
    widget.set_criteria({"field": "bpm", "comparison": "gt", "value": "120", "type": "Integer"})

    assert widget.get_criteria()["value"] == 120


def test_last_criteria_row_cannot_be_deleted(qapp):
    from src.playlist.smart.playlist_smart_new import SmartPlaylistCreateDialog

    dialog = SmartPlaylistCreateDialog()
    assert not dialog.criteria_widgets[0].delete_btn.isEnabled()

    dialog.add_criteria_widget()
    assert all(w.delete_btn.isEnabled() for w in dialog.criteria_widgets)

    dialog.remove_criteria_widget(dialog.criteria_widgets[1])
    assert not dialog.criteria_widgets[0].delete_btn.isEnabled()
