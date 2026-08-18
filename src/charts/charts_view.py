"""
charts_view.py

Top-level Charts nav view: header controls (Download / Fetch Updates /
Match Now) driving the download -> import -> match pipeline, plus the two
browse tabs (Week Browser, Search). Worker wiring follows
src/sync/sync_execution_mixin.py's pattern (clicked -> build worker ->
connect progress/finished/error -> start()).
"""

from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from src.charts.chart_download import chart_csv_exists
from src.charts.chart_download_worker import ChartDownloadWorker
from src.charts.chart_import_worker import ChartImportWorker
from src.charts.chart_matching_worker import ChartMatchingWorker
from src.charts.chart_recommendations_tab import ChartRecommendationsTab
from src.charts.chart_search_tab import ChartSearchTab
from src.charts.chart_week_browser_tab import ChartWeekBrowserTab
from src.core.logger_config import logger
from src.core.status_utility import show_status_message


class ChartsView(QWidget):
    def __init__(self, controller):
        super().__init__()
        self.controller = controller
        self._charts = []
        self._download_worker = None
        self._import_worker = None
        self._match_worker = None
        self._import_queue = []
        self._match_queue = []
        self._match_total = 0
        self._match_chart_key = ""
        self._match_queue_label = ""
        self.init_ui()
        self.load_charts()

    def init_ui(self):
        layout = QVBoxLayout(self)

        header = QHBoxLayout()
        self.download_btn = QPushButton("Download Chart Data")
        self.download_btn.clicked.connect(self._start_download)
        header.addWidget(self.download_btn)

        self.fetch_btn = QPushButton("Fetch Updates")
        self.fetch_btn.clicked.connect(self._start_fetch)
        header.addWidget(self.fetch_btn)

        self.match_btn = QPushButton("Match Now")
        self.match_btn.clicked.connect(self._start_match)
        header.addWidget(self.match_btn)

        header.addStretch()
        self.status_label = QLabel("")
        header.addWidget(self.status_label)
        layout.addLayout(header)

        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        self.progress_bar.setFixedHeight(6)
        self.progress_bar.setTextVisible(False)
        layout.addWidget(self.progress_bar)

        self.tabs = QTabWidget()
        self.week_tab = ChartWeekBrowserTab(self.controller)
        self.search_tab = ChartSearchTab(self.controller)
        self.recommendations_tab = ChartRecommendationsTab(self.controller)
        self.tabs.addTab(self.week_tab, "Week Browser")
        self.tabs.addTab(self.search_tab, "Search")
        self.tabs.addTab(self.recommendations_tab, "Recommendations")
        layout.addWidget(self.tabs)

    # -----------------------------------------------------------------------
    # State
    # -----------------------------------------------------------------------

    def load_charts(self):
        """Refresh header button visibility + tab data from current DB/disk
        state. Called on __init__ and (via main_window.py's revisit-refresh
        dispatch) whenever the user navigates back to this view."""
        self._charts = self.controller.get.get_all_entities("Chart")

        all_downloaded = bool(self._charts) and all(
            chart_csv_exists(c.chart_key) for c in self._charts
        )
        synced_charts = [c for c in self._charts if c.last_synced_week is not None]

        self.download_btn.setVisible(not all_downloaded)
        self.fetch_btn.setVisible(all_downloaded)
        self.match_btn.setVisible(bool(synced_charts))

        if synced_charts:
            self.week_tab.set_charts(synced_charts)
            self.search_tab.set_charts(synced_charts)
            self.recommendations_tab.set_charts(synced_charts)
            latest = max(
                (c.last_downloaded_at for c in self._charts if c.last_downloaded_at),
                default=None,
            )
            self.status_label.setText(
                f"Last updated: {latest.strftime('%Y-%m-%d %H:%M')}" if latest else ""
            )
        else:
            self.status_label.setText(
                "No chart data imported yet." if all_downloaded else ""
            )

    # -----------------------------------------------------------------------
    # Download -> import pipeline (Download Chart Data / Fetch Updates)
    # -----------------------------------------------------------------------

    def _start_download(self):
        self._run_download_then_import()

    def _start_fetch(self):
        self._run_download_then_import()

    def _run_download_then_import(self):
        self.download_btn.setEnabled(False)
        self.fetch_btn.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 0)  # indeterminate until Content-Length known
        self.status_label.setText("Downloading chart data...")

        chart_keys = [c.chart_key for c in self._charts]
        self._download_worker = ChartDownloadWorker(chart_keys)
        self._download_worker.progress.connect(self._on_download_progress)
        self._download_worker.finished.connect(self._on_download_finished)
        self._download_worker.error.connect(self._on_worker_error)
        self._download_worker.start()

    def _on_download_progress(self, downloaded: int, total: int):
        if total > 0:
            self.progress_bar.setRange(0, total)
            self.progress_bar.setValue(downloaded)

    def _on_download_finished(self, results: dict):
        show_status_message(self, f"Downloaded {len(results)} chart file(s)")
        # Download -> import is one chained user action, not two manual steps.
        self._import_queue = [c.chart_key for c in self._charts]
        self._run_next_import()

    def _run_next_import(self):
        if not self._import_queue:
            self.progress_bar.setVisible(False)
            self.download_btn.setEnabled(True)
            self.fetch_btn.setEnabled(True)
            self.load_charts()
            self.week_tab.refresh()
            self.search_tab.refresh()
            self.recommendations_tab.refresh()
            return

        chart_key = self._import_queue.pop(0)
        self.progress_bar.setRange(0, 0)
        self.status_label.setText(f"Importing {chart_key}...")
        self._import_worker = ChartImportWorker(self.controller, chart_key)
        self._import_worker.progress.connect(self._on_progress)
        self._import_worker.finished.connect(
            lambda n, key=chart_key: self._on_import_finished(key, n)
        )
        self._import_worker.error.connect(self._on_worker_error)
        self._import_worker.start()

    def _on_import_finished(self, chart_key: str, imported: int):
        show_status_message(self, f"Imported {imported} new row(s) for {chart_key}")
        # finished is a custom payload signal emitted from inside run(), before
        # the thread has actually unwound (its finally block still has to
        # release the DB session). wait() blocks until QThread.isRunning() is
        # actually False, so _run_next_import() doesn't clobber self._import_worker
        # -- the only reference to this QThread -- while it's still alive.
        self._import_worker.wait()
        self._run_next_import()

    def _on_progress(self, done: int, total: int):
        if total > 0:
            self.progress_bar.setRange(0, total)
            self.progress_bar.setValue(done)

    # -----------------------------------------------------------------------
    # Matching (Match Now)
    # -----------------------------------------------------------------------

    def _start_match(self):
        self.match_btn.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 0)
        self._match_queue = [
            c.chart_key for c in self._charts if c.last_synced_week is not None
        ]
        self._match_total = len(self._match_queue)
        self._run_next_match()

    def _run_next_match(self):
        if not self._match_queue:
            self.progress_bar.setVisible(False)
            self.match_btn.setEnabled(True)
            self.load_charts()
            self.week_tab.refresh()
            self.search_tab.refresh()
            self.recommendations_tab.refresh()
            return

        chart_key = self._match_queue.pop(0)
        self._match_chart_key = chart_key
        position = self._match_total - len(self._match_queue)
        self._match_queue_label = f"chart {position}/{self._match_total}"
        self.progress_bar.setRange(0, 0)
        self.status_label.setText(
            f"Matching {chart_key} ({self._match_queue_label})..."
        )
        self._match_worker = ChartMatchingWorker(self.controller, chart_key)
        self._match_worker.stage.connect(self._on_match_stage)
        self._match_worker.progress.connect(self._on_match_progress)
        self._match_worker.finished.connect(
            lambda stats, key=chart_key: self._on_match_finished(key, stats)
        )
        self._match_worker.error.connect(self._on_worker_error)
        self._match_worker.start()

    def _on_match_stage(self, stage: str):
        self.status_label.setText(
            f"{stage} ({self._match_chart_key}, {self._match_queue_label})"
        )

    def _on_match_progress(self, scored: int, total: int, matched: int):
        if total > 0:
            self.progress_bar.setRange(0, total)
            self.progress_bar.setValue(scored)
        self.status_label.setText(
            f"Matching {self._match_chart_key} ({self._match_queue_label}): "
            f"{scored}/{total} scored, {matched} matched"
        )

    def _on_match_finished(self, chart_key: str, stats):
        show_status_message(self, f"Matched {stats.matched}/{stats.total_unmatched} for {chart_key}")
        # See _on_import_finished: finished fires before the thread has fully
        # unwound, so wait() here before reassigning self._match_worker.
        self._match_worker.wait()
        self._run_next_match()

    # -----------------------------------------------------------------------
    # Shared error handling
    # -----------------------------------------------------------------------

    def _on_worker_error(self, message: str):
        logger.error(f"Charts operation failed: {message}")
        self.progress_bar.setVisible(False)
        self.download_btn.setEnabled(True)
        self.fetch_btn.setEnabled(True)
        self.match_btn.setEnabled(True)
        QMessageBox.warning(self, "Charts", f"Operation failed: {message}")
