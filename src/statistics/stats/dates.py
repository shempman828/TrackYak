"""
stats/dates.py

DateStats: birthdate/deathdate/album-release-date mode statistics and album
release-year distribution. These are single-table GROUP BY queries backed by
the idx_artists_begin_month_day / idx_artists_end_month_day /
idx_albums_release_month_day indexes added in Phase 1, so unlike most of
AudioStats they're cheap enough to fold into
MusicStatistics.get_comprehensive_statistics() rather than needing their own
worker. Most-complete chart year (Phase 3) is a two-table join but still a
single GROUP BY over a bounded chart-entries table, so it stays here too.
"""

from calendar import month_name

from sqlalchemy import case, func

from src.db.db_tables import Album, Artist, Chart, ChartEntry

# A year needs at least this many chart entries (of a given matched entity
# type) before its match-completeness is considered meaningful -- a year
# with 3 entries that all happened to match would otherwise look "perfect".
CHART_YEAR_MIN_ENTRIES = 50


def _mode_month_day(session, month_col, day_col):
    """Most common (month, day) pair for a date-like column pair, or None if
    no rows have both fields set."""
    row = (
        session.query(month_col, day_col, func.count().label("n"))
        .filter(month_col.isnot(None), day_col.isnot(None))
        .group_by(month_col, day_col)
        .order_by(func.count().desc())
        .first()
    )
    if row is None:
        return None
    month, day, n = row
    return {"month": month, "day": day, "count": n, "label": f"{month_name[month]} {day}"}


class DateStats:
    def __init__(self, session_factory):
        self.session_factory = session_factory

    def get_comprehensive_date_stats(self):
        session = self.session_factory()
        try:
            return {
                "most_common_birthdate": _mode_month_day(
                    session, Artist.begin_month, Artist.begin_day
                ),
                "most_common_deathdate": _mode_month_day(
                    session, Artist.end_month, Artist.end_day
                ),
                "most_common_album_release_date": _mode_month_day(
                    session, Album.release_month, Album.release_day
                ),
                "album_release_year_distribution": self._album_release_year_distribution(
                    session
                ),
                "most_complete_chart_year": self._most_complete_chart_year(session),
            }
        finally:
            session.close()

    def _album_release_year_distribution(self, session):
        rows = (
            session.query(Album.release_year, func.count(Album.album_id))
            .filter(Album.release_year.isnot(None))
            .group_by(Album.release_year)
            .order_by(Album.release_year)
            .all()
        )
        return {year: count for year, count in rows}

    def _most_complete_chart_year(self, session):
        """The calendar year with the highest fraction of matched chart
        entries, computed separately for Track-charts and Album-charts
        (Chart.matched_entity_type determines which one a chart's entries
        resolve to)."""
        year_expr = func.extract("year", ChartEntry.chart_week)
        rows = (
            session.query(
                Chart.matched_entity_type,
                year_expr.label("year"),
                func.count(ChartEntry.chart_entry_id).label("total"),
                func.sum(
                    case((ChartEntry.entity_id.isnot(None), 1), else_=0)
                ).label("matched"),
            )
            .join(Chart, ChartEntry.chart_id == Chart.chart_id)
            .group_by(Chart.matched_entity_type, "year")
            .all()
        )

        best = {}
        for entity_type, year, total, matched in rows:
            if year is None or total < CHART_YEAR_MIN_ENTRIES:
                continue
            completeness = (matched or 0) / total
            current = best.get(entity_type)
            if current is None or completeness > current["completeness"]:
                best[entity_type] = {
                    "year": int(year),
                    "completeness": round(completeness * 100, 1),
                    "matched": matched or 0,
                    "total": total,
                }
        return best
