"""Floating status toast shown in the main window's bottom-right corner."""

from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtGui import QColor, QIcon
from PySide6.QtWidgets import QGraphicsDropShadowEffect, QHBoxLayout, QLabel, QProgressBar, QPushButton, QWidget

from src.common.widgets.style_utils import set_style_property
from src.foundation.asset_paths import icon
from src.foundation.logger_config import logger

_MARGIN = 12
_MIN_WIDTH = 260


class StatusBarWidget(QWidget):
    """Translucent status toast that floats over the parent window and never takes layout space."""

    def __init__(self, parent=None):
        super().__init__(parent)

        # Plain QWidgets don't paint stylesheet background/border by default;
        # this is required for the translucent rgba panel to actually render.
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        self._full_message = ""

        self._auto_hide_timer = QTimer(self)
        self._auto_hide_timer.setSingleShot(True)
        self._auto_hide_timer.timeout.connect(self.hide)

        # Reposition whenever the parent window is moved or resized
        if parent:
            parent.installEventFilter(self)

        self._init_ui()
        set_style_property(self, "mode", "float")

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(24)
        shadow.setOffset(0, 6)
        shadow.setColor(QColor(0, 0, 0, 160))
        self.setGraphicsEffect(shadow)

        self.hide()

    def _init_ui(self):
        """Build the icon, message, progress bar and close button."""
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(10, 6, 10, 6)
        self._layout.setSpacing(8)

        self.icon_label = QLabel()
        self.icon_label.setFixedSize(16, 16)
        self._layout.addWidget(self.icon_label)

        self.message_label = QLabel()
        self.message_label.setWordWrap(False)
        self._layout.addWidget(self.message_label, 1)

        # Indeterminate progress bar for ongoing tasks
        self.progress_bar = QProgressBar()
        self.progress_bar.setFixedHeight(6)
        self.progress_bar.setMaximumWidth(100)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setRange(0, 0)
        self.progress_bar.hide()
        self._layout.addWidget(self.progress_bar)

        self.close_btn = QPushButton()
        self.close_btn.setIcon(QIcon(icon("close.svg")))
        self.close_btn.setFixedSize(16, 16)
        self.close_btn.setFlat(True)
        self.close_btn.setToolTip("Dismiss")
        self.close_btn.setAccessibleName("Dismiss")
        self.close_btn.clicked.connect(self.hide)
        self.close_btn.hide()
        self._layout.addWidget(self.close_btn)

    def show_message(self, message: str, duration: int = 0):
        """Show message; duration is ms before auto-hide, 0 = persistent with progress bar and close button."""
        logger.debug(f"StatusBarWidget.show_message: '{message}', duration={duration}")

        self._full_message = message
        self.message_label.setText(message)
        self.message_label.setToolTip("")
        self._set_icon(message)

        is_persistent = duration == 0
        self.progress_bar.setVisible(is_persistent)
        self.close_btn.setVisible(is_persistent)

        self._position()
        self.show()
        self.raise_()  # keep it painted on top of the current view

        if duration > 0:
            self._auto_hide_timer.start(duration)
        else:
            self._auto_hide_timer.stop()

    def hide(self):
        """Hide the toast and stop its auto-hide timer."""
        self._auto_hide_timer.stop()
        self.progress_bar.hide()
        self.close_btn.hide()
        super().hide()

    def _set_icon(self, message: str):
        """Pick the toast icon from keywords in the message."""
        msg_lower = message.lower()
        if "import" in msg_lower:
            icon_name = "import.svg"
        elif "error" in msg_lower or "fail" in msg_lower:
            icon_name = "error.svg"
        else:
            icon_name = "info.svg"
        self.icon_label.setPixmap(QIcon(icon(icon_name)).pixmap(16, 16))

    def _position(self):
        """Place the toast in the parent's bottom-right corner, never wider than the parent."""
        parent = self.parent()
        if parent is None:
            return

        parent_rect = parent.rect()
        max_w = max(parent_rect.width() - 2 * _MARGIN, 1)

        # Measure with the full text, then elide it if the toast would overflow the parent.
        self.message_label.setText(self._full_message)
        self.adjustSize()
        hint_w = self.sizeHint().width()
        if hint_w > max_w:
            overflow = hint_w - max_w
            label_w = max(self.message_label.sizeHint().width() - overflow, 20)
            elided = self.message_label.fontMetrics().elidedText(self._full_message, Qt.ElideRight, label_w)
            self.message_label.setText(elided)
            self.message_label.setToolTip(self._full_message)
        else:
            self.message_label.setToolTip("")

        w = min(max(hint_w, _MIN_WIDTH), max_w)
        h = self.sizeHint().height()

        x = parent_rect.right() - w - _MARGIN
        y = parent_rect.bottom() - h - _MARGIN

        self.setGeometry(x, y, w, h)

    def eventFilter(self, watched, event):
        """Reposition the toast when the visible parent is resized or moved."""
        if watched is self.parent() and self.isVisible() and event.type() in (QEvent.Type.Resize, QEvent.Type.Move):
            self._position()
        return super().eventFilter(watched, event)
