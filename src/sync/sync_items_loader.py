"""
SyncItemsLoader — runs SyncManager.get_playlists() / get_moods() off the GUI
thread.

`SyncSelectionMixin._refresh_sync_items()` used to call both synchronously on
the Qt GUI thread, from `SyncView.__init__` *and* from `showEvent` (i.e. every
time the Sync tab is shown). Even as one grouped query per kind that is still a
DB round trip blocking the UI for no reason; this moves it to a throwaway
QThread and delivers the result on the GUI thread via `loaded`.

`SyncManager` is constructed with the scoped_session proxy (see
`SyncView.__init__`), so `get_playlists()` here resolves to *this* worker
thread's own Session — `_release_db_session()` in the `finally` is mandatory
(see `cancellable_worker.py`: an un-removed read-only scoped session pins its
pooled connection forever once the thread dies, eventually exhausting the pool).
"""

from PySide6.QtCore import Signal

from src.common.cancellable_worker import CancellableWorker
from src.foundation.logger_config import logger


class SyncItemsLoader(CancellableWorker):
    loaded = Signal(list, list)  # (playlists, moods) — each a list[dict]
    failed = Signal(str)

    def __init__(self, sync_manager):
        super().__init__()
        self._sync_manager = sync_manager

    def run(self):
        try:
            playlists = self._sync_manager.get_playlists()
            moods = self._sync_manager.get_moods()
            if not self.is_cancelled:
                self.loaded.emit(playlists, moods)
        except Exception as e:
            # Broad boundary catch: a QThread run() body has no caller frame,
            # so any error must become the `failed` signal rather than kill the
            # thread and leave the tree wedged on its old contents forever.
            logger.exception("SyncItemsLoader: loading playlists/moods failed")
            if not self.is_cancelled:
                self.failed.emit(str(e))
        finally:
            self._release_db_session()
