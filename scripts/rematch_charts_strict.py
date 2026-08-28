"""One-off: re-run chart Track matching under the stricter matcher.

Clears every Track-type ChartEntry match with match_score < 1.0 (the loose
old-fuzzy-matcher grabs; byte-exact 1.0 matches are kept), resets each
Track chart's last_library_fingerprint so every unmatched entry is
rescored, then runs match_chart for each Track chart.

Run once, from the repo root, with the app CLOSED (matching takes a write
lock) and a fresh backup of music_library.db in hand:

    python scripts/rematch_charts_strict.py

Verified on a scratch copy: ~40.6k sub-1.0 matches cleared, ~16.7k
re-matched under the strict rules; the ~24k that don't re-match were
overwhelmingly old-matcher mistakes (it scored them <0.8 itself) -- link
any real survivors by hand from the Charts view.
"""

import sys

from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import sessionmaker

from src.charts.chart_matching import match_chart
from src.db.db_tables.chart import Chart, ChartEntry

DB_PATH = "music_library.db"


def main() -> int:
    session = sessionmaker(bind=create_engine(f"sqlite:///{DB_PATH}"))()
    try:
        total = session.scalar(select(func.count()).select_from(ChartEntry))
        matched = select(func.count()).select_from(ChartEntry).where(
            ChartEntry.entity_id.isnot(None)
        )
        print(f"before: {session.scalar(matched)}/{total} entries matched")

        cleared = session.execute(
            text(
                "UPDATE chart_entries SET entity_type=NULL, entity_id=NULL, "
                "match_score=NULL, last_match_attempt_at=NULL "
                "WHERE entity_type='Track' AND match_score < 1.0"
            )
        ).rowcount
        session.execute(
            text(
                "UPDATE charts SET last_library_fingerprint=NULL "
                "WHERE matched_entity_type='Track'"
            )
        )
        session.commit()
        print(f"cleared {cleared} sub-1.0 Track matches")

        for chart in session.scalars(
            select(Chart).where(Chart.matched_entity_type == "Track")
        ):
            stats = match_chart(
                session,
                chart,
                stage_callback=lambda m, k=chart.chart_key: print(f"  [{k}] {m}"),
            )
            print(
                f"  [{chart.chart_key}] re-matched {stats.matched} / "
                f"{stats.total_unmatched} unmatched"
            )

        print(f"after: {session.scalar(matched)}/{total} entries matched")
        return 0
    finally:
        session.close()


if __name__ == "__main__":
    sys.exit(main())
