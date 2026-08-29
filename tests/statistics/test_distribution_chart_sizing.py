"""Regression guard for bugs.md #444 -- the Statistics distribution graphs
(BarDistributionChart / HistogramChart) rendered as unreadable slivers: a
hard-coded 90px bar area plus ``QSizePolicy.Fixed`` vertical sizing, so
the bars stayed ~86px tall no matter how much room the dialog had.

The fix raises the minimum bar-area height and paints bars relative to the
widget's actual height instead of the fixed constant. These tests lock in
both properties so nobody shrinks the charts back.
"""

from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QSizePolicy

from src.statistics.charts.bar_distribution_chart import BarDistributionChart
from src.statistics.charts.histogram_chart import HistogramChart
from src.statistics.stats.helpers import DistributionStats

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
