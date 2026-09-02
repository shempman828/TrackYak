"""
BatchAnalysisScheduler — background process pool manager.

Accepts a list of tracks, processes them with a configurable worker count,
saves cache every 25 tracks, and fires a progress callback on the main
thread via Qt signals.
"""

import concurrent.futures
import multiprocessing
import os
import queue
import threading

from PySide6.QtCore import QObject, Signal
from sqlalchemy.exc import SQLAlchemyError

from src.foundation.logger_config import logger
from src.statistics.album_gain_peak import recompute_album_gain_peak
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


def _analyze_track_worker(
    track_id: int, track_file_path: str
) -> tuple[int, dict | None, str | None]:
    """
    Entry point run inside a worker *process* (see BatchAnalysisScheduler).

    Only picklable plain values may cross the process boundary in either
    direction — no SQLAlchemy session, no Qt object, no analysis_cache
    singleton. Those all stay on the coordinator thread in the main process.
    """
    try:
        calc = AudioCalculations(track_file_path)
        metadata = calc.run_all()
        return track_id, metadata, None
    except Exception as e:
        # Intentional broad boundary catch: runs in a worker process, so only
        # picklable plain values may cross back — return the error as a string
        # for the coordinator to log rather than raising into the pool machinery.
        return track_id, None, str(e)


class _SchedulerSignals(QObject):
    """Qt signals for cross-thread communication."""

    track_done = Signal(int, dict)  # track_id, metadata
    batch_done = Signal(int, int)  # completed_so_far, total
    all_done = Signal(int)  # total tracks processed
    albums_updated = Signal(list)  # album_ids whose gain/peak were recomputed
    error = Signal(int, str)  # track_id, error message


