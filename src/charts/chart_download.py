"""
chart_download.py

Source registry and CSV parsing for Billboard chart history, imported from
the community-maintained utdata/rwd-billboard-data GitHub repo rather than
scraped live from billboard.com -- live scraping has no bulk/search API, is
rate-limited, and a full historical pull takes hours; this static dataset
covers Hot 100 (1958-present) + Billboard 200 (1967-present) in two CSVs
updated Tue-Fri upstream, downloadable in seconds.
"""

from collections.abc import Iterator
import csv
from datetime import date
from pathlib import Path

from src.core.asset_paths import chart_data_path

# chart_key -> (local filename, source URL). Filenames match the upstream
# basenames so "does the file exist" is a plain Path.exists() check -- see
# src/core/asset_paths.py's chart_data_path().
CHART_FILES = {
    "hot-100": (
        "hot-100-current.csv",
        "https://raw.githubusercontent.com/utdata/rwd-billboard-data/main/data-out/hot-100-current.csv",
    ),
    "billboard-200": (
        "billboard-200-current.csv",
        "https://raw.githubusercontent.com/utdata/rwd-billboard-data/main/data-out/billboard-200-current.csv",
    ),
}


def chart_csv_exists(chart_key: str) -> bool:
    """Whether chart_key's CSV has been downloaded to CHARTS_DIR. Freshness/
    import-progress bookkeeping lives on the Chart row (last_downloaded_at/
    last_synced_week), not the filesystem -- this only answers "is there a
    file to import from at all"."""
    filename, _ = CHART_FILES[chart_key]
    return Path(chart_data_path(filename)).exists()


def _safe_int(value) -> int | None:
    """Coerce a CSV field to int, treating any non-numeric sentinel ("-",
    "NA", empty string, etc.) as missing. Real chart CSV data has been
    observed to use more than one such sentinel."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_chart_csv(path: Path) -> Iterator[dict]:
    """Yield one ChartEntry-shaped kwargs dict per CSV row (excluding
    chart_id, which the caller attaches once it knows which Chart this is).

    Source columns: chart_week,current_week,title,performer,last_week,peak_pos,wks_on_chart
    `last_week`/`peak_pos`/`wks_on_chart` use non-numeric sentinels ("-",
    "NA") for missing values -- see _safe_int().
    """
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            yield {
                "chart_week": date.fromisoformat(row["chart_week"]),
                "position": int(row["current_week"]),
                "last_week_position": _safe_int(row.get("last_week")),
                "peak_position": _safe_int(row.get("peak_pos")),
                "weeks_on_chart": _safe_int(row.get("wks_on_chart")),
                "raw_title": row["title"],
                "raw_performer": row["performer"],
            }
