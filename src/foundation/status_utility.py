"""App-wide status-bar message bus and toast helper."""

from PySide6.QtCore import QMutex, QMutexLocker, QObject, QTimer, Signal

from src.foundation.logger_config import logger


class _StatusManager(QObject):
    """Singleton that tracks background tasks and drives the main status bar."""

    show_status = Signal(str, int)
    hide_status = Signal()
    # Internal: the public methods emit these so that calls from worker threads are queued onto the
    # manager's own thread (AutoConnection), where the QTimer and task counter live.
    _start_task_requested = Signal(str)
    _end_task_requested = Signal(str, int)
    _show_message_requested = Signal(str, int)

    _instance = None
    _lock = QMutex()
    _initialized = False

    def __new__(cls):
        with QMutexLocker(cls._lock):
            if cls._instance is None:
                cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        # __new__ returns the singleton, so __init__ runs again on every _StatusManager() call.
        if self._initialized:
            return
        super().__init__()
        self._initialized = True
        self._active_tasks = 0
        # Persistent message of the most recent running task, restored after a transient message expires.
        self._task_message = ""
        self._auto_hide_timer = QTimer(self)
        self._auto_hide_timer.setSingleShot(True)
        self._auto_hide_timer.timeout.connect(self._check_and_hide)
        self._start_task_requested.connect(self._start_task)
        self._end_task_requested.connect(self._end_task)
        self._show_message_requested.connect(self._show_message)

    def start_task(self, message=""):
        """Start a background task and show its message until the task ends."""
        self._start_task_requested.emit(message)

    def end_task(self, completion_message="", duration=3000):
        """End a background task and optionally show a completion message."""
        self._end_task_requested.emit(completion_message, duration)

    def show_message(self, message, duration=3000):
        """Show a status message; a duration of 0 or less keeps it until replaced."""
        self._show_message_requested.emit(message, duration)

    def _start_task(self, message):
        """Count a new task and show its persistent message."""
        self._active_tasks += 1
        if message:
            self._task_message = message
            self._show_message(message, 0)
        logger.debug(f"Task started. Active tasks: {self._active_tasks}")

    def _end_task(self, completion_message, duration):
        """Count a finished task and show the completion message or hide the bar."""
        self._active_tasks = max(0, self._active_tasks - 1)
        if self._active_tasks == 0:
            self._task_message = ""

        if completion_message:
            self._show_message(completion_message, duration)
        else:
            self._check_and_hide()

        logger.debug(f"Task ended. Active tasks: {self._active_tasks}")

    def _show_message(self, message, duration):
        """Emit show_status and arm the auto-hide timer for transient messages."""
        self.show_status.emit(message, duration)
        if duration > 0:
            self._auto_hide_timer.start(duration)
        else:
            self._auto_hide_timer.stop()
        logger.debug(f"Status message: {message} (duration: {duration})")

    def _check_and_hide(self):
        """Hide the bar when no task runs; else show the running task's message again."""
        if self._auto_hide_timer.isActive():
            return
        if self._active_tasks == 0:
            self.hide_status.emit()
            logger.debug("Status bar hidden - no active tasks")
        elif self._task_message:
            self.show_status.emit(self._task_message, 0)


StatusManager = _StatusManager()


def show_status_message(widget, message: str, duration: int = 4000):
    """Show a non-blocking toast on the dialog that holds `widget`, else on the main status bar."""
    # A modal dialog gets its own StatusBarWidget, so the toast is not hidden behind the dialog.
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
