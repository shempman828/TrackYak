"""
Tests for get_missing_gap_fills (src/charts/chart_recommendations.py) --
specifically that an owned run's length carries across a chart_week
boundary instead of resetting to 0 at the edge of each week.
"""

import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.charts.chart_recommendations import get_missing_gap_fills
from src.db.db_tables.base import Base
from src.db.db_tables.chart import Chart, ChartEntry


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def _make_chart(session):
    chart = Chart(chart_key="hot-100", chart_name="Billboard Hot 100", source_url="https://example.invalid/hot-100.csv", matched_entity_type="Track")
    session.add(chart)
    session.commit()
    return chart


def _add_entry(session, chart, week, position, *, owned, title, weeks_on_chart=1):
    session.add(
        ChartEntry(
            chart_id=chart.chart_id,
            chart_week=week,
            position=position,
            peak_position=position,
            weeks_on_chart=weeks_on_chart,
            raw_title=title,
            raw_performer="Someone",
            entity_type="Track" if owned else None,
            entity_id=(2000 + position) if owned else None,
            match_score=1.0 if owned else None,
        )
    )


def test_owned_run_connects_across_a_week_boundary(session):
    # Week A (earlier): positions 1-3 owned -- a trailing run of 3.
    # Week B (the next week): position 1 unmatched, positions 2-3 owned --
    # a leading run of 2. The miss sits at the very edge of its own week on
    # both sides, so it should connect the week A tail run (3) with the
    # week B head run (2) into a gap_run_length of 5, not treat either edge
    # as having nothing owned beside it.
    chart = _make_chart(session)
    week_a = datetime.date(2023, 12, 16)
    week_b = datetime.date(2023, 12, 23)

    for pos in (1, 2, 3):
        _add_entry(session, chart, week_a, pos, owned=True, title=f"A Owned {pos}")

    _add_entry(session, chart, week_b, 1, owned=False, title="Gap Song")
    for pos in (2, 3):
        _add_entry(session, chart, week_b, pos, owned=True, title=f"B Owned {pos}")

    session.commit()

    items = get_missing_gap_fills(session, min_gap=5)

    assert len(items) == 1
    assert items[0].raw_title == "Gap Song"
    assert items[0].gap_run_length == 5


def test_gap_at_week_edge_ignores_unrelated_chart(session):
    # The neighboring rows in sort order belong to a different chart_id --
    # the run must not connect across charts, only across weeks of the
    # *same* chart.
    chart = _make_chart(session)
    other_chart = Chart(chart_key="billboard-200", chart_name="Billboard 200", source_url="https://example.invalid/billboard-200.csv", matched_entity_type="Track")
    session.add(other_chart)
    session.commit()

    week = datetime.date(2023, 12, 16)
    for pos in (1, 2, 3):
        _add_entry(session, other_chart, week, pos, owned=True, title=f"Other {pos}")

    _add_entry(session, chart, week, 1, owned=False, title="Gap Song")
    for pos in (2, 3):
        _add_entry(session, chart, week, pos, owned=True, title=f"Owned {pos}")

    session.commit()

    items = get_missing_gap_fills(session, min_gap=1)

    assert len(items) == 1
    assert items[0].raw_title == "Gap Song"
    assert items[0].gap_run_length == 2  # only the same-chart run after it


def test_run_does_not_cross_a_missing_week(session):
    # Week A and week C are both present, but week B (in between, 7 days
    # after A and 7 days before C) is missing from the data entirely --
    # a real gap in the chart's history, not just a filtered-out range.
    # The week A tail run and week C head run sit right next to each other
    # in row order once week B's rows are absent, but they must NOT be
    # treated as touching, since a whole week of unknown chart state sits
    # between them.
    chart = _make_chart(session)
    week_a = datetime.date(2023, 12, 16)
    week_c = datetime.date(2023, 12, 30)  # 14 days after week_a, not 7

    for pos in (1, 2, 3):
        _add_entry(session, chart, week_a, pos, owned=True, title=f"A Owned {pos}")

    _add_entry(session, chart, week_c, 1, owned=False, title="Gap Song")
    for pos in (2, 3):
        _add_entry(session, chart, week_c, pos, owned=True, title=f"C Owned {pos}")

    session.commit()

    items = get_missing_gap_fills(session, min_gap=1)

    assert len(items) == 1
    assert items[0].raw_title == "Gap Song"
    assert items[0].gap_run_length == 2  # only week C's own head run, not week A's tail too
