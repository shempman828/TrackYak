"""Qt-free "missing popular" and "gap fill" rankings over unmatched ChartEntry rows."""

# Billboard's peak_position and weeks_on_chart are running-to-date per row, so
# MIN(peak)/MAX(weeks) over an item's rows equals its final peak and tenure.

from dataclasses import dataclass
import datetime

from sqlalchemy import func, select

from src.db.db_tables.chart import Chart, ChartEntry

# Sort key for a missing peak, so unranked rows sort after #1 instead of before it.
MISSING_PEAK_RANK = 10_000_000

_ONE_WEEK = datetime.timedelta(days=7)


@dataclass
class MissingChartItem:
    """One unmatched (chart, title, performer) aggregated over its chart weeks."""

    chart_id: int
    chart_name: str
    entity_type: str  # 'Track' or 'Album'
    raw_title: str
    raw_performer: str
    peak_position: int | None
    weeks_on_chart: int | None
    gap_run_length: int = 0  # best (before + after owned run) this item would connect


def _popularity_key(item: MissingChartItem) -> tuple:
    """Return the sort key for best peak first, then most weeks on chart."""
    peak = item.peak_position if item.peak_position is not None else MISSING_PEAK_RANK
    return (peak, -(item.weeks_on_chart or 0))


def _apply_week_bound(stmt, week_from, week_to):
    """Add the inclusive [week_from, week_to] chart_week filter; either bound is optional."""
    if week_from is not None:
        stmt = stmt.where(ChartEntry.chart_week >= week_from)
    if week_to is not None:
        stmt = stmt.where(ChartEntry.chart_week <= week_to)
    return stmt


def chart_week_years(session) -> list[int]:
    """Return the sorted distinct calendar years of all ChartEntry chart weeks."""
    year_expr = func.extract("year", ChartEntry.chart_week)
    rows = session.execute(select(year_expr).distinct().order_by(year_expr)).all()
    return [int(year) for (year,) in rows if year is not None]


def _aggregate_missing(session, chart_ids: list | None = None, week_from: datetime.date | None = None, week_to: datetime.date | None = None) -> list:
    """Return one MissingChartItem per unmatched (chart, title, performer) with its best peak and tenure."""
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
        .group_by(ChartEntry.chart_id, Chart.chart_name, Chart.matched_entity_type, ChartEntry.raw_title, ChartEntry.raw_performer)
    )
    if chart_ids:
        stmt = stmt.where(ChartEntry.chart_id.in_(chart_ids))
    stmt = _apply_week_bound(stmt, week_from, week_to)

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


def get_missing_popular(session, chart_ids: list | None = None, limit: int = 100, week_from: datetime.date | None = None, week_to: datetime.date | None = None) -> list:
    """Return missing items ranked by best peak, then weeks on chart, within the optional week window."""
    items = _aggregate_missing(session, chart_ids, week_from, week_to)
    items.sort(key=_popularity_key)
    return items[:limit]


def _owned_run_gaps(chart_rows: list) -> list:
    """Return, per row, the owned-run length before plus after it (0 for owned rows)."""
    n = len(chart_rows)
    owned = [r.entity_id is not None for r in chart_rows]

    # A run carries into the next row only within the same week or into the very
    # next week (exactly 7 days later); a missing week breaks the run.
    connects_back = [True] * n
    for i in range(1, n):
        prev_week, week = chart_rows[i - 1].chart_week, chart_rows[i].chart_week
        connects_back[i] = week == prev_week or week - prev_week == _ONE_WEEK

    streak = [0] * n  # owned run ending at i (inclusive)
    for i in range(n):
        carry = streak[i - 1] if i > 0 and connects_back[i] else 0
        streak[i] = carry + 1 if owned[i] else 0

    streak_rev = [0] * n  # owned run starting at i (inclusive)
    for i in range(n - 1, -1, -1):
        carry = streak_rev[i + 1] if i < n - 1 and connects_back[i + 1] else 0
        streak_rev[i] = carry + 1 if owned[i] else 0

    gaps = [0] * n
    for i in range(n):
        if owned[i]:
            continue
        before_run = streak[i - 1] if i > 0 and connects_back[i] else 0
        after_run = streak_rev[i + 1] if i < n - 1 and connects_back[i + 1] else 0
        gaps[i] = before_run + after_run
    return gaps


def get_missing_gap_fills(session, chart_ids: list | None = None, min_gap: int = 4, limit: int = 100, week_from: datetime.date | None = None, week_to: datetime.date | None = None) -> list:
    """Return missing items ranked by how much owned chart run they would connect (at least min_gap)."""
    # Run lengths need every row (owned and not), read in one index-ordered pass.
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
    stmt = _apply_week_bound(stmt, week_from, week_to)

    best: dict = {}  # (chart_id, raw_title, raw_performer) -> MissingChartItem
    peaks: dict = {}  # same key -> (best peak, most weeks) over every unmatched row

    def _flush_chart(chart_rows: list) -> None:
        for row, gap in zip(chart_rows, _owned_run_gaps(chart_rows), strict=True):
            if row.entity_id is not None:
                continue
            key = (row.chart_id, row.raw_title, row.raw_performer)
            peak, weeks = peaks.get(key, (None, None))
            if row.peak_position is not None and (peak is None or row.peak_position < peak):
                peak = row.peak_position
            if row.weeks_on_chart is not None and (weeks is None or row.weeks_on_chart > weeks):
                weeks = row.weeks_on_chart
            peaks[key] = (peak, weeks)
            if gap < min_gap:
                continue
            existing = best.get(key)
            if existing is None or gap > existing.gap_run_length:
                best[key] = MissingChartItem(
                    chart_id=row.chart_id,
                    chart_name=row.chart_name,
                    entity_type=row.matched_entity_type,
                    raw_title=row.raw_title,
                    raw_performer=row.raw_performer,
                    peak_position=None,
                    weeks_on_chart=None,
                    gap_run_length=gap,
                )

    current_chart = None
    chart_rows: list = []
    for row in session.execute(stmt):
        if row.chart_id != current_chart:
            if chart_rows:
                _flush_chart(chart_rows)
            current_chart = row.chart_id
            chart_rows = []
        chart_rows.append(row)
    if chart_rows:
        _flush_chart(chart_rows)

    # Report the same final peak/tenure as Missing Popular, not the gap week's running value.
    for key, item in best.items():
        item.peak_position, item.weeks_on_chart = peaks[key]

    items = list(best.values())
    items.sort(key=lambda i: (-i.gap_run_length, *_popularity_key(i)))
    return items[:limit]
