"""Charts nav view: header actions for the download -> import -> match pipeline, plus the browse tabs."""

import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QHBoxLayout, QLabel, QMessageBox, QProgressBar, QPushButton, QStackedWidget, QTabWidget, QVBoxLayout, QWidget
from sqlalchemy import func, select

from src.charts.chart_download import chart_csv_exists
from src.charts.chart_playlist_builder import CHART_PLAYLIST_MARKER_PREFIX
from src.charts.ui.chart_recommendations_tab import ChartRecommendationsTab
from src.charts.ui.chart_search_tab import ChartSearchTab
from src.charts.ui.chart_week_browser_tab import ChartWeekBrowserTab
from src.charts.workers.chart_download_worker import ChartDownloadWorker
from src.charts.workers.chart_import_worker import ChartImportWorker
from src.charts.workers.chart_matching_worker import ChartMatchingWorker
from src.charts.workers.chart_playlist_worker import ChartPlaylistWorker
from src.common.eta_estimator import estimate_remaining
from src.common.widgets.detail_card import DetailCard
from src.common.widgets.style_utils import refresh_style
from src.core.status_widget import show_status_message
from src.db.db_tables.chart import ChartEntry
from src.foundation.logger_config import logger
from src.statistics.widgets.stat_tile import StatTileWidget

_CANCEL_POLL_MS = 100


