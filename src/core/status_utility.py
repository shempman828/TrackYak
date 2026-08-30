# status_manager.py
from PySide6.QtCore import QMutex, QMutexLocker, QObject, QTimer, Signal

from src.core.logger_config import logger


class _StatusManager(QObject):
    show_status = Signal(str, int)
    hide_status = Signal()

    _instance = None
    _lock = QMutex()  # The lock

    def __new__(cls):
        # 2. Wrap the lock with QMutexLocker
        with QMutexLocker(cls._lock):
            if cls._instance is None:
                cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        super().__init__()
        self._active_tasks = 0
        self._message_queue = []
        self._current_message = ""
        self._auto_hide_timer = QTimer()
        self._auto_hide_timer.setSingleShot(True)
        self._auto_hide_timer.timeout.connect(self._check_and_hide)

    def start_task(self, message=""):
        """Start a background task and show status if provided"""
        self._active_tasks += 1
        if message:
            self.show_message(message, 0)  # Persistent until task ends
        logger.debug(f"Task started. Active tasks: {self._active_tasks}")

    def end_task(self, completion_message="", duration=3000):
        """End a background task and optionally show completion message"""
        self._active_tasks = max(0, self._active_tasks - 1)

        if completion_message:
            self.show_message(completion_message, duration)
        else:
            self._check_and_hide()

        logger.debug(f"Task ended. Active tasks: {self._active_tasks}")

    def show_message(self, message, duration=3000):
        """Show a status message with optional auto-hide duration"""
        logger.debug(
            f"DEBUG: StatusManager.show_message: '{message}', duration: {duration}"
        )
        self._current_message = message
        self.show_status.emit(message, duration)

        # Set up auto-hide for non-persistent messages
        if duration > 0:
            self._auto_hide_timer.start(duration)
        else:
            self._auto_hide_timer.stop()

        logger.debug(f"Status message: {message} (duration: {duration})")

    def _check_and_hide(self):
        """Hide status bar if no active tasks and no persistent messages"""
        if self._active_tasks == 0 and not self._auto_hide_timer.isActive():
            self.hide_status.emit()
            self._current_message = ""
            logger.debug("Status bar hidden - no active tasks")


StatusManager = _StatusManager()


def show_status_message(widget, message: str, duration: int = 4000):
    """
    Show a non-blocking toast for `message`, replacing an intrusive QMessageBox.

    Anchors to whichever top-level window is actually on screen for `widget`:
    if that's a modal QDialog, the toast gets a local StatusBarWidget owned by
    the dialog (so it's visible while the dialog is open, instead of hiding
    behind it on the main window). Otherwise it goes through the shared
    StatusManager/main-window status bar as usual.
    """
    from PySide6.QtWidgets import QDialog

    from src.core.status_widget import StatusBarWidget

    window = widget.window()
    if not isinstance(window, QDialog):
        StatusManager.show_message(message, duration)
        return

    status_widget = getattr(window, "_local_status_widget", None)
    if status_widget is None:
        status_widget = StatusBarWidget(window)
        window._local_status_widget = status_widget
    status_widget.show_message(message, duration)
