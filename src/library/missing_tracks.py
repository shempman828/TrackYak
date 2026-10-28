from pathlib import Path

from PySide6.QtCore import Signal, Slot

from src.common.cancellable_worker import CancellableWorker
from src.core.status_widget import show_status_message
from src.foundation.logger_config import logger
from src.track.view.base_track_view import BaseTrackView

# Tracks checked between progress updates (and cancel checks).
PROGRESS_INTERVAL = 200


class MissingTracksWorker(CancellableWorker):
    """Find tracks whose file is missing on disk; emits progress(done, total), results_ready(missing_ids, cancelled), error(msg)."""

    progress = Signal(int, int)
    results_ready = Signal(list, bool)
    error = Signal(str)

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller

    def run(self):
        """Check every track's file, checking for cancel between batches."""
        try:
            # Plain ids and paths only: ORM objects from this thread's session must not cross to the UI thread.
            paths = [(t.track_id, t.track_file_path) for t in self.controller.get.get_all_entities("Track")]
            total = len(paths)
            missing_ids = []
            done = 0
            for start in range(0, total, PROGRESS_INTERVAL):
                if self.is_cancelled:
                    break
                batch = paths[start : start + PROGRESS_INTERVAL]
                missing_ids.extend(track_id for track_id, path in batch if not path or not Path(path).exists())
                done += len(batch)
                self.progress.emit(done, total)
            self.results_ready.emit(missing_ids, done < total)
        except Exception as e:
            logger.error("MissingTracksWorker failed: %s", e, exc_info=True)
            self.error.emit(str(e) or type(e).__name__)
        finally:
            self._release_db_session()


class MissingTracks:
    """Scans the library for tracks whose file no longer exists on disk and shows them in a BaseTrackView dialog."""

    def __init__(self, controller, parent=None):
        self.controller = controller
        self.parent = parent
        self._worker: MissingTracksWorker | None = None

    @property
    def is_running(self) -> bool:
        return self._worker is not None

    def start(self):
        """Start the scan in a worker thread; the dialog opens when it finishes."""
        if self._worker is not None:
            return
        self._status("Scanning for missing tracks…", duration=0)
        worker = MissingTracksWorker(self.controller)
        worker.progress.connect(self._on_progress)
        worker.results_ready.connect(self._on_results)
        worker.error.connect(self._on_error)
        self._worker = worker
        worker.start()

    def stop(self):
        """Cancel and join a running scan without opening the dialog."""
        worker, self._worker = self._worker, None
        if worker is None:
            return
        for signal in (worker.progress, worker.results_ready, worker.error):
            signal.disconnect()
        worker.request_cancel()
        # No timeout: a QThread destroyed while running aborts the app, and at most one batch is left.
        worker.wait()
        worker.deleteLater()

    def _release_worker(self):
        worker, self._worker = self._worker, None
        if worker is not None:
            worker.wait()
            worker.deleteLater()

    @Slot(int, int)
    def _on_progress(self, done: int, total: int):
        self._status(f"Scanning for missing tracks… {done} / {total}", duration=0)

    @Slot(list, bool)
    def _on_results(self, missing_ids: list, cancelled: bool):
        self._release_worker()
        if cancelled:
            return
        missing = self.controller.get.get_all_entities("Track", track_id__in=missing_ids) if missing_ids else []
        self.show_missing_tracks(missing)

    @Slot(str)
    def _on_error(self, message: str):
        self._release_worker()
        self._status(f"Missing track scan failed: {message}")

    def _status(self, message: str, duration: int = 4000):
        if self.parent is not None:
            show_status_message(self.parent, message, duration=duration)

    def show_missing_tracks(self, missing):
        """Open a BaseTrackView dialog listing `missing`, or show a status message if there are none."""
        if not missing:
            logger.info("No missing tracks found")
            self._status("No missing tracks found.")
            return None

        # Replace the duration=0 progress toast before the modal dialog opens.
        self._status(f"Found {len(missing)} missing track(s).")
        view = BaseTrackView(controller=self.controller, tracks=missing, title=f"Missing Tracks ({len(missing)})")
        view.exec()
        return view
