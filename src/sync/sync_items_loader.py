"""SyncItemsLoader: runs SyncManager.get_playlists() / get_moods() off the GUI thread."""

# SyncManager holds the scoped_session proxy, so the queries here open this thread's own Session;
# _release_db_session() in `finally` is mandatory or the pooled connection stays pinned.

from PySide6.QtCore import Signal

from src.common.cancellable_worker import CancellableWorker
from src.foundation.logger_config import logger


class SyncItemsLoader(CancellableWorker):
    """Load the selection tree's playlists and moods; emit `loaded` or `failed`."""

    loaded = Signal(list, list)  # (playlists, moods) — each a list[dict]
    failed = Signal(str)

    def __init__(self, sync_manager):
        super().__init__()
        self._sync_manager = sync_manager

    def run(self):
        """Query both lists, then release this thread's DB session."""
        try:
            playlists = self._sync_manager.get_playlists()
            moods = self._sync_manager.get_moods()
            if not self.is_cancelled:
                self.loaded.emit(playlists, moods)
        except Exception as e:
            # Broad boundary catch: an error must become `failed`, not kill the thread silently.
            logger.exception("SyncItemsLoader: loading playlists/moods failed")
            if not self.is_cancelled:
                self.failed.emit(str(e))
        finally:
            self._release_db_session()
