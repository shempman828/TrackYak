"""SyncWorker: runs a profile's sync (folder or MTP) on a background thread."""

from PySide6.QtCore import Signal

from src.common.cancellable_worker import CancellableWorker
from src.foundation.config_setup import app_config
from src.foundation.logger_config import logger
from src.foundation.status_utility import StatusManager
from src.sync.sync_manager import SyncManager
from src.sync.sync_profile import SyncProfile

# Each playlist/mood gets this many progress units, so per-track progress maps onto one overall bar.
PROGRESS_UNITS_PER_ITEM = 1000


class SyncWorker(CancellableWorker):
    """Sync each selected playlist/mood, then trim the transcode cache and prune, emitting results as it goes."""

    progress = Signal(int, int, str)  # current, total, message (overall run)
    playlist_complete = Signal(dict)  # one playlist result
    prune_complete = Signal(dict)  # prune result (only if profile.prune_untracked)
    notice = Signal(str)  # a non-fatal problem worth showing in the log (e.g. clear failed)
    failed = Signal(str)  # the run stopped on an unexpected error
    # Not named `finished`: that would shadow QThread.finished.
    sync_finished = Signal(list)  # all results

    def __init__(self, sync_manager: SyncManager, playlists: list[dict], profile: SyncProfile):
        super().__init__()
        self.sync_manager = sync_manager
        self.playlists = playlists
        self.profile = profile
        self.results = []
        self.prune_result: dict | None = None
        self._item_index = 0

    def run(self):
        """Clear (optional), sync every item, trim the cache, prune (optional); always emits sync_finished."""
        try:
            profile = self.profile
            total_units = max(1, len(self.playlists)) * PROGRESS_UNITS_PER_ITEM

            if profile.clear_before_sync and not self.is_cancelled:
                self.progress.emit(0, total_units, "Clearing destination…")
                errors = self.sync_manager.clear_mtp_folders(profile.device_uri, profile.music_path) if profile.is_mtp else self.sync_manager.clear_device_folder(profile.path)
                for error in errors or []:
                    self.notice.emit(error)

            # A track shared by several selected playlists/moods is transcoded and copied once per run.
            self.sync_manager.begin_sync_run()
            for i, playlist in enumerate(self.playlists):
                if self.is_cancelled:
                    StatusManager.show_message("Sync cancelled", 3000)
                    break

                self._item_index = i
                self.progress.emit(i * PROGRESS_UNITS_PER_ITEM, total_units, f"Starting: {playlist['name']}")
                StatusManager.show_message(f"Syncing: {playlist['name']}", 0)

                common = {"should_cancel": lambda: self.is_cancelled, "transcode_to_mp3": profile.transcode_to_mp3, "transcode_bitrate": profile.transcode_bitrate}
                if profile.is_mtp:
                    result = self.sync_manager.sync_playlist_to_mtp(playlist, profile.device_uri, profile.music_path, self._progress_callback, **common)
                else:
                    result = self.sync_manager.sync_playlist_to_device(playlist, profile.path, self._progress_callback, **common)

                self.results.append(result)
                self.playlist_complete.emit(result)

            # Only after a run that wasn't cancelled mid-way, so fresh cache hits have settled mtimes.
            if profile.transcode_to_mp3 and not self.is_cancelled:
                self._enforce_transcode_cache_limit()

            # A half-finished (cancelled) run is not the moment to start deleting.
            if profile.prune_untracked and not self.is_cancelled:
                self.progress.emit(total_units, total_units, "Removing files no longer in this profile…")
                self.prune_result = self.sync_manager.prune_device(profile, self.playlists, should_cancel=lambda: self.is_cancelled)
                self.prune_complete.emit(self.prune_result)

        except Exception as e:
            # Broad boundary catch: an exception must not kill this QThread silently.
            logger.exception("SyncWorker error")
            self.failed.emit(str(e) or type(e).__name__)
        finally:
            self.sync_finished.emit(self.results)
            # sync_manager resolves a Session per calling thread; release the one this thread opened.
            self._release_db_session()

    def _enforce_transcode_cache_limit(self):
        """LRU-evict the transcode cache down to `sync.transcode_cache_max_mb`."""
        max_bytes = app_config.get_transcode_cache_max_mb() * 1024 * 1024
        try:
            outcome = self.sync_manager.transcode_cache.enforce_limit(max_bytes)
        except OSError:
            logger.exception("Transcode cache cleanup failed")
            return
        if outcome["evicted"] or outcome["swept_parts"]:
            logger.info(f"Transcode cache trimmed: {outcome['evicted']} evicted ({outcome['freed_bytes'] / (1024 * 1024):.0f} MB), {outcome['swept_parts']} stale temp(s) swept")

    def _progress_callback(self, current: int, total: int, message: str):
        """Map one item's (current, total) onto the whole run's progress scale."""
        fraction = min(1.0, current / total) if total > 0 else 0.0
        overall = int((self._item_index + fraction) * PROGRESS_UNITS_PER_ITEM)
        self.progress.emit(overall, max(1, len(self.playlists)) * PROGRESS_UNITS_PER_ITEM, message)

    def cancel(self):
        """Alias for request_cancel()."""
        self.request_cancel()
