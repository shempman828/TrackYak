"""
theme_palette.py

ThemedChartWidget: base class extracting the theme-lookup boilerplate that
was duplicated inline in rating_distribution_chart.py (the house pattern
for hand-painted QPainter stat widgets). Every new chart/leaderboard/tile
widget in src/statistics/charts/ subclasses this and supplies its own
_THEME_PALETTE dict keyed by the app's four display themes.
"""

import configparser

from PySide6.QtWidgets import QWidget

from src.core.config_setup import app_config
from src.core.logger_config import logger


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
