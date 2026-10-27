"""Write each track's changed database tags to its file, off the UI thread."""

from PySide6.QtCore import Signal

from src.common.cancellable_worker import CancellableWorker
from src.foundation.logger_config import logger
from src.metadata.writers.metadata_writer import MetadataWriter


class WriteMetadataWorker(CancellableWorker):
    """Sync tags for (track_id, track_name) pairs; emits progress(done, total), results_ready(results, cancelled), error(msg).

    `results` is a list of (track_name, sync result dict) for the tracks processed before any cancel.
    """

    progress = Signal(int, int)
    results_ready = Signal(list, bool)
    error = Signal(str)

    def __init__(self, controller, tracks: list[tuple[int, str]], parent=None):
        super().__init__(parent)
        self.controller = controller
        # Plain ids and names only: ORM objects from the UI thread's session must not cross threads.
        self._tracks = tracks

    def run(self):
        """Sync each track in turn, checking for cancel between tracks."""
        try:
            writer = MetadataWriter(self.controller)
            total = len(self._tracks)
            results = []
            for track_id, track_name in self._tracks:
                if self.is_cancelled:
                    break
                results.append((track_name, writer.sync_metadata_to_track(track_id)))
                self.progress.emit(len(results), total)
            self.results_ready.emit(results, len(results) < total)
        except Exception as e:
            logger.error("WriteMetadataWorker failed: %s", e, exc_info=True)
            self.error.emit(str(e) or type(e).__name__)
        finally:
            self._release_db_session()
