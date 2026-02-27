"""
analysis_dialog.py

Audio Analysis Manager — a monitoring dashboard for the BatchAnalysisScheduler.

Responsibilities
----------------
* Show how many tracks still need analysis and which ones they are.
* Let the user start, pause, resume, and stop background analysis.
* Right-click any track in the list to force a re-analysis (clears cache entry).
* Stay out of the way: closing the dialog does NOT stop analysis.
* If analysis is already running when the dialog opens, reconnect to it and
  show live progress immediately.
"""

from PySide6.QtCore import Qt, Slot
from PySide6.QtWidgets import (
    QDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from src.analysis_utility import (
    BatchAnalysisScheduler,
    analysis_cache,
)
from src.logger_config import logger
from src.status_utility import StatusManager


# Fields that must be present and non-zero/None for a track to be
# considered fully analysed.  Extend this list if new metrics are added.
_REQUIRED_FIELDS = [
    "bpm",
    "key",
    "track_gain",
    "spectral_centroid",
    "dynamic_range",
    "energy",
    "danceability",
]


def _track_needs_analysis(track) -> bool:
    """Return True if any required audio field is missing or zero."""
    for field in _REQUIRED_FIELDS:
        val = getattr(track, field, None)
        if val is None or val == 0 or val == 0.0:
            return True
    return False


class AudioAnalysisDialog(QDialog):
    """
    Background Analysis Manager dialog.

    The dialog is a *monitor*.  It does not own the scheduler — the scheduler
    is passed in (or created fresh) so it can outlive the dialog being closed.
    """

    def __init__(
        self, controller, scheduler: BatchAnalysisScheduler | None = None, parent=None
    ):
        super().__init__(parent)
        self.controller = controller

        # Accept an externally-owned scheduler so a running analysis survives
        # the dialog being closed and reopened.
        if scheduler is not None:
            self._scheduler = scheduler
            self._owns_scheduler = False
        else:
            self._scheduler = BatchAnalysisScheduler(controller, num_workers=2)
            self._owns_scheduler = True

        self.setWindowTitle("Audio Analysis Manager")
        self.setMinimumSize(560, 520)
        self.setModal(False)  # Non-modal so the user can keep using the app

        self._tracks_pending: list = []  # tracks not yet in the cache
        self._track_id_to_item: dict = {}  # track_id → QListWidgetItem

        self._build_ui()
        self._connect_scheduler_signals()
        self._load_pending_tracks()

        # If the scheduler is already running (dialog was closed and reopened),
        # reflect the live state immediately.
        if self._scheduler.is_running:
            self._sync_running_state()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setSpacing(10)

        # --- Summary ---
        summary_group = QGroupBox("Library Status")
        summary_layout = QVBoxLayout(summary_group)

        self._summary_label = QLabel("Scanning library…")
        self._summary_label.setWordWrap(True)
        summary_layout.addWidget(self._summary_label)

        root.addWidget(summary_group)

        # --- Track list ---
        tracks_group = QGroupBox("Tracks Pending Analysis")
        tracks_layout = QVBoxLayout(tracks_group)

        self._list = QListWidget()
        self._list.setAlternatingRowColors(True)
        self._list.setContextMenuPolicy(Qt.CustomContextMenu)
        self._list.customContextMenuRequested.connect(self._show_context_menu)
        self._list.setToolTip(
            "Right-click a track to force re-analysis even if it is cached."
        )
        tracks_layout.addWidget(self._list)

        root.addWidget(tracks_group)

        # --- Settings ---
        settings_group = QGroupBox("Settings")
        settings_layout = QHBoxLayout(settings_group)

        settings_layout.addWidget(QLabel("Worker threads:"))
        self._workers_spin = QSpinBox()
        self._workers_spin.setRange(1, 8)
        self._workers_spin.setValue(2)
        self._workers_spin.setToolTip(
            "Number of parallel analysis workers.  More workers = faster but "
            "higher CPU and memory usage.  2 is a good default."
        )
        settings_layout.addWidget(self._workers_spin)
        settings_layout.addStretch()

        root.addWidget(settings_group)

        # --- Progress ---
        progress_group = QGroupBox("Progress")
        progress_layout = QVBoxLayout(progress_group)

        self._status_label = QLabel("Ready.")
        progress_layout.addWidget(self._status_label)

        self._progress_bar = QProgressBar()
        self._progress_bar.setMinimum(0)
        self._progress_bar.setValue(0)
        progress_layout.addWidget(self._progress_bar)

        self._progress_label = QLabel("0 / 0 tracks processed")
        progress_layout.addWidget(self._progress_label)

        root.addWidget(progress_group)

        # --- Buttons ---
        btn_layout = QHBoxLayout()

        self._start_btn = QPushButton("Start Analysis")
        self._start_btn.clicked.connect(self._on_start)
        btn_layout.addWidget(self._start_btn)

        self._pause_btn = QPushButton("Pause")
        self._pause_btn.clicked.connect(self._on_pause_resume)
        self._pause_btn.setEnabled(False)
        btn_layout.addWidget(self._pause_btn)

        self._stop_btn = QPushButton("Stop")
        self._stop_btn.clicked.connect(self._on_stop)
        self._stop_btn.setEnabled(False)
        btn_layout.addWidget(self._stop_btn)

        btn_layout.addStretch()

        self._close_btn = QPushButton("Close")
        self._close_btn.clicked.connect(self.close)
        btn_layout.addWidget(self._close_btn)

        root.addLayout(btn_layout)

    # ------------------------------------------------------------------
    # Data loading
    # ------------------------------------------------------------------

    def _load_pending_tracks(self):
        """
        Find tracks that need analysis.

        Fast path: if the track_id is already in the analysis cache, skip it
        without inspecting the DB fields at all.  Only for tracks NOT in the
        cache do we check individual fields — this keeps the scan fast even
        on large libraries.
        """
        try:
            all_tracks = self.controller.get.get_all_entities("Track")
        except Exception as e:
            logger.error(f"AudioAnalysisDialog: failed to load tracks — {e}")
            self._summary_label.setText("Error loading library.")
            return

        self._tracks_pending = []
        self._track_id_to_item = {}

        for track in all_tracks:
            tid = track.track_id
            if analysis_cache.is_analysed(tid):
                continue
            if _track_needs_analysis(track):
                self._tracks_pending.append(track)

        self._refresh_list()

    def _refresh_list(self):
        """Repopulate the QListWidget from self._tracks_pending."""
        self._list.clear()
        self._track_id_to_item = {}

        for track in self._tracks_pending:
            name = getattr(track, "track_name", "Unknown Title")
            artist = (
                track.artists[0].artist_name
                if getattr(track, "artists", None)
                else "Unknown Artist"
            )
            item = QListWidgetItem(f"{artist} — {name}")
            item.setData(Qt.UserRole, track.track_id)
            self._list.addItem(item)
            self._track_id_to_item[track.track_id] = item

        total_cached = analysis_cache.count()
        pending = len(self._tracks_pending)

        self._summary_label.setText(
            f"{total_cached} tracks already analysed in cache.  "
            f"{pending} track{'s' if pending != 1 else ''} pending."
        )
        self._progress_bar.setMaximum(max(pending, 1))
        self._progress_bar.setValue(0)
        self._progress_label.setText(f"0 / {pending} tracks processed")

        # Disable Start if nothing to do
        self._start_btn.setEnabled(pending > 0 and not self._scheduler.is_running)

    # ------------------------------------------------------------------
    # Scheduler signal wiring
    # ------------------------------------------------------------------

    def _connect_scheduler_signals(self):
        self._scheduler.signals.track_done.connect(self._on_track_done)
        self._scheduler.signals.batch_done.connect(self._on_batch_done)
        self._scheduler.signals.all_done.connect(self._on_all_done)
        self._scheduler.signals.error.connect(self._on_track_error)

    def _sync_running_state(self):
        """Update buttons/labels to match a scheduler that's already running."""
        completed, total = self._scheduler.progress
        self._set_running_ui(total)
        self._update_progress_display(completed, total)

    # ------------------------------------------------------------------
    # Button handlers
    # ------------------------------------------------------------------

    def _on_start(self):
        if not self._tracks_pending:
            QMessageBox.information(
                self, "Nothing to do", "All tracks are already analysed."
            )
            return

        # Recreate the scheduler if it finished or was stopped
        if not self._scheduler.is_running:
            if not self._owns_scheduler:
                # Don't replace an externally-owned scheduler; just restart it
                pass
            self._scheduler.num_workers = self._workers_spin.value()

        self._set_running_ui(len(self._tracks_pending))
        StatusManager.start_task(f"Analysing {len(self._tracks_pending)} tracks…")
        self._scheduler.start(self._tracks_pending)

    def _on_pause_resume(self):
        if self._scheduler.is_paused:
            self._scheduler.resume()
            self._pause_btn.setText("Pause")
            self._status_label.setText("Analysing…")
            StatusManager.show_message("Analysis resumed", 2000)
        else:
            self._scheduler.pause()
            self._pause_btn.setText("Resume")
            self._status_label.setText("Paused.")
            StatusManager.show_message("Analysis paused", 0)

    def _on_stop(self):
        reply = QMessageBox.question(
            self,
            "Stop Analysis",
            "Stop the current analysis?\n\n"
            "Progress is saved — the next run will continue where this one left off.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            self._scheduler.stop()
            self._set_idle_ui()
            StatusManager.end_task("Analysis stopped", 3000)

    # ------------------------------------------------------------------
    # Scheduler callbacks  (run on main thread via Qt signals)
    # ------------------------------------------------------------------

    @Slot(int, dict)
    def _on_track_done(self, track_id: int, metadata: dict):
        """Remove the finished track from the pending list display."""
        item = self._track_id_to_item.pop(track_id, None)
        if item:
            row = self._list.row(item)
            if row >= 0:
                self._list.takeItem(row)

        completed, total = self._scheduler.progress
        self._update_progress_display(completed, total)

    @Slot(int, int)
    def _on_batch_done(self, completed: int, total: int):
        self._update_progress_display(completed, total)

    @Slot(int)
    def _on_all_done(self, total: int):
        self._set_idle_ui()
        self._load_pending_tracks()  # Refresh list (should now be empty / much smaller)

        StatusManager.end_task(f"Analysis complete: {total} tracks processed", 5000)

        if self.isVisible():
            QMessageBox.information(
                self,
                "Analysis Complete",
                f"Audio analysis finished.\n{total} tracks processed.",
            )

    @Slot(int, str)
    def _on_track_error(self, track_id: int, message: str):
        logger.warning(f"Analysis error for track {track_id}: {message}")
        # Don't pop from display — a failed track may still need a retry

    # ------------------------------------------------------------------
    # Context menu — right-click force re-analyse
    # ------------------------------------------------------------------

    def _show_context_menu(self, pos):
        item = self._list.itemAt(pos)
        if item is None:
            return

        track_id = item.data(Qt.UserRole)
        menu = QMenu(self)

        reanalyse_action = menu.addAction("Force Re-analyse This Track")
        action = menu.exec(self._list.viewport().mapToGlobal(pos))

        if action == reanalyse_action:
            self._force_reanalyse(track_id)

    def _force_reanalyse(self, track_id: int):
        """Remove a track from the cache so it will be picked up next run."""
        analysis_cache.remove(track_id)

        # If it's not already in the pending list, reload so it appears
        pending_ids = {t.track_id for t in self._tracks_pending}
        if track_id not in pending_ids:
            self._load_pending_tracks()
        else:
            self._refresh_list()

        logger.info(f"Track {track_id} removed from cache — will be re-analysed")

        if self._scheduler.is_running:
            QMessageBox.information(
                self,
                "Track Queued",
                "This track will be re-analysed the next time you start analysis.\n"
                "(Stop and restart to include it in the current run.)",
            )

    # ------------------------------------------------------------------
    # UI state helpers
    # ------------------------------------------------------------------

    def _set_running_ui(self, total: int):
        self._start_btn.setEnabled(False)
        self._pause_btn.setEnabled(True)
        self._pause_btn.setText("Pause")
        self._stop_btn.setEnabled(True)
        self._workers_spin.setEnabled(False)
        self._progress_bar.setMaximum(max(total, 1))
        self._status_label.setText("Analysing…")

    def _set_idle_ui(self):
        self._start_btn.setEnabled(bool(self._tracks_pending))
        self._pause_btn.setEnabled(False)
        self._pause_btn.setText("Pause")
        self._stop_btn.setEnabled(False)
        self._workers_spin.setEnabled(True)
        self._status_label.setText("Ready.")

    def _update_progress_display(self, completed: int, total: int):
        self._progress_bar.setValue(completed)
        self._progress_label.setText(f"{completed} / {total} tracks processed")
        self._status_label.setText(
            f"Analysing… ({completed}/{total} complete)"
            if completed < total
            else "Finishing up…"
        )

    # ------------------------------------------------------------------
    # Close behaviour — analysis keeps running
    # ------------------------------------------------------------------

    def closeEvent(self, event):
        if self._scheduler.is_running:
            # Disconnect signals from this dialog instance so stale callbacks
            # don't try to update widgets that no longer exist.
            try:
                self._scheduler.signals.track_done.disconnect(self._on_track_done)
                self._scheduler.signals.batch_done.disconnect(self._on_batch_done)
                self._scheduler.signals.all_done.disconnect(self._on_all_done)
                self._scheduler.signals.error.disconnect(self._on_track_error)
            except RuntimeError:
                pass  # Already disconnected
        event.accept()