class ChartsView(QWidget):
    """Top-level Charts view: pipeline buttons, summary tiles, and the Week Browser/Search/Recommendations tabs."""

    def __init__(self, controller):
        super().__init__()
        self.controller = controller
        self._charts = []
        self._download_worker = None
        self._import_worker = None
        self._match_worker = None
        self._playlist_worker = None
        self._import_queue = []
        self._match_queue = []
        self._match_total = 0
        self._match_chart_key = ""
        self._match_queue_label = ""
        self._match_start_time = None
        self._cancelling = False
        self.init_ui()
        self.load_charts()

    def init_ui(self):
        """Build the header, stat tiles, progress bar, and the empty-state/tabs stack."""
        layout = QVBoxLayout(self)

        header = QHBoxLayout()
        self.download_btn = QPushButton("Download Chart Data")
        self.download_btn.clicked.connect(self._run_download_then_import)
        header.addWidget(self.download_btn)

        self.fetch_btn = QPushButton("Fetch Updates")
        self.fetch_btn.clicked.connect(self._run_download_then_import)
        header.addWidget(self.fetch_btn)

        self.match_btn = QPushButton("Match Now")
        self.match_btn.clicked.connect(self._start_match)
        header.addWidget(self.match_btn)

        self.playlists_btn = QPushButton("Generate Charts Playlists")
        self.playlists_btn.clicked.connect(self._start_playlist_generation)
        header.addWidget(self.playlists_btn)

        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setToolTip("Stop the running download, import, or match. Work already saved is kept.")
        self.cancel_btn.clicked.connect(self._cancel_pipeline)
        self.cancel_btn.setVisible(False)
        header.addWidget(self.cancel_btn)

        header.addStretch()
        self.status_label = QLabel("")
        header.addWidget(self.status_label)
        layout.addLayout(header)

        self.stats_row = QWidget()
        stats_layout = QHBoxLayout(self.stats_row)
        stats_layout.setContentsMargins(0, 0, 0, 0)
        stats_layout.setSpacing(10)
        self.stat_synced = StatTileWidget("Charts Synced")
        self.stat_match_rate = StatTileWidget("Match Rate")
        self.stat_updated = StatTileWidget("Last Updated")
        for tile in (self.stat_synced, self.stat_match_rate, self.stat_updated):
            stats_layout.addWidget(tile)
        layout.addWidget(self.stats_row)

        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        self.progress_bar.setFixedHeight(6)
        self.progress_bar.setTextVisible(False)
        layout.addWidget(self.progress_bar)

        self.empty_state_card = DetailCard("Get Started")
        empty_hint = QLabel("Download chart data above to begin matching it against your library.")
        empty_hint.setProperty("textRole", "muted")
        empty_hint.setWordWrap(True)
        empty_hint.setAlignment(Qt.AlignCenter)
        self.empty_state_card.body.addStretch()
        self.empty_state_card.body.addWidget(empty_hint)
        self.empty_state_card.body.addStretch()

        self.tabs = QTabWidget()
        self.week_tab = ChartWeekBrowserTab(self.controller)
        self.search_tab = ChartSearchTab(self.controller)
        self.recommendations_tab = ChartRecommendationsTab(self.controller)
        self.tabs.addTab(self.week_tab, "Week Browser")
        self.tabs.addTab(self.search_tab, "Search")
        self.tabs.addTab(self.recommendations_tab, "Recommendations")

        # A stack (not two toggled siblings) keeps exactly one Expanding child in the
        # layout, so no blank gap opens between the header and the card.
        self.content_stack = QStackedWidget()
        self.content_stack.addWidget(self.empty_state_card)
        self.content_stack.addWidget(self.tabs)
        layout.addWidget(self.content_stack)

        self._cancel_poll = QTimer(self)
        self._cancel_poll.setInterval(_CANCEL_POLL_MS)
        self._cancel_poll.timeout.connect(self._poll_cancel)

    # -----------------------------------------------------------------------
    # State
    # -----------------------------------------------------------------------

    def load_charts(self):
        """Refresh header buttons, stat tiles and tab data from the DB and disk (also on every revisit)."""
        self._charts = self.controller.get.get_all_entities("Chart")

        all_downloaded = bool(self._charts) and all(chart_csv_exists(c.chart_key) for c in self._charts)
        synced_charts = [c for c in self._charts if c.last_synced_week is not None]

        self.download_btn.setVisible(not all_downloaded)
        self.fetch_btn.setVisible(all_downloaded)
        self.match_btn.setVisible(bool(synced_charts))
        self.playlists_btn.setVisible(bool(synced_charts))
        self._refresh_playlists_btn_label()
        self._refresh_primary_button(all_downloaded)

        self.stats_row.setVisible(bool(synced_charts))
        self.content_stack.setCurrentWidget(self.tabs if synced_charts else self.empty_state_card)

        if synced_charts:
            self.week_tab.set_charts(synced_charts)
            self.search_tab.set_charts(synced_charts)
            self.recommendations_tab.set_charts(synced_charts)
            latest = max((c.last_downloaded_at for c in self._charts if c.last_downloaded_at), default=None)
            self.status_label.setText(f"Last updated: {latest.strftime('%Y-%m-%d %H:%M')}" if latest else "")
            self._refresh_stats(synced_charts, latest)
        else:
            self.status_label.setText("No chart data imported yet." if all_downloaded else "")

    def _chart_name(self, chart_key: str) -> str:
        """Return the display name for chart_key, else the key itself."""
        return next((c.chart_name for c in self._charts if c.chart_key == chart_key), chart_key)

    def _refresh_primary_button(self, all_downloaded: bool) -> None:
        """Give the accent fill to whichever of Download/Fetch is visible."""
        self.download_btn.setObjectName("" if all_downloaded else "PrimaryButton")
        self.fetch_btn.setObjectName("PrimaryButton" if all_downloaded else "")
        refresh_style(self.download_btn)
        refresh_style(self.fetch_btn)

    def _refresh_stats(self, synced_charts, latest) -> None:
        """Fill the Charts Synced, Match Rate and Last Updated tiles."""
        chart_ids = [c.chart_id for c in synced_charts]
        session = self.controller.get.session
        total = session.scalar(select(func.count()).select_from(ChartEntry).where(ChartEntry.chart_id.in_(chart_ids))) or 0
        matched = session.scalar(select(func.count()).select_from(ChartEntry).where(ChartEntry.chart_id.in_(chart_ids), ChartEntry.entity_id.isnot(None))) or 0
        self.stat_synced.set_data(len(synced_charts), f"of {len(self._charts)} chart(s)")
        match_rate = f"{matched / total:.0%}" if total else "N/A"
        self.stat_match_rate.set_data(match_rate, f"{matched:,} / {total:,} entries")
        self.stat_updated.set_data(latest.strftime("%b %d, %Y") if latest else "N/A")

    def _refresh_playlists_btn_label(self):
        """Label the playlists button "Generate" until a chart playlist tree exists, then "Update"."""
        has_tree = self.controller.get.get_entity_object("Playlist", playlist_description__startswith=CHART_PLAYLIST_MARKER_PREFIX) is not None
        self.playlists_btn.setText("Update Charts Playlists" if has_tree else "Generate Charts Playlists")

    # -----------------------------------------------------------------------
    # Busy state shared by every pipeline
    # -----------------------------------------------------------------------

    def _set_busy(self, busy: bool, cancellable: bool = False) -> None:
        """Disable every pipeline button while a worker runs, so two writers never run at once."""
        for button in (self.download_btn, self.fetch_btn, self.match_btn, self.playlists_btn):
            button.setEnabled(not busy)
        self.cancel_btn.setVisible(busy and cancellable)
        self.cancel_btn.setEnabled(busy and cancellable)
        self.progress_bar.setVisible(busy)
        if busy:
            self.progress_bar.setRange(0, 0)  # indeterminate until a total is known

    def _finish_pipeline(self) -> None:
        """Reset the busy state and reload every tab after a pipeline ends."""
        self._cancel_poll.stop()
        self._set_busy(False)
        self.load_charts()
        self.week_tab.refresh()
        self.search_tab.refresh()
        self.recommendations_tab.refresh()

    def _pipeline_workers(self) -> list:
        """Return the cancellable workers that exist."""
        return [w for w in (self._download_worker, self._import_worker, self._match_worker) if w is not None]

    def _cancel_pipeline(self) -> None:
        """Stop the running download/import/match and drop the queued charts."""
        self._cancelling = True
        self._import_queue = []
        self._match_queue = []
        self.cancel_btn.setEnabled(False)
        self.status_label.setText("Cancelling...")
        for worker in self._pipeline_workers():
            if worker.isRunning():
                worker.request_cancel()
        # A cancelled worker emits no finished signal, so poll until its thread ends.
        self._cancel_poll.start()

    def _poll_cancel(self) -> None:
        """End the pipeline once every cancelled worker thread has stopped."""
        if any(w.isRunning() for w in self._pipeline_workers()):
            return
        self._finish_pipeline()
        show_status_message(self, "Chart operation cancelled")

    def _on_progress(self, done: int, total: int):
        """Show determinate progress once a total is known."""
        if total > 0:
            self.progress_bar.setRange(0, total)
            self.progress_bar.setValue(done)

    # -----------------------------------------------------------------------
    # Download -> import pipeline (Download Chart Data / Fetch Updates)
    # -----------------------------------------------------------------------

    def _run_download_then_import(self):
        """Download every chart CSV, then import each one."""
        self._cancelling = False
        self._set_busy(True, cancellable=True)
        self.status_label.setText("Downloading chart data...")

        self._download_worker = ChartDownloadWorker([c.chart_key for c in self._charts])
        self._download_worker.progress.connect(self._on_progress)
        self._download_worker.finished.connect(self._on_download_finished)
        self._download_worker.error.connect(self._on_worker_error)
        self._download_worker.start()

    def _on_download_finished(self, results: dict):
        """Queue an import for each chart file that was downloaded."""
        if self._cancelling:
            return
        show_status_message(self, f"Downloaded {len(results)} chart file(s)")
        # Download -> import is one chained user action, not two manual steps.
        self._import_queue = [c.chart_key for c in self._charts if c.chart_key in results]
        self._run_next_import()

    def _run_next_import(self):
        """Start the next queued import, or end the pipeline."""
        if not self._import_queue:
            self._finish_pipeline()
            return

        chart_key = self._import_queue.pop(0)
        self.progress_bar.setRange(0, 0)
        self.status_label.setText(f"Importing {self._chart_name(chart_key)}...")
        self._import_worker = ChartImportWorker(self.controller, chart_key)
        self._import_worker.progress.connect(self._on_progress)
        self._import_worker.finished.connect(lambda n, key=chart_key: self._on_import_finished(key, n))
        self._import_worker.error.connect(self._on_worker_error)
        self._import_worker.start()

    def _on_import_finished(self, chart_key: str, imported: int):
        """Report one chart's import and start the next one."""
        show_status_message(self, f"Imported {imported} new row(s) for {self._chart_name(chart_key)}")
        # finished fires inside run(), before the thread unwinds; wait() so the only
        # reference to this QThread is not replaced while it is still alive.
        self._import_worker.wait()
        if not self._cancelling:
            self._run_next_import()

    # -----------------------------------------------------------------------
    # Matching (Match Now)
    # -----------------------------------------------------------------------

    def _start_match(self):
        """Queue a match run for every synced chart."""
        self._cancelling = False
        self._set_busy(True, cancellable=True)
        self._match_queue = [c.chart_key for c in self._charts if c.last_synced_week is not None]
        self._match_total = len(self._match_queue)
        self._run_next_match()

    def _run_next_match(self):
        """Start the next queued match run, or end the pipeline."""
        if not self._match_queue:
            self._finish_pipeline()
            return

        chart_key = self._match_queue.pop(0)
        self._match_chart_key = chart_key
        position = self._match_total - len(self._match_queue)
        self._match_queue_label = f"chart {position}/{self._match_total}"
        self.progress_bar.setRange(0, 0)
        self.status_label.setText(f"Matching {self._chart_name(chart_key)} ({self._match_queue_label})...")
        self._match_start_time = time.monotonic()
        self._match_worker = ChartMatchingWorker(self.controller, chart_key)
        self._match_worker.stage.connect(self._on_match_stage)
        self._match_worker.progress.connect(self._on_match_progress)
        self._match_worker.finished.connect(lambda stats, key=chart_key: self._on_match_finished(key, stats))
        self._match_worker.error.connect(self._on_worker_error)
        self._match_worker.start()

    def _on_match_stage(self, stage: str):
        """Show the matcher's current phase."""
        self.status_label.setText(f"{stage} ({self._chart_name(self._match_chart_key)}, {self._match_queue_label})")

    def _on_match_progress(self, scored: int, total: int, matched: int):
        """Show match progress, counts and an ETA."""
        self._on_progress(scored, total)
        eta = estimate_remaining(self._match_start_time, scored, total)
        eta_suffix = f", ETA: {eta}" if eta else ""
        self.status_label.setText(f"Matching {self._chart_name(self._match_chart_key)} ({self._match_queue_label}): {scored}/{total} scored, {matched} matched{eta_suffix}")

    def _on_match_finished(self, chart_key: str, stats):
        """Report one chart's match run and start the next one."""
        show_status_message(self, f"Matched {stats.matched}/{stats.total_unmatched} for {self._chart_name(chart_key)}")
        # See _on_import_finished: wait() before replacing self._match_worker.
        self._match_worker.wait()
        if not self._cancelling:
            self._run_next_match()

    # -----------------------------------------------------------------------
    # Chart playlist generation (Generate/Update Charts Playlists)
    # -----------------------------------------------------------------------

    def _start_playlist_generation(self):
        """Build or update the chart playlist trees on a worker."""
        # Not cancellable: the builder has no cancellation points.
        self._set_busy(True)
        self.status_label.setText("Generating charts playlists...")

        self._playlist_worker = ChartPlaylistWorker(self.controller)
        self._playlist_worker.progress.connect(self._on_progress)
        self._playlist_worker.finished.connect(self._on_playlist_finished)
        self._playlist_worker.error.connect(self._on_worker_error)
        self._playlist_worker.start()

    def _on_playlist_finished(self, stats):
        """Report the playlist run and reset the header."""
        show_status_message(
            self,
            f"Charts playlists updated: {stats.playlists_created} created, "
            f"{stats.playlists_updated} updated, {stats.playlists_removed} deleted, "
            f"{stats.tracks_added} track(s) added, {stats.tracks_removed} removed",
        )
        # See _on_import_finished: wait() before the worker can be replaced.
        self._playlist_worker.wait()
        self._set_busy(False)
        # Clears "Generating charts playlists..." and flips the button label to "Update".
        self.load_charts()

    # -----------------------------------------------------------------------
    # Shared error handling
    # -----------------------------------------------------------------------

    def _on_worker_error(self, message: str):
        """Stop the pipeline, reset the header, and show the error."""
        logger.error(f"Charts operation failed: {message}")
        self._import_queue = []
        self._match_queue = []
        self._cancel_poll.stop()
        self._set_busy(False)
        # Reset any in-progress header text so a failed run does not leave it stuck.
        self.load_charts()
        QMessageBox.warning(self, "Charts", f"Operation failed: {message}")
