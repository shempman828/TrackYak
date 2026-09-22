"""
charts_view.py

Top-level Charts nav view: header controls (Download / Fetch Updates /
Match Now) driving the download -> import -> match pipeline, plus the two
browse tabs (Week Browser, Search). Worker wiring follows
src/sync/sync_execution_mixin.py's pattern (clicked -> build worker ->
connect progress/finished/error -> start()).
"""

import time

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QMessageBox, QProgressBar, QPushButton, QStackedWidget, QTabWidget, QVBoxLayout, QWidget
from sqlalchemy import func, select

from src.charts.chart_download import chart_csv_exists
from src.charts.chart_download_worker import ChartDownloadWorker
from src.charts.chart_import_worker import ChartImportWorker
from src.charts.chart_matching_worker import ChartMatchingWorker
from src.charts.chart_playlist_builder import CHART_PLAYLIST_MARKER_PREFIX
from src.charts.chart_playlist_worker import ChartPlaylistWorker
from src.charts.chart_recommendations_tab import ChartRecommendationsTab
from src.charts.chart_search_tab import ChartSearchTab
from src.charts.chart_week_browser_tab import ChartWeekBrowserTab
from src.common.eta_estimator import estimate_remaining
from src.common.widgets.detail_card import DetailCard
from src.common.widgets.style_utils import refresh_style
from src.db.db_tables.chart import ChartEntry
from src.foundation.logger_config import logger
from src.foundation.status_utility import show_status_message
from src.statistics.widgets.stat_tile import StatTileWidget


