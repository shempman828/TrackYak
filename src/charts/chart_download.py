"""Source registry and CSV parsing for the Billboard chart history files."""

from collections.abc import Iterator
import csv
from datetime import date
from pathlib import Path

from src.foundation.asset_paths import chart_data_path
from src.foundation.logger_config import logger

# chart_key -> (local filename, source URL). Static CSVs from utdata/rwd-billboard-data,
# not a live billboard.com scrape (no bulk API, rate-limited, hours for a full pull).
# Local filenames match the upstream basenames.
CHART_FILES = {
    "hot-100": ("hot-100-current.csv", "https://raw.githubusercontent.com/utdata/rwd-billboard-data/main/data-out/hot-100-current.csv"),
    "billboard-200": ("billboard-200-current.csv", "https://raw.githubusercontent.com/utdata/rwd-billboard-data/main/data-out/billboard-200-current.csv"),
}


def chart_csv_exists(chart_key: str) -> bool:
    """Return whether chart_key's CSV is on disk (freshness lives on the Chart row)."""
    filename, _ = CHART_FILES[chart_key]
    return Path(chart_data_path(filename)).exists()


def _safe_int(value) -> int | None:
    """Coerce a CSV field to int, treating any non-numeric sentinel ("-", "NA", "") as missing."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_chart_csv(path: Path) -> Iterator[dict]:
    """Yield one ChartEntry kwargs dict (without chart_id) per valid CSV row."""
    with path.open(newline="", encoding="utf-8") as f:
        # start=2: line 1 is the header, so this is the file's real line number.
        for line_no, row in enumerate(csv.DictReader(f), start=2):
            try:
                chart_week = date.fromisoformat(row["chart_week"])
                position = int(row["current_week"])
            except (KeyError, TypeError, ValueError) as e:
                # One bad row must not abort a ~1M-row import.
                logger.warning(f"Skipping malformed chart row {line_no} in {path.name}: {e}")
                continue
            yield {
                "chart_week": chart_week,
                "position": position,
                "last_week_position": _safe_int(row.get("last_week")),
                "peak_position": _safe_int(row.get("peak_pos")),
                "weeks_on_chart": _safe_int(row.get("wks_on_chart")),
                "raw_title": row.get("title") or "",
                "raw_performer": row.get("performer") or "",
            }
