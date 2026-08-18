"""
chart_recommendation_table.py

Flat results table for aggregated MissingChartItem rows (see
chart_recommendations.py). Same QTreeWidget convention as
chart_entry_table.py, but there's no single ChartEntry backing a row here
-- a recommendation folds together every week the song/album appeared
unmatched -- so rows carry no chart_entry_id and the table is
display-only (no selection-driven detail view).

The "Connects" column shows gap_run_length, which is only meaningful for
get_missing_gap_fills results (0/blank for get_missing_popular).
"""

from typing import Iterable

from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QTreeWidget, QTreeWidgetItem

from src.charts.chart_recommendations import MissingChartItem

_COLUMNS = ["Title", "Artist", "Type", "Chart", "Peak", "Weeks on Chart", "Connects"]


class ChartRecommendationTable(QTreeWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setColumnCount(len(_COLUMNS))
        self.setHeaderLabels(_COLUMNS)
        self.setRootIsDecorated(False)  # flat list, no expand arrows
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setSortingEnabled(False)  # results arrive pre-ranked
        self.header().setSectionResizeMode(0, QHeaderView.Stretch)  # Title column grows
        self.header().setSectionResizeMode(1, QHeaderView.Stretch)  # Artist column grows

    def populate(self, items: Iterable[MissingChartItem]) -> None:
        self.clear()
        for item in items:
            self.addTopLevelItem(
                QTreeWidgetItem(
                    [
                        item.raw_title,
                        item.raw_performer,
                        item.entity_type or "",
                        item.chart_name,
                        str(item.peak_position) if item.peak_position else "",
                        str(item.weeks_on_chart) if item.weeks_on_chart else "",
                        str(item.gap_run_length) if item.gap_run_length else "",
                    ]
                )
            )