class BatchAnalysisScheduler:
    """
    Manages a pool of worker *processes* that run AudioCalculations.run_all()
    on each track, coordinated by a lightweight thread in the main process.

    Analysis is partly CPU-bound pure-Python work (onset/envelope framing,
    autocorrelation, etc. alongside the vectorised numpy/scipy calls), so
    real OS processes are used instead of threads — threads would serialize
    on the GIL for those sections and never approach num_workers-way
    parallelism. Workers use the 'spawn' start method rather than fork, since
    forking a process that already has Qt/PySide6 initialised (internal
    threads, mutexes) is a known source of rare, hard-to-diagnose deadlocks.

    Only a (track_id, file_path) pair crosses into a worker process, and
    only a metadata dict (or an error string) comes back. DB writes, the
    analysis_cache, and Qt signal emission all happen on the coordinator
    thread here, in the main process — exactly as before, just no longer on
    the same thread that decoded/analysed the audio.

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
        self._pending: queue.Queue = queue.Queue()
        self._lock = threading.Lock()

        self._executor: concurrent.futures.ProcessPoolExecutor | None = None
        self._coordinator: threading.Thread | None = None

        self._total = 0
        self._completed = 0
        self._track_album_ids: dict[int, int | None] = {}
        self._dirty_albums: set[int] = set()
        self._running = False
        self._paused = False
        self._stop_flag = False
        self._pause_event = threading.Event()
        self._pause_event.set()  # not paused initially

    # ------------------------------------------------------------------
    # Public control methods
    # ------------------------------------------------------------------

    def start(self, tracks: list, ignore_cache: bool = False):
        """
        Populate the queue with tracks and spin up a worker process pool.
        Tracks already in the cache are skipped automatically, unless
        `ignore_cache` is set (e.g. the dialog's "re-analyze all" option).
        """
        with self._lock:
            if self._running:
                logger.warning("BatchAnalysisScheduler: already running")
                return

            pending = (
                list(tracks)
                if ignore_cache
                else [t for t in tracks if not analysis_cache.is_analysed(t.track_id)]
            )
            if not pending:
                logger.info("BatchAnalysisScheduler: all tracks already analysed")
                self.signals.all_done.emit(0)
                return

            self._total = len(pending)
            self._completed = 0
            self._track_album_ids = {
                track.track_id: getattr(track, "album_id", None) for track in pending
            }
            self._dirty_albums = set()
            self._running = True
            self._paused = False
            self._stop_flag = False
            self._pause_event.set()

            for track in pending:
                self._pending.put((track.track_id, track.track_file_path))

            self._executor = concurrent.futures.ProcessPoolExecutor(
                max_workers=self.num_workers, mp_context=multiprocessing.get_context("spawn")
            )
            self._coordinator = threading.Thread(
                target=self._coordinate, daemon=True, name="AnalysisCoordinator"
            )
            self._coordinator.start()

            logger.info(
                f"BatchAnalysisScheduler: started {self.num_workers} worker processes "
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
        Signal the coordinator to stop submitting new work. Tracks already
        in flight in a worker process are left to finish and are still
        recorded (matching the previous thread-pool behaviour, where a
        worker's current track always ran to completion before exiting).
        """
        with self._lock:
            self._stop_flag = True
            self._running = False
            self._pause_event.set()  # unblock if paused

            # Drain anything not yet dispatched to a worker
            while not self._pending.empty():
                try:
                    self._pending.get_nowait()
                except queue.Empty:
                    break

        if self._executor is not None:
            self._executor.shutdown(wait=False, cancel_futures=True)

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
    # Internal callbacks (called from the coordinator thread)
    # ------------------------------------------------------------------

    def _on_track_done(self, track_id: int, metadata: dict):
        with self._lock:
            self._completed += 1
            completed = self._completed
            total = self._total

        analysis_cache.mark_analysed(track_id)
        album_id = self._track_album_ids.get(track_id)
        if album_id is not None:
            self._dirty_albums.add(album_id)
        self.signals.track_done.emit(track_id, metadata)

        if completed % CACHE_SAVE_INTERVAL == 0:
            self.signals.batch_done.emit(completed, total)

        if completed >= total:
            analysis_cache.save()
            self._running = False
            self._recompute_dirty_albums()
            self.signals.all_done.emit(total)
            logger.info(f"BatchAnalysisScheduler: all {total} tracks complete")

    def _recompute_dirty_albums(self):
        """Roll freshly analyzed track_gain/track_peak values up into
        album_gain/album_peak for every album touched by this batch."""
        albums = sorted(self._dirty_albums)
        self._dirty_albums = set()
        for album_id in albums:
            try:
                recompute_album_gain_peak(self.controller, album_id)
            except SQLAlchemyError as e:
                logger.error(f"Failed to recompute album gain/peak for album {album_id}: {e}")
        if albums:
            self.signals.albums_updated.emit(albums)

    def _on_track_error(self, track_id: int, message: str):
        with self._lock:
            self._completed += 1

        self.signals.error.emit(track_id, message)

    # ------------------------------------------------------------------
    # Coordinator (runs on its own thread in the main process)
    # ------------------------------------------------------------------

    def _coordinate(self):
        """
        Keeps up to num_workers futures in flight in the process pool, and
        handles each one (DB write, cache update, signal emit) as it
        completes. None of that ever runs inside a worker process.
        """
        in_flight: dict[concurrent.futures.Future, tuple[int, str]] = {}

        try:
            while True:
                if not self._stop_flag:
                    while len(in_flight) < self.num_workers:
                        if not self._pause_event.wait(timeout=1.0):
                            break  # still paused
                        try:
                            track_id, file_path = self._pending.get_nowait()
                        except queue.Empty:
                            break
                        future = self._executor.submit(_analyze_track_worker, track_id, file_path)
                        in_flight[future] = (track_id, file_path)

                if not in_flight:
                    break  # nothing left to do and nothing pending/in flight

                done, _ = concurrent.futures.wait(
                    in_flight.keys(), timeout=1.0, return_when=concurrent.futures.FIRST_COMPLETED
                )
                for future in done:
                    track_id, file_path = in_flight.pop(future)
                    self._handle_result(track_id, file_path, future)
        finally:
            # This thread's DB writes (update_entity, recompute_album_gain_peak)
            # commit as they go, so this isn't a pinned-connection leak like
            # the read-only workers -- but the scoped_session registry entry
            # for this (now-dead) thread is otherwise never cleaned up either.
            # See CancellableWorker's _release_db_session docstring.
            from src.db.db_engine import Session

            Session.remove()

        logger.debug("AnalysisCoordinator: exiting")

    def _handle_result(self, track_id: int, file_path: str, future: concurrent.futures.Future):
        try:
            _, metadata, error = future.result()
        except Exception as e:
            # Intentional broad boundary catch: covers worker-process failures
            # (e.g. a crashed/broken process pool) that _analyze_track_worker
            # itself can't hand back as a normal (track_id, metadata, error) tuple.
            logger.exception(f"Worker process failure for track {track_id}")
            metadata, error = None, str(e)

        if error is not None or metadata is None:
            msg = f"Worker error on track {file_path} (id={track_id}): {error}"
            logger.error(msg)
            self._on_track_error(track_id, msg)
            return

        try:
            self.controller.update.update_entity("Track", track_id, **metadata)
        except SQLAlchemyError as e:
            msg = f"DB write failed for track {file_path} (id={track_id}): {e}"
            logger.error(msg, exc_info=True)
            self._on_track_error(track_id, msg)
            return

        self._on_track_done(track_id, metadata)
