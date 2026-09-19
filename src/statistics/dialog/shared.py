"""Shared helpers used by MusicStatsDialog and its per-tab mixins."""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton
from sqlalchemy.exc import SQLAlchemyError

from src.common.cancellable_worker import CancellableWorker
from src.foundation.logger_config import logger

# Highlight color for stat values in the HTML rich-text labels below. Qt's
# rich-text renderer doesn't resolve QSS for inline HTML color attributes,
# so this can't be sourced from themes/dark_mode.qss like widget styling can.
_HIGHLIGHT_COLOR = "#EA8599"


def _hl(text: object) -> str:
    """Wrap `text` in the stats-dialog highlight span (bold, accent colour)."""
    return f"<span style='color: {_HIGHLIGHT_COLOR}; font-weight: bold;'>{text}</span>"


def _lifespan_detail(d: dict) -> str:
    """`N years (begin-end)` tile text; the range uses an intentional en dash."""
    return f"{d['years']} years ({d['begin_year']}–{d['end_year']})"  # noqa: RUF001


def _match_detail(d: dict) -> str:
    """`P% matched (m/total)` secondary text for a chart-year tile."""
    return f"{d['completeness']}% matched ({d['matched']}/{d['total']})"


class StatisticsWorker(CancellableWorker):
    """Runs get_comprehensive_statistics() off the main thread.

    The stats query does ~20 sequential SQL queries with joins/group-bys;
    running it on the GUI thread froze the whole app (not just this dialog)
    every time the dialog opened.
    """

    finished = Signal(dict)
    error = Signal(str)

    def __init__(self, statistics, parent=None):
        super().__init__(parent)
        self.statistics = statistics

    def run(self):
        try:
            stats = self.statistics.get_comprehensive_statistics()
            self.finished.emit(stats)
        except SQLAlchemyError as e:
            logger.error(f"Error loading statistics: {e}")
            self.error.emit(str(e))
        finally:
            # Read-only. See CancellableWorker's _release_db_session docstring.
            self._release_db_session()


def _placeholder_label(text: str) -> QLabel:
    """Muted note used on tabs/sections whose content lands in a later
    phase of the statistics expansion, so the tab shell is stable now and
    later phases only add content rather than reshuffling layout."""
    label = QLabel(text)
    label.setObjectName("StatPlaceholderLabel")
    label.setWordWrap(True)
    return label


def _recompute_bar(on_click):
    """Right-aligned 'Recompute' button for a lazily-loaded tab. These tabs
    load once per dialog session (see the loading-model note on
    StatisticsWorker); this is the only way to refresh one without closing
    and reopening the whole dialog. Returns (layout, button) -- the caller
    adds the layout to its tab and keeps the button to disable it while a
    recompute is in flight."""
    bar = QHBoxLayout()
    bar.addStretch()
    button = QPushButton("Recompute")
    button.clicked.connect(on_click)
    bar.addWidget(button)
    return bar, button
