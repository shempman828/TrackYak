# status_bar.py
from PySide6.QtCore import QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QHBoxLayout, QLabel, QProgressBar, QPushButton, QWidget

from asset_paths import icon

from logger_config import logger


class StatusBarWidget(QWidget):
    """
    Compact status bar that only appears when there's activity.
    Shows current tasks and progress.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._auto_hide_timer = QTimer()
        self._auto_hide_timer.setSingleShot(True)
        self._auto_hide_timer.timeout.connect(self.hide)

        self._init_ui()
        self.hide()  # Start hidden

    def _init_ui(self):
        """Initialize the status bar UI"""
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(8)

        # Status icon
        self.icon_label = QLabel()
        self.icon_label.setFixedSize(16, 16)
        layout.addWidget(self.icon_label)

        # Status message
        self.message_label = QLabel()
        layout.addWidget(self.message_label, 1)

        # Progress bar for ongoing tasks
        self.progress_bar = QProgressBar()
        self.progress_bar.setFixedHeight(6)
        self.progress_bar.setMaximumWidth(120)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.hide()
        layout.addWidget(self.progress_bar)

        # Close button
        self.close_btn = QPushButton()
        self.close_btn.setIcon(QIcon(icon("close.svg")))
        self.close_btn.setFixedSize(16, 16)
        self.close_btn.setFlat(True)
        self.close_btn.clicked.connect(self.hide)
        self.close_btn.hide()  # Only show for persistent messages
        layout.addWidget(self.close_btn)

        # Set style
        self.setStyleSheet("""
            StatusBarWidget {
                background-color: palette(mid);
                border-top: 1px solid palette(shadow);
                min-height: 24px;
                max-height: 30px;
            }
        """)

    def show_message(self, message, duration=0):
        """Show a message in the status bar"""
        logger.debug(
            f"DEBUG: StatusBarWidget.show_message: '{message}', duration: {duration}"
        )
        self.message_label.setText(message)

        # Set appropriate icon
        if "import" in message.lower():
            self.icon_label.setPixmap(QIcon(icon("import.svg")).pixmap(16, 16))
        elif "error" in message.lower() or "fail" in message.lower():
            self.icon_label.setPixmap(QIcon(icon("error.svg")).pixmap(16, 16))
        else:
            self.icon_label.setPixmap(QIcon(icon("info.svg")).pixmap(16, 16))

        # Show progress bar for ongoing tasks
        if duration == 0:  # Persistent task
            self.progress_bar.show()
            self.close_btn.show()
        else:
            self.progress_bar.hide()
            self.close_btn.hide()

        # Show the status bar
        self.show()

        # Auto-hide for temporary messages
        if duration > 0:
            self._auto_hide_timer.start(duration)
        else:
            self._auto_hide_timer.stop()

    def hide(self):
        """Hide the status bar"""
        super().hide()
        self.progress_bar.hide()
        self.close_btn.hide()
        self._auto_hide_timer.stop()
        self.setFixedHeight(0)
