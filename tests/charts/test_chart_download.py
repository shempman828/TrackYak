"""
Tests for chart_download.py's CSV parsing -- specifically _safe_int()'s
handling of the real data's missing-value sentinels. A hand-written fixture
CSV using only "-" didn't catch that the real Hot 100 CSV also uses "NA"
for the same "no prior week" meaning (found during the Phase 5 real-data
run against the actual GitHub-hosted CSV, not anticipated up front).
"""

import csv
from pathlib import Path

import pytest

from src.charts.chart_download import _safe_int, parse_chart_csv


@pytest.mark.parametrize("value,expected", [("42", 42), ("-", None), ("NA", None), ("", None), (None, None)])
def test_safe_int_handles_all_observed_sentinels(value, expected):
    assert _safe_int(value) == expected


def test_parse_chart_csv_handles_mixed_sentinels(tmp_path):
    csv_path = tmp_path / "mixed.csv"
    with open(csv_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["chart_week", "current_week", "title", "performer", "last_week", "peak_pos", "wks_on_chart"])
        writer.writerow(["2023-12-30", "1", "Song A", "Artist A", "-", "1", "5"])
        writer.writerow(["2023-12-30", "2", "Song B", "Artist B", "NA", "2", "1"])
        writer.writerow(["2023-12-30", "3", "Song C", "Artist C", "3", "1", "10"])

    rows = list(parse_chart_csv(Path(csv_path)))

    assert rows[0]["last_week_position"] is None  # "-" sentinel
    assert rows[1]["last_week_position"] is None  # "NA" sentinel
    assert rows[2]["last_week_position"] == 3  # real value
