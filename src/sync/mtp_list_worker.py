"""MtpListWorker: runs MtpManager.list_devices() off the GUI thread."""

# `gio` against a wedged MTP backend can block its caller, so it never runs on the GUI thread.
# No DB access, so no _release_db_session() is needed.

from PySide6.QtCore import Signal

from src.common.cancellable_worker import CancellableWorker
from src.foundation.logger_config import logger
from src.sync.mtp_manager import MtpManager


class MtpListWorker(CancellableWorker):
    """List connected MTP devices once and emit them via `ready`."""

    ready = Signal(list)  # list[MtpDevice]

    def __init__(self, mtp_manager: MtpManager | None = None):
        super().__init__()
        self._mtp = mtp_manager or MtpManager()

    def run(self):
        """Scan once; a failed scan is reported as no devices."""
        try:
            devices = self._mtp.list_devices()
        except Exception:
            # Broad boundary catch: an error must not kill this QThread silently.
            logger.exception("MtpListWorker: list_devices() failed")
            devices = []
        if not self.is_cancelled:
            self.ready.emit(devices)
