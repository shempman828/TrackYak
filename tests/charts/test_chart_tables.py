"""Chart entry and recommendation tables: initial order and numeric/alphabetic
header sorting.
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHeaderView

from src.charts.chart_recommendations import MissingChartItem
from src.charts.ui.chart_entry_table import ChartEntryTable
from src.charts.ui.chart_recommendation_table import ChartRecommendationTable
from src.db.db_tables.chart import ChartEntry

# ---------------------------------------------------------------------------
# Regression tests for ChartEntryTable's column-header sort (shared by
# ---------------------------------------------------------------------------


def _make_entries():
    return [
        ChartEntry(chart_entry_id=1, position=9, raw_title="Song B", raw_performer="Artist B", peak_position=9, weeks_on_chart=2),
        ChartEntry(chart_entry_id=2, position=10, raw_title="Song A", raw_performer="Artist A", peak_position=10, weeks_on_chart=20),
        ChartEntry(chart_entry_id=3, position=1, raw_title="Song C", raw_performer="Artist C", peak_position=None, weeks_on_chart=None),
    ]


def test_populate_keeps_pre_ordered_position_before_any_header_click(qapp):
    table = ChartEntryTable()
    table.populate(_make_entries())
    titles = [table.topLevelItem(i).text(1) for i in range(table.topLevelItemCount())]
    assert titles == ["Song B", "Song A", "Song C"]


def test_clicking_title_header_sorts_alphabetically(qapp):
    table = ChartEntryTable()
    table.populate(_make_entries())
    table.sortByColumn(1, Qt.AscendingOrder)
    titles = [table.topLevelItem(i).text(1) for i in range(table.topLevelItemCount())]
    assert titles == ["Song A", "Song B", "Song C"]


def test_clicking_pos_header_sorts_numerically_not_lexicographically(qapp):
    # Positions 9 and 10: a lexicographic sort would wrongly put "10" before "9".
    table = ChartEntryTable()
    table.populate(_make_entries())
    table.sortByColumn(0, Qt.AscendingOrder)
    titles = [table.topLevelItem(i).text(1) for i in range(table.topLevelItemCount())]
    assert titles == ["Song C", "Song B", "Song A"]


def test_clicking_peak_header_sorts_numerically_with_blank_first(qapp):
    table = ChartEntryTable()
    table.populate(_make_entries())
    table.sortByColumn(3, Qt.AscendingOrder)
    titles = [table.topLevelItem(i).text(1) for i in range(table.topLevelItemCount())]
    assert titles == ["Song C", "Song B", "Song A"]


# ---------------------------------------------------------------------------
# Regression tests for ChartRecommendationTable's column-header sort (both
# ---------------------------------------------------------------------------


def _make_items():
    return [
        MissingChartItem(chart_id=1, chart_name="Billboard Hot 100", entity_type="Track", raw_title="Song B", raw_performer="Artist B", peak_position=9, weeks_on_chart=2, gap_run_length=1),
        MissingChartItem(chart_id=1, chart_name="Billboard Hot 100", entity_type="Track", raw_title="Song A", raw_performer="Artist A", peak_position=10, weeks_on_chart=20, gap_run_length=10),
        MissingChartItem(chart_id=1, chart_name="Billboard Hot 100", entity_type="Track", raw_title="Song C", raw_performer="Artist C", peak_position=None, weeks_on_chart=None, gap_run_length=0),
    ]


def test_populate_keeps_pre_ranked_order_before_any_header_click(qapp):
    table = ChartRecommendationTable()
    table.populate(_make_items())
    titles = [table.topLevelItem(i).text(0) for i in range(table.topLevelItemCount())]
    assert titles == ["Song B", "Song A", "Song C"]


def test_recommendation_title_header_sorts_alphabetically(qapp):
    table = ChartRecommendationTable()
    table.populate(_make_items())
    table.sortByColumn(0, Qt.AscendingOrder)
    titles = [table.topLevelItem(i).text(0) for i in range(table.topLevelItemCount())]
    assert titles == ["Song A", "Song B", "Song C"]


def test_clicking_peak_header_sorts_numerically_not_lexicographically(qapp):
    # Peak values 9 and 10: a lexicographic sort would wrongly put "10"
    # before "9". Blank peak (Song C) sorts as 0, i.e. first ascending.
    table = ChartRecommendationTable()
    table.populate(_make_items())
    table.sortByColumn(4, Qt.AscendingOrder)
    titles = [table.topLevelItem(i).text(0) for i in range(table.topLevelItemCount())]
    assert titles == ["Song C", "Song B", "Song A"]


def test_clicking_connects_header_sorts_numerically(qapp):
    table = ChartRecommendationTable()
    table.populate(_make_items())
    table.sortByColumn(6, Qt.DescendingOrder)
    titles = [table.topLevelItem(i).text(0) for i in range(table.topLevelItemCount())]
    assert titles == ["Song A", "Song B", "Song C"]
