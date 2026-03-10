# status_widget.py
from PySide6.QtCore import QRect, Qt, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QHBoxLayout, QLabel, QProgressBar, QPushButton, QWidget

from src.asset_paths import icon
from src.logger_config import logger


class StatusBarWidget(QWidget):
    """
    Status widget with two display modes:

    1. FLOAT mode (default): Hovers over the bottom-right corner of the parent
       window as a toast notification. Takes up zero space in the layout.

    2. BAR mode: The classic inline bar pinned at the bottom of the window.
       The user can switch to this by clicking the collapse (↓) button on the
       floating notification.

    Public API (unchanged from the original):
        show_message(message, duration=0)   – show a message
        hide()                              – hide the widget
    """

    # ------------------------------------------------------------------ init

    def __init__(self, parent=None):
        super().__init__(parent)

        self._mode = "float"  # "float" | "bar"
        self._current_message = ""
        self._current_duration = 0

        self._auto_hide_timer = QTimer()
        self._auto_hide_timer.setSingleShot(True)
        self._auto_hide_timer.timeout.connect(self.hide)

        # Reposition whenever the parent window is moved or resized
        if parent:
            parent.installEventFilter(self)

        self._init_ui()
        self.hide()

    # ------------------------------------------------------------ UI building

    def _init_ui(self):
        """Build the widget contents (shared by both modes)."""
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(10, 6, 10, 6)
        self._layout.setSpacing(8)

        # Status icon
        self.icon_label = QLabel()
        self.icon_label.setFixedSize(16, 16)
        self._layout.addWidget(self.icon_label)

        # Status message
        self.message_label = QLabel()
        self.message_label.setWordWrap(False)
        self._layout.addWidget(self.message_label, 1)

        # Progress bar for ongoing tasks
        self.progress_bar = QProgressBar()
        self.progress_bar.setFixedHeight(6)
        self.progress_bar.setMaximumWidth(100)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setRange(0, 0)  # indeterminate / spinning
        self.progress_bar.hide()
        self._layout.addWidget(self.progress_bar)

        # Collapse button – switches from float → bar
        self.collapse_btn = QPushButton("↓")
        self.collapse_btn.setFixedSize(18, 18)
        self.collapse_btn.setFlat(True)
        self.collapse_btn.setToolTip("Collapse to status bar")
        self.collapse_btn.clicked.connect(self._switch_to_bar)
        self.collapse_btn.hide()
        self._layout.addWidget(self.collapse_btn)

        # Close / dismiss button
        self.close_btn = QPushButton()
        self.close_btn.setIcon(QIcon(icon("close.svg")))
        self.close_btn.setFixedSize(16, 16)
        self.close_btn.setFlat(True)
        self.close_btn.clicked.connect(self.hide)
        self.close_btn.hide()
        self._layout.addWidget(self.close_btn)

    # ------------------------------------------------------- public interface

    def show_message(self, message: str, duration: int = 0):
        """
        Show a message.

        Args:
            message:  The text to display.
            duration: Milliseconds before auto-hide.
                      0 = persistent (shows progress bar + buttons).
        """
        logger.debug(
            f"StatusBarWidget.show_message: '{message}', duration={duration}, mode={self._mode}"
        )

        self._current_message = message
        self._current_duration = duration

        self.message_label.setText(message)
        self._set_icon(message)

        is_persistent = duration == 0

        # Progress bar + buttons only for persistent messages
        self.progress_bar.setVisible(is_persistent)
        self.close_btn.setVisible(is_persistent)

        # Collapse button only in float mode for persistent messages
        self.collapse_btn.setVisible(is_persistent and self._mode == "float")

        self._apply_mode_style()
        self._show_widget()

        if duration > 0:
            self._auto_hide_timer.start(duration)
        else:
            self._auto_hide_timer.stop()

    def hide(self):
        """Hide the widget completely."""
        self._auto_hide_timer.stop()
        self.progress_bar.hide()
        self.close_btn.hide()
        self.collapse_btn.hide()
        super().hide()

    # ---------------------------------------------------- mode switching

    def _switch_to_bar(self):
        """Switch from floating toast → inline bar at the bottom."""
        logger.debug("StatusBarWidget: switching to bar mode")
        self._mode = "bar"

        # In bar mode the widget must be part of the normal layout flow,
        # so we clear the floating window flags.
        self.setWindowFlags(Qt.WindowType.Widget)

        # Re-show with the same message using bar styling
        self.show_message(self._current_message, self._current_duration)

    # ---------------------------------------------------- internal helpers

    def _set_icon(self, message: str):
        msg_lower = message.lower()
        if "import" in msg_lower:
            icon_name = "import.svg"
        elif "error" in msg_lower or "fail" in msg_lower:
            icon_name = "error.svg"
        else:
            icon_name = "info.svg"
        self.icon_label.setPixmap(QIcon(icon(icon_name)).pixmap(16, 16))

    def _apply_mode_style(self):
        """Apply the correct stylesheet for the current mode."""
        if self._mode == "float":
            self.setStyleSheet("""
                StatusBarWidget {
                    background-color: palette(window);
                    border: 1px solid palette(mid);
                    border-radius: 8px;
                    min-height: 36px;
                    max-height: 36px;
                }
                QLabel {
                    color: palette(window-text);
                    font-size: 12px;
                }
                QPushButton {
                    color: palette(window-text);
                }
            """)
        else:  # bar
            self.setStyleSheet("""
                StatusBarWidget {
                    background-color: palette(mid);
                    border-top: 1px solid palette(shadow);
                    border-radius: 0px;
                    min-height: 24px;
                    max-height: 30px;
                }
                QLabel {
                    color: palette(window-text);
                    font-size: 12px;
                }
            """)

    def _show_widget(self):
        """Make the widget visible using the correct geometry for the mode."""
        if self._mode == "float":
            self._position_float()
            # Ensure it paints on top of other widgets but stays inside the window
            self.setWindowFlags(Qt.WindowType.Widget)
            self.raise_()
        else:
            # Bar mode: let the parent layout handle it normally.
            # Remove any absolute positioning leftovers.
            self.setGeometry(QRect())  # clear explicit geometry
            self.setMaximumHeight(30)
            self.setMinimumHeight(24)

        self.show()

        if self._mode == "float":
            # raise again after show() so it stays on top
            self.raise_()

    def _position_float(self):
        """Place the toast in the bottom-right corner of the parent widget."""
        parent = self.parent()
        if parent is None:
            return

        self.adjustSize()
        w = max(self.sizeHint().width(), 260)
        h = self.sizeHint().height()

        margin = 12
        parent_rect = parent.rect()

        x = parent_rect.right() - w - margin
        y = parent_rect.bottom() - h - margin

        self.setGeometry(x, y, w, h)

    # ------------------------------------------------ event filter (reposition on parent resize/move)

    def eventFilter(self, watched, event):
        from PySide6.QtCore import QEvent

        if watched is self.parent() and self._mode == "float" and self.isVisible():
            if event.type() in (QEvent.Type.Resize, QEvent.Type.Move):
                self._position_float()
        return super().eventFilter(watched, event)
