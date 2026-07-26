"""
BatchAnalysisScheduler — background thread manager.

Accepts a list of tracks, processes them with a configurable worker count,
saves cache every 25 tracks, and fires a progress callback on the main
thread via Qt signals.
"""

import os
import queue
import threading

from PySide6.QtCore import QObject, Signal

from src.core.logger_config import logger
from src.statistics.analysis_cache import CACHE_SAVE_INTERVAL, analysis_cache
from src.statistics.audio_calculations import AudioCalculations


def max_worker_count() -> int:
    """Return the number of CPU cores available, for sizing UI controls."""
    return os.cpu_count() or 2


def recommended_worker_count() -> int:
    """Return a safe default analysis worker count for this machine.

    Leaves one core free for the UI/OS so a full batch run doesn't stall
    playback or the rest of the app.
    """
    return max(1, max_worker_count() - 1)


class _SchedulerSignals(QObject):
    """Qt signals for cross-thread communication."""

    track_done = Signal(int, dict)  # track_id, metadata
    batch_done = Signal(int, int)  # completed_so_far, total
    all_done = Signal(int)  # total tracks processed
    error = Signal(int, str)  # track_id, error message


class BatchAnalysisScheduler:
    """
    Manages a pool of worker threads that pull tracks from a queue and run
    AudioCalculations.run_all() on each one.

    Usage
    -----
        scheduler = BatchAnalysisScheduler(controller)  # uses recommended_worker_count()
        scheduler.start(tracks)           # non-blocking
        scheduler.pause()
        scheduler.resume()
        scheduler.stop()                  # graceful stop; saves cache

    Signals (via .signals)
    ----------------------
        track_done(track_id, metadata)
        batch_done(completed, total)
        all_done(total)
        error(track_id, message)
    """

    def __init__(self, controller, num_workers: int | None = None):
        self.controller = controller
        self.num_workers = num_workers if num_workers is not None else recommended_worker_count()

        self.signals = _SchedulerSignals()
        self._queue: queue.Queue = queue.Queue()
        self._workers: list[_AnalysisWorker] = []
        self._lock = threading.Lock()

        self._total = 0
        self._completed = 0
        self._running = False
        self._paused = False
        self._pause_event = threading.Event()
        self._pause_event.set()  # not paused initially

    # ------------------------------------------------------------------
    # Public control methods
    # ------------------------------------------------------------------

    def start(self, tracks: list):
        """
        Populate the queue with tracks and spin up worker threads.
        Tracks already in the cache are skipped automatically.
        """
        with self._lock:
            if self._running:
                logger.warning("BatchAnalysisScheduler: already running")
                return

            # Filter out cached tracks
            pending = [t for t in tracks if not analysis_cache.is_analysed(t.track_id)]
            if not pending:
                logger.info("BatchAnalysisScheduler: all tracks already analysed")
                self.signals.all_done.emit(0)
                return

            self._total = len(pending)
            self._completed = 0
            self._running = True
            self._paused = False
            self._pause_event.set()

            for track in pending:
                self._queue.put(track)

            # Sentinel values so workers know when to exit
            for _ in range(self.num_workers):
                self._queue.put(None)

            self._workers = []
            for i in range(self.num_workers):
                w = _AnalysisWorker(
                    worker_id=i,
                    task_queue=self._queue,
                    pause_event=self._pause_event,
                    controller=self.controller,
                    on_done=self._on_track_done,
                    on_error=self._on_track_error,
                )
                w.start()
                self._workers.append(w)

            logger.info(
                f"BatchAnalysisScheduler: started {self.num_workers} workers "
                f"for {self._total} tracks"
            )

    def pause(self):
        with self._lock:
            if self._running and not self._paused:
                self._paused = True
                self._pause_event.clear()
                logger.info("BatchAnalysisScheduler: paused")

    def resume(self):
        with self._lock:
            if self._running and self._paused:
                self._paused = False
                self._pause_event.set()
                logger.info("BatchAnalysisScheduler: resumed")

    def stop(self):
        """
        Signal all workers to stop after their current track finishes,
        then save the cache.
        """
        with self._lock:
            self._running = False
            self._pause_event.set()  # unblock if paused

            for w in self._workers:
                w.stop()

            # Drain the queue so workers can exit
            while not self._queue.empty():
                try:
                    self._queue.get_nowait()
                except queue.Empty:
                    break

        analysis_cache.save()
        logger.info("BatchAnalysisScheduler: stopped and cache saved")

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def is_paused(self) -> bool:
        return self._paused

    @property
    def progress(self) -> tuple[int, int]:
        """Return (completed, total)."""
        return self._completed, self._total

    # ------------------------------------------------------------------
    # Internal callbacks (called from worker threads)
    # ------------------------------------------------------------------

    def _on_track_done(self, track_id: int, metadata: dict):
        with self._lock:
            self._completed += 1
            completed = self._completed
            total = self._total

        analysis_cache.mark_analysed(track_id)
        self.signals.track_done.emit(track_id, metadata)

        if completed % CACHE_SAVE_INTERVAL == 0:
            self.signals.batch_done.emit(completed, total)

        if completed >= total:
            analysis_cache.save()
            self._running = False
            self.signals.all_done.emit(total)
            logger.info(f"BatchAnalysisScheduler: all {total} tracks complete")

    def _on_track_error(self, track_id: int, message: str):
        with self._lock:
            self._completed += 1

        self.signals.error.emit(track_id, message)


class _AnalysisWorker(threading.Thread):
    """
    Pulls tracks from the shared queue, runs AudioCalculations.run_all(),
    and writes results to the database via the controller.
    """

    def __init__(
        self,
        worker_id: int,
        task_queue: queue.Queue,
        pause_event: threading.Event,
        controller,
        on_done,
        on_error,
    ):
        super().__init__(daemon=True, name=f"AnalysisWorker-{worker_id}")
        self.worker_id = worker_id
        self._queue = task_queue
        self._pause_event = pause_event
        self.controller = controller
        self._on_done = on_done
        self._on_error = on_error
        self._stop_flag = False

    def stop(self):
        self._stop_flag = True

    def run(self):
        logger.debug(f"{self.name}: started")
        while not self._stop_flag:
            self._pause_event.wait()  # blocks if paused

            try:
                track = self._queue.get(timeout=1.0)
            except queue.Empty:
                continue

            if track is None:  # sentinel → no more work
                break

            if self._stop_flag:
                self._queue.task_done()
                break

            try:
                self._process(track)
            finally:
                self._queue.task_done()

        logger.debug(f"{self.name}: exiting")

    def _process(self, track):
        try:
            logger.info(f"{self.name}: analysing {track.track_file_path}")
            calc = AudioCalculations(track.track_file_path)
            metadata = calc.run_all()  # releases memory inside

            self.controller.update.update_entity("Track", track.track_id, **metadata)
            self._on_done(track.track_id, metadata)

        except Exception as e:
            msg = f"Worker error on track {track.track_id}: {e}"
            logger.error(msg, exc_info=True)
            self._on_error(track.track_id, msg)
