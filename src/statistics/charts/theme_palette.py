"""
theme_palette.py

ThemedChartWidget: base class extracting the theme-lookup boilerplate that
was duplicated inline in rating_distribution_chart.py (the house pattern
for hand-painted QPainter stat widgets). Every new chart/leaderboard/tile
widget in src/statistics/charts/ subclasses this and supplies its own
_THEME_PALETTE dict keyed by the app's four display themes.
"""

import configparser

from PySide6.QtGui import QColor
from PySide6.QtWidgets import QWidget

from src.core.config_setup import app_config
from src.core.logger_config import logger

# Shared by every plain categorical/continuous bar chart (surface, bar,
# bar_border, text, muted_text) -- BarDistributionChart and HistogramChart
# both use this exact palette as-is; a widget with different visual needs
# (a peak-highlight color, a gridline, a chip fill) defines its own
# _THEME_PALETTE instead of reusing this one.
STANDARD_BAR_PALETTE = {
    "dark_mode": (
        QColor("#11121a"),
        QColor("#8599EA"),
        QColor(133, 153, 234, 140),
        QColor("#b8c0f0"),
        QColor("#7a82a8"),
    ),
    "light_mode": (
        QColor("#ffffff"),
        QColor("#5566c0"),
        QColor(85, 102, 192, 140),
        QColor("#2b2c36"),
        QColor("#6b6f80"),
    ),
    "colorful_mode": (
        QColor("#ffffff"),
        QColor("#ea8599"),
        QColor(234, 133, 153, 140),
        QColor("#1c1c21"),
        QColor("#777777"),
    ),
    "accessibility_mode": (
        QColor("#ffffff"),
        QColor("#a8580c"),
        QColor(168, 88, 12, 140),
        QColor("#1c1c21"),
        QColor("#4a4a4a"),
    ),
}


class ThemedChartWidget(QWidget):
    """QWidget base that re-reads the active display theme and looks up a
    subclass-supplied palette tuple, falling back to "dark_mode" if the
    theme name isn't recognized or can't be read.

    Subclasses must define `_THEME_PALETTE: dict[str, tuple]` with a
    "dark_mode" key at minimum, and call `self._apply_theme_palette()` to
    populate `self._palette` with that theme's tuple.
    """

    _THEME_PALETTE: dict = {}

    def _apply_theme_palette(self):
        theme_name = None
        try:
            theme_name = app_config.get_display_theme()
        except configparser.Error as e:
            logger.warning(f"Failed to get display theme, using default: {e}")
        self._palette = self._THEME_PALETTE.get(
            theme_name, self._THEME_PALETTE.get("dark_mode")
        )
