"""
MtpListWorker — runs MtpManager.list_devices() off the GUI thread.

Opening SyncView used to enumerate MTP devices synchronously on the Qt GUI
thread: once in __init__ (via _rebuild_cards → connection badges), again in
_refresh_device_label, and then every 5 s from a QTimer. Each of those calls
shells out to `gio mount -li`, and a `gio` call against a wedged MTP backend
(phone asleep, gvfsd-mtp stuck on USB I/O) blocks its caller with no bounded
recovery — freezing the whole UI. This worker moves that call onto a
throwaway QThread; the result comes back on the GUI thread via `ready`.

No DB access, so no _release_db_session() is needed.
"""

from PySide6.QtCore import Signal

from src.common.cancellable_worker import CancellableWorker
from src.foundation.logger_config import logger
from src.sync.mtp_manager import MtpManager


class MtpListWorker(CancellableWorker):
    ready = Signal(list)  # list[MtpDevice]

    def __init__(self, mtp_manager: MtpManager | None = None):
        super().__init__()
        self._mtp = mtp_manager or MtpManager()

    def run(self):
        try:
            devices = self._mtp.list_devices()
        except Exception:
            # Broad boundary catch: this is a QThread run() loop and must not
            # let an unexpected error kill the thread silently. A failed scan
            # is reported as "no devices".
            logger.exception("MtpListWorker: list_devices() failed")
            devices = []
        if not self.is_cancelled:
            self.ready.emit(devices)
