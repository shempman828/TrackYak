"""Regression coverage for CalendarWidget edge-case handling."""

from datetime import date
import logging

from src.dates.dates_calendar import CalendarWidget


def test_set_year_clamps_above_max_representable_year(qapp):
    widget = CalendarWidget(year=date.max.year + 500, events_data=[])
    assert widget.year == date.max.year


def test_set_year_clamps_below_min_representable_year(qapp):
    widget = CalendarWidget(year=date.min.year - 500, events_data=[])
    assert widget.year == date.min.year


def test_organize_events_by_date_drops_event_with_out_of_range_day(qapp, caplog):
    events = [
        {"year": 2020, "month": 2, "day": 15, "type": "album_release", "entity_name": "Valid"},
        {"year": 2020, "month": 2, "day": 32, "type": "album_release", "entity_name": "Invalid"},
    ]

    with caplog.at_level(logging.WARNING, logger="musiclib"):
        widget = CalendarWidget(year=2020, events_data=events)

    assert widget.events_by_date == {(2, 15): [events[0]]}
    assert "Invalid" in caplog.text
