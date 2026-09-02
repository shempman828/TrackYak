"""Regression guard for bugs.md #444 -- the Statistics distribution graphs
(BarDistributionChart / HistogramChart) rendered as unreadable slivers: a
hard-coded 90px bar area plus ``QSizePolicy.Fixed`` vertical sizing, so
the bars stayed ~86px tall no matter how much room the dialog had.

The fix raises the minimum bar-area height and paints bars relative to the
widget's actual height instead of the fixed constant. These tests lock in
both properties so nobody shrinks the charts back.
"""

from PySide6.QtCore import QPoint
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QSizePolicy

from src.statistics.stats.helpers import DistributionStats
from src.statistics.widgets.bar_distribution_chart import BarDistributionChart
from src.statistics.widgets.histogram_chart import HistogramChart
from src.statistics.widgets.year_time_series_chart import YearTimeSeriesChart

# ---- test_distribution_chart_sizing.py ---------------------------------------
_READABLE_MIN_HEIGHT = 190


def _surface_differs_at(widget, x, y, height=420):
    """Render ``widget`` at ``height`` px tall and report whether the pixel
    at (x, y) has been painted over (i.e. differs from the top-left surface
    pixel). Used to prove a bar reaches well below the old 96px baseline."""
    widget.resize(600, height)
    image = QImage(widget.size(), QImage.Format_ARGB32)
    widget.render(image)
    surface = QColor(image.pixel(2, 2))
    return QColor(image.pixel(x, y)) != surface


def test_bar_chart_minimum_height_is_readable(qapp):
    chart = BarDistributionChart()
    assert chart.minimumHeight() >= _READABLE_MIN_HEIGHT
    assert chart.sizeHint().height() >= _READABLE_MIN_HEIGHT


def test_bar_chart_vertical_policy_is_not_fixed(qapp):
    chart = BarDistributionChart()
    assert chart.sizePolicy().verticalPolicy() != QSizePolicy.Policy.Fixed


def test_bar_chart_bars_scale_to_widget_height(qapp):
    chart = BarDistributionChart()
    chart.set_data({"A": 10, "B": 3, "C": 1})
    # y=320 is far below the old fixed 96px bar baseline; the tallest bar
    # must extend there now that the bar area tracks widget height.
    assert _surface_differs_at(chart, x=40, y=320)


def test_histogram_minimum_height_is_readable(qapp):
    chart = HistogramChart()
    assert chart.minimumHeight() >= _READABLE_MIN_HEIGHT
    assert chart.sizeHint().height() >= _READABLE_MIN_HEIGHT


def test_histogram_vertical_policy_is_not_fixed(qapp):
    chart = HistogramChart()
    assert chart.sizePolicy().verticalPolicy() != QSizePolicy.Policy.Fixed


def test_histogram_bars_scale_to_widget_height(qapp):
    chart = HistogramChart()
    chart.set_data(
        DistributionStats(
            buckets=[(0.0, 1.0, 10), (1.0, 2.0, 4), (2.0, 3.0, 1)],
            n=15,
            minimum=0.0,
            maximum=3.0,
            mean=1.0,
            median=1.0,
            stdev=0.5,
        )
    )
    assert _surface_differs_at(chart, x=40, y=320)


# ---- test_year_time_series_chart.py ------------------------------------------
# YearTimeSeriesChart -- the filled-area replacement for the categorical
# BarDistributionChart on the Albums tab's "Release Year Distribution".
#
# The bar chart it replaces had ``max_categories = 24`` and, with
# ``sort_by="label"``, silently sliced a 100+ year distribution down to its
# earliest 24 years. These tests lock in that every year in the range now
# survives, plus the data-coercion / edge-case behaviour.
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
