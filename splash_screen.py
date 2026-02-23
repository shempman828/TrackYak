from typing import Optional

from PySide6.QtCore import (
    Property,
    QEasingCurve,
    QPropertyAnimation,
    QRect,
    QSize,
    Qt,
    QTimer,
)
from PySide6.QtGui import QColor, QFont, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QWidget

from asset_paths import ASSETS_DIR
from logger_config import logger


class StartupSplash(QWidget):
    """Wayland-friendly splash screen with fade/scale animations and status messages."""

    def __init__(self, min_duration_ms: int = 1000) -> None:
        super().__init__()

        # Window setup
        self.setWindowFlags(
            Qt.Window | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground)

        # Load splash image
        self.pixmap = self._load_splash_image()
        self.resize(self.pixmap.size())

        # Animation state
        self._opacity = 0.0
        self._scale = 0.8
        self._message: Optional[str] = None
        self._progress: Optional[int] = None

        # Minimum duration
        self.min_duration_ms = min_duration_ms
        self.finish_requested = False

        # Center splash
        self._center_on_screen()

        # Animations
        self._setup_animations()

        # Show splash and start entrance animation
        self.show()
        self._animate_entrance()

        # Start minimum duration timer
        QTimer.singleShot(self.min_duration_ms, self._on_min_duration_elapsed)

    # --- Animations ---
    def _setup_animations(self) -> None:
        self.fade_animation = QPropertyAnimation(self, b"opacity")
        self.fade_animation.setDuration(600)
        self.fade_animation.setStartValue(0.0)
        self.fade_animation.setEndValue(1.0)
        self.fade_animation.setEasingCurve(QEasingCurve.OutCubic)

        self.scale_animation = QPropertyAnimation(self, b"scale")
        self.scale_animation.setDuration(700)
        self.scale_animation.setStartValue(0.8)
        self.scale_animation.setEndValue(1.0)
        self.scale_animation.setEasingCurve(QEasingCurve.OutBack)

        self.exit_animation = QPropertyAnimation(self, b"opacity")
        self.exit_animation.setDuration(400)
        self.exit_animation.setStartValue(1.0)
        self.exit_animation.setEndValue(0.0)
        self.exit_animation.setEasingCurve(QEasingCurve.InCubic)
        self.exit_animation.finished.connect(self.close)

    def _animate_entrance(self) -> None:
        self.fade_animation.start()
        self.scale_animation.start()

    def _animate_exit(self) -> None:
        self.exit_animation.start()

    def get_scale(self) -> float:
        return self._scale

    def set_scale(self, value: float) -> None:
        self._scale = value
        self.update()  # Triggers paintEvent
        self._center_on_screen()

    scale = Property(float, get_scale, set_scale)

    # --- Core Behavior ---
    def finish(self, widget: Optional[QWidget] = None) -> None:
        """Request splash finish respecting minimum duration."""
        if not hasattr(self, "_min_duration_elapsed"):
            # safety if timer hasn't fired
            self.finish_requested = True
            return

        if not self._min_duration_elapsed:
            self.finish_requested = True
        else:
            self._animate_exit()

    def _on_min_duration_elapsed(self) -> None:
        self._min_duration_elapsed = True
        if self.finish_requested:
            self._animate_exit()

    def update_status(self, message: str, progress: Optional[int] = None) -> None:
        """Update status message and optional progress."""
        self._message = message
        self._progress = progress
        self.update()

    # --- Helpers ---
    def _center_on_screen(self) -> None:
        screen_geom = QApplication.primaryScreen().availableGeometry()
        w, h = (
            int(self.pixmap.width() * self._scale),
            int(self.pixmap.height() * self._scale),
        )
        x = (screen_geom.width() - w) // 2 + screen_geom.x()
        y = (screen_geom.height() - h) // 2 + screen_geom.y()
        self.setGeometry(x, y, w, h)

    def _load_splash_image(self) -> QPixmap:
        screen = QApplication.primaryScreen()
        size = screen.availableSize()
        target = QSize(int(size.width() * 0.6), int(size.height() * 0.6))

        path = ASSETS_DIR / "splash.png"
        pixmap = QPixmap(str(path))
        if pixmap.isNull():
            logger.warning("Splash image not found, using fallback")
            pixmap = QPixmap(target)
            pixmap.fill(QColor(43, 43, 43))
        else:
            pixmap = pixmap.scaled(target, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        return pixmap

    # --- Painting ---
    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
        # Draw scaled pixmap
        scaled_size = self.pixmap.size() * self._scale
        painter.drawPixmap(
            QRect(0, 0, int(scaled_size.width()), int(scaled_size.height())),
            self.pixmap,
        )

        # Draw status area
        if self._message:
            status_rect = QRect(
                0,
                int(scaled_size.height() * 2 / 3),
                int(scaled_size.width()),
                int(scaled_size.height() / 3),
            )
            painter.fillRect(status_rect, QColor(43, 43, 43, 220))

            # Title
            painter.setFont(QFont("Arial", 16, QFont.Bold))
            painter.setPen(Qt.white)
            painter.drawText(
                status_rect.adjusted(0, 10, 0, -40), Qt.AlignCenter, "Baby Yak Studios"
            )

            # Message
            painter.setFont(QFont("Arial", 12))
            painter.drawText(
                status_rect.adjusted(20, 40, -20, -40),
                Qt.AlignCenter | Qt.TextWordWrap,
                self._message,
            )

            # Progress bar
            if self._progress is not None:
                bar_rect = status_rect.adjusted(
                    int(status_rect.width() * 0.1),
                    -30,
                    -int(status_rect.width() * 0.1),
                    -10,
                )
                painter.fillRect(bar_rect, QColor(85, 85, 85))
                if self._progress > 0:
                    fill_width = max(1, int(bar_rect.width() * self._progress / 100))
                    painter.fillRect(
                        bar_rect.adjusted(0, 0, fill_width - bar_rect.width(), 0),
                        QColor(76, 175, 80),
                    )

        painter.end()