class ChartsView(QWidget):
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

        self.playlists_btn = QPushButton("Generate Charts Playlists")
        self.playlists_btn.clicked.connect(self._start_playlist_generation)
        header.addWidget(self.playlists_btn)

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

        # A QStackedWidget (rather than toggling .setVisible() on two
        # siblings in the same QVBoxLayout) keeps exactly one Expanding
        # child in the layout at all times -- with the tab widget simply
        # hidden, nothing was left to absorb the window's leftover vertical
        # space, so the header and the card drifted apart with a blank gap.
        self.content_stack = QStackedWidget()
        self.content_stack.addWidget(self.empty_state_card)
        self.content_stack.addWidget(self.tabs)
        layout.addWidget(self.content_stack)

    # -----------------------------------------------------------------------
    # State
    # -----------------------------------------------------------------------

    def load_charts(self):
        """Refresh header button visibility + tab data from current DB/disk
        state. Called on __init__ and (via main_window.py's revisit-refresh
        dispatch) whenever the user navigates back to this view."""
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

    def _refresh_primary_button(self, all_downloaded: bool) -> None:
        """The header's one primary (accent-filled) action is whichever of
        Download/Fetch is currently visible -- the "get data flowing" step
        -- so it stands out from the plainer Match Now/Playlists buttons."""
        self.download_btn.setObjectName("" if all_downloaded else "PrimaryButton")
        self.fetch_btn.setObjectName("PrimaryButton" if all_downloaded else "")
        refresh_style(self.download_btn)
        refresh_style(self.fetch_btn)

    def _refresh_stats(self, synced_charts, latest) -> None:
        chart_ids = [c.chart_id for c in synced_charts]
        session = self.controller.get.session
        total = session.scalar(select(func.count()).select_from(ChartEntry).where(ChartEntry.chart_id.in_(chart_ids))) or 0
        matched = (
            session.scalar(
                select(func.count()).select_from(ChartEntry).where(ChartEntry.chart_id.in_(chart_ids), ChartEntry.entity_id.isnot(None))
            )
            or 0
        )
        self.stat_synced.set_data(len(synced_charts), f"of {len(self._charts)} chart(s)")
        match_rate = f"{matched / total:.0%}" if total else "N/A"
        self.stat_match_rate.set_data(match_rate, f"{matched:,} / {total:,} entries")
        self.stat_updated.set_data(latest.strftime("%b %d, %Y") if latest else "N/A")

    def _refresh_playlists_btn_label(self):
        """Contextual label: "Generate Charts Playlists" until a chart-derived
        playlist tree exists, "Update Charts Playlists" once one does. Existence
        is keyed off the builder's marker in Playlist.playlist_description, the
        same signal ChartPlaylistBuilder uses for idempotent regeneration."""
        has_tree = bool(self.controller.get.get_all_entities("Playlist", playlist_description__startswith=CHART_PLAYLIST_MARKER_PREFIX))
        self.playlists_btn.setText("Update Charts Playlists" if has_tree else "Generate Charts Playlists")

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
        self._import_worker.finished.connect(lambda n, key=chart_key: self._on_import_finished(key, n))
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
        self._match_queue = [c.chart_key for c in self._charts if c.last_synced_week is not None]
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
        self.status_label.setText(f"Matching {chart_key} ({self._match_queue_label})...")
        self._match_start_time = time.monotonic()
        self._match_worker = ChartMatchingWorker(self.controller, chart_key)
        self._match_worker.stage.connect(self._on_match_stage)
        self._match_worker.progress.connect(self._on_match_progress)
        self._match_worker.finished.connect(lambda stats, key=chart_key: self._on_match_finished(key, stats))
        self._match_worker.error.connect(self._on_worker_error)
        self._match_worker.start()

    def _on_match_stage(self, stage: str):
        self.status_label.setText(f"{stage} ({self._match_chart_key}, {self._match_queue_label})")

    def _on_match_progress(self, scored: int, total: int, matched: int):
        if total > 0:
            self.progress_bar.setRange(0, total)
            self.progress_bar.setValue(scored)
        eta = estimate_remaining(self._match_start_time, scored, total)
        eta_suffix = f", ETA: {eta}" if eta else ""
        self.status_label.setText(f"Matching {self._match_chart_key} ({self._match_queue_label}): {scored}/{total} scored, {matched} matched{eta_suffix}")

    def _on_match_finished(self, chart_key: str, stats):
        show_status_message(self, f"Matched {stats.matched}/{stats.total_unmatched} for {chart_key}")
        # See _on_import_finished: finished fires before the thread has fully
        # unwound, so wait() here before reassigning self._match_worker.
        self._match_worker.wait()
        self._run_next_match()

    # -----------------------------------------------------------------------
    # Chart playlist generation (Generate/Update Charts Playlists)
    # -----------------------------------------------------------------------

    def _start_playlist_generation(self):
        self.download_btn.setEnabled(False)
        self.fetch_btn.setEnabled(False)
        self.match_btn.setEnabled(False)
        self.playlists_btn.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 0)
        self.status_label.setText("Generating charts playlists...")

        self._playlist_worker = ChartPlaylistWorker(self.controller)
        self._playlist_worker.progress.connect(self._on_playlist_progress)
        self._playlist_worker.finished.connect(self._on_playlist_finished)
        self._playlist_worker.error.connect(self._on_worker_error)
        self._playlist_worker.start()

    def _on_playlist_progress(self, done: int, total: int):
        if total > 0:
            self.progress_bar.setRange(0, total)
            self.progress_bar.setValue(done)

    def _on_playlist_finished(self, stats):
        show_status_message(
            self,
            f"Charts playlists updated: {stats.playlists_created} created, "
            f"{stats.playlists_updated} updated, {stats.playlists_removed} deleted, "
            f"{stats.tracks_added} track(s) added, {stats.tracks_removed} removed",
        )
        # See _on_import_finished: finished fires before the thread has fully
        # unwound, so wait() here before reassigning self._playlist_worker.
        self._playlist_worker.wait()
        self.progress_bar.setVisible(False)
        self.download_btn.setEnabled(True)
        self.fetch_btn.setEnabled(True)
        self.match_btn.setEnabled(True)
        self.playlists_btn.setEnabled(True)
        # Clear the "Generating charts playlists..." header text and (via the
        # same call) flip the button label to "Update" now the tree exists --
        # same end-of-pipeline reset the import/match handlers do.
        self.load_charts()

    # -----------------------------------------------------------------------
    # Shared error handling
    # -----------------------------------------------------------------------

    def _on_worker_error(self, message: str):
        logger.error(f"Charts operation failed: {message}")
        self.progress_bar.setVisible(False)
        self.download_btn.setEnabled(True)
        self.fetch_btn.setEnabled(True)
        self.match_btn.setEnabled(True)
        self.playlists_btn.setEnabled(True)
        # Reset any in-progress header text (e.g. "Generating charts
        # playlists...") so a failed run doesn't leave it stuck.
        self.load_charts()
        QMessageBox.warning(self, "Charts", f"Operation failed: {message}")
