"""
chart_recommendations.py

Recommendation queries on top of chart matching, run entirely off
ChartEntry.entity_id (whether a row is matched) -- no Qt dependency,
mirrors chart_matching.py's separation of pure query logic from its UI
(see chart_recommendations_tab.py / chart_recommendation_table.py).

Two rankings:
- get_missing_popular: entries with no library match, ranked by chart
  performance (best peak position, most weeks on chart).
- get_missing_gap_fills: entries with no library match that sit between
  two runs of already-owned positions on the same chart_week -- e.g. you
  own #1-15 and #17-23 of a week's chart, #16 is the gap. Ranked by how
  much owned run length filling the gap would connect (15 + 7 = 22 for
  that example), since a missing song bordered by songs you already have
  is a much stronger "you're basically done with this week" signal than
  an isolated miss with nothing owned on either side.

A single (raw_title, raw_performer) pair can appear as many ChartEntry rows
as the song/album had weeks on the chart, so both rankings group results
per chart. Both Billboard fields are already running-to-date per row
(peak_position only ever improves week over week, weeks_on_chart only ever
increments), so MIN(peak_position)/MAX(weeks_on_chart) across a group
equals the item's final peak/tenure without needing to special-case the
latest week's row.
"""

from dataclasses import dataclass
from typing import Optional

from sqlalchemy import func, select

from src.db.db_tables.chart import Chart, ChartEntry


@dataclass
class MissingChartItem:
    chart_id: int
    chart_name: str
    entity_type: str  # 'Track' or 'Album'
    raw_title: str
    raw_performer: str
    peak_position: Optional[int]
    weeks_on_chart: Optional[int]
    gap_run_length: int = 0  # best (before + after owned run) this item would connect


def _popularity_key(item: MissingChartItem) -> tuple:
    # Missing peak_position sorts last rather than first (None < int in
    # naive sorts would put unranked rows ahead of #1 hits).
    peak = item.peak_position if item.peak_position is not None else 10_000_000
    weeks = item.weeks_on_chart or 0
    return (peak, -weeks)


def _aggregate_missing(session, chart_ids: Optional[list] = None) -> list:
    stmt = (
        select(
            ChartEntry.chart_id,
            Chart.chart_name,
            Chart.matched_entity_type,
            ChartEntry.raw_title,
            ChartEntry.raw_performer,
            func.min(ChartEntry.peak_position).label("peak_position"),
            func.max(ChartEntry.weeks_on_chart).label("weeks_on_chart"),
        )
        .join(Chart, Chart.chart_id == ChartEntry.chart_id)
        .where(ChartEntry.entity_id.is_(None))
        .group_by(
            ChartEntry.chart_id,
            Chart.chart_name,
            Chart.matched_entity_type,
            ChartEntry.raw_title,
            ChartEntry.raw_performer,
        )
    )
    if chart_ids:
        stmt = stmt.where(ChartEntry.chart_id.in_(chart_ids))

    return [
        MissingChartItem(
            chart_id=row.chart_id,
            chart_name=row.chart_name,
            entity_type=row.matched_entity_type,
            raw_title=row.raw_title,
            raw_performer=row.raw_performer,
            peak_position=row.peak_position,
            weeks_on_chart=row.weeks_on_chart,
        )
        for row in session.execute(stmt).all()
    ]


def get_missing_popular(
    session, chart_ids: Optional[list] = None, limit: int = 100
) -> list:
    """Missing entries ranked by chart performance alone (best peak
    position, then most weeks on chart as a tiebreaker)."""
    items = _aggregate_missing(session, chart_ids)
    items.sort(key=_popularity_key)
    return items[:limit]


def get_missing_gap_fills(
    session, chart_ids: Optional[list] = None, min_gap: int = 4, limit: int = 100
) -> list:
    """Missing entries that would connect two runs of already-owned chart
    positions in the same week -- see module docstring. `min_gap` is the
    minimum combined owned-run length (before + after) required to
    surface a candidate, so an isolated miss with nothing owned on either
    side doesn't show up as noise.

    Reads every row of the selected chart(s) (not just unmatched ones) --
    computing a run length needs the full owned/unowned sequence for each
    chart_week, not just the missing rows -- but does it as one
    index-ordered pass (uq_chart_entry_week_position already covers
    chart_id, chart_week, position) rather than N+1 per-week queries.
    """
    stmt = (
        select(
            ChartEntry.chart_id,
            Chart.chart_name,
            Chart.matched_entity_type,
            ChartEntry.chart_week,
            ChartEntry.entity_id,
            ChartEntry.raw_title,
            ChartEntry.raw_performer,
            ChartEntry.peak_position,
            ChartEntry.weeks_on_chart,
        )
        .join(Chart, Chart.chart_id == ChartEntry.chart_id)
        .order_by(ChartEntry.chart_id, ChartEntry.chart_week, ChartEntry.position)
    )
    if chart_ids:
        stmt = stmt.where(ChartEntry.chart_id.in_(chart_ids))

    rows = session.execute(stmt).all()

    best: dict = {}  # (chart_id, raw_title, raw_performer) -> MissingChartItem

    def _flush_week(week_rows: list) -> None:
        n = len(week_rows)
        owned = [r.entity_id is not None for r in week_rows]

        streak = [0] * n  # length of owned run ending at i (inclusive)
        for i in range(n):
            streak[i] = (streak[i - 1] if i > 0 else 0) + 1 if owned[i] else 0

        streak_rev = [0] * n  # length of owned run starting at i (inclusive)
        for i in range(n - 1, -1, -1):
            streak_rev[i] = (streak_rev[i + 1] if i < n - 1 else 0) + 1 if owned[i] else 0

        for i, row in enumerate(week_rows):
            if owned[i]:
                continue
            before_run = streak[i - 1] if i > 0 else 0
            after_run = streak_rev[i + 1] if i < n - 1 else 0
            gap = before_run + after_run
            if gap < min_gap:
                continue
            key = (row.chart_id, row.raw_title, row.raw_performer)
            existing = best.get(key)
            if existing is None or gap > existing.gap_run_length:
                best[key] = MissingChartItem(
                    chart_id=row.chart_id,
                    chart_name=row.chart_name,
                    entity_type=row.matched_entity_type,
                    raw_title=row.raw_title,
                    raw_performer=row.raw_performer,
                    peak_position=row.peak_position,
                    weeks_on_chart=row.weeks_on_chart,
                    gap_run_length=gap,
                )

    week_key = None
    week_rows: list = []
    for row in rows:
        key = (row.chart_id, row.chart_week)
        if key != week_key:
            if week_rows:
                _flush_week(week_rows)
            week_key = key
            week_rows = []
        week_rows.append(row)
    if week_rows:
        _flush_week(week_rows)

    items = list(best.values())
    items.sort(key=lambda i: (-i.gap_run_length,) + _popularity_key(i))
    return items[:limit]
