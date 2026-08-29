"""YearTimeSeriesChart -- the filled-area replacement for the categorical
BarDistributionChart on the Albums tab's "Release Year Distribution".

The bar chart it replaces had ``max_categories = 24`` and, with
``sort_by="label"``, silently sliced a 100+ year distribution down to its
earliest 24 years. These tests lock in that every year in the range now
survives, plus the data-coercion / edge-case behaviour.
"""

from PySide6.QtCore import QPoint
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QSizePolicy

from src.statistics.charts.year_time_series_chart import YearTimeSeriesChart


def _paint(chart, w=560, h=200):
    """Force a paint pass so chart._year_points is populated."""
    chart.resize(w, h)
    image = QImage(chart.size(), QImage.Format_ARGB32)
    chart.render(image)
    return image


def test_all_years_survive_no_24_category_cap(qapp):
    data = {y: (y % 7) + 1 for y in range(1906, 2027)}  # 121 distinct years
    chart = YearTimeSeriesChart()
    chart.set_data(data)
    _paint(chart)

    years = [yp[0] for yp in chart._year_points]
    assert len(years) == 121
    assert years[0] == 1906
    assert years[-1] == 2026


def test_gap_years_are_filled_with_zero(qapp):
    chart = YearTimeSeriesChart()
    chart.set_data({1990: 10, 1995: 4})  # 1991-1994 absent
    _paint(chart)

    years = [yp[0] for yp in chart._year_points]
    assert years == [1990, 1991, 1992, 1993, 1994, 1995]


def test_set_data_coerces_and_drops_junk(qapp):
    chart = YearTimeSeriesChart()
    chart.set_data({"1990": "5", 1991: 3, None: 2, "bad": 1, 1992: None})
    assert chart._counts == {1990: 5, 1991: 3}


def test_empty_and_none_do_not_raise(qapp):
    chart = YearTimeSeriesChart()
    chart.set_data({})
    _paint(chart)
    assert chart._year_points == []

    chart.set_data(None)
    _paint(chart)
    assert chart._year_points == []


def test_single_year_pads_the_axis(qapp):
    chart = YearTimeSeriesChart()
    chart.set_data({2015: 4})
    _paint(chart)
    assert [yp[0] for yp in chart._year_points] == [2014, 2015, 2016]


def test_hover_snaps_to_nearest_year(qapp):
    chart = YearTimeSeriesChart()
    chart.set_data(dict.fromkeys(range(2000, 2021), 1))
    _paint(chart)

    left_x = int(chart._year_points[0][1])
    right_x = int(chart._year_points[-1][1])
    assert chart._year_at(QPoint(left_x, 100)) == 2000
    assert chart._year_at(QPoint(right_x, 100)) == 2020
    assert chart._year_at(QPoint((left_x + right_x) // 2, 100)) == 2010


def test_tick_step_is_nice(qapp):
    assert YearTimeSeriesChart._tick_step(120) == 20
    assert YearTimeSeriesChart._tick_step(2) == 1
    assert YearTimeSeriesChart._tick_step(10) == 2


def test_vertical_policy_and_min_height(qapp):
    chart = YearTimeSeriesChart()
    assert chart.sizePolicy().verticalPolicy() != QSizePolicy.Policy.Fixed
    assert chart.minimumHeight() >= 180
