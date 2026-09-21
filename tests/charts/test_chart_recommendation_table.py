"""
Regression tests for ChartRecommendationTable's column-header sort (both
Missing Popular and Gap Fills sub-tabs share this widget -- see
chart_recommendations_tab.py). Sorting was previously disabled outright
(setSortingEnabled(False)); these tests cover the click-to-sort behavior and
the numeric-vs-lexicographic ordering of the Peak / Weeks on Chart / Connects
columns.
"""

from PySide6.QtCore import Qt

from src.charts.chart_recommendation_table import ChartRecommendationTable
from src.charts.chart_recommendations import MissingChartItem


def _make_items():
    return [
        MissingChartItem(chart_id=1, chart_name="Billboard Hot 100", entity_type="Track", raw_title="Song B", raw_performer="Artist B", peak_position=9, weeks_on_chart=2, gap_run_length=1),
        MissingChartItem(chart_id=1, chart_name="Billboard Hot 100", entity_type="Track", raw_title="Song A", raw_performer="Artist A", peak_position=10, weeks_on_chart=20, gap_run_length=10),
        MissingChartItem(chart_id=1, chart_name="Billboard Hot 100", entity_type="Track", raw_title="Song C", raw_performer="Artist C", peak_position=None, weeks_on_chart=None, gap_run_length=0),
    ]


def test_sorting_is_enabled(qapp):
    table = ChartRecommendationTable()
    assert table.isSortingEnabled()


def test_populate_keeps_pre_ranked_order_before_any_header_click(qapp):
    table = ChartRecommendationTable()
    table.populate(_make_items())
    titles = [table.topLevelItem(i).text(0) for i in range(table.topLevelItemCount())]
    assert titles == ["Song B", "Song A", "Song C"]


def test_clicking_title_header_sorts_alphabetically(qapp):
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
