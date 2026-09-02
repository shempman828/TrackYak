from PySide6.QtCore import Property, QEasingCurve, QPropertyAnimation, QRect, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QWidget

from src.foundation.asset_paths import ASSETS_DIR
from src.foundation.logger_config import logger

# Matches themes/dark_mode.qss so the splash doesn't look like a different app.
_COLOR_SURFACE = QColor("#11121a")
_COLOR_FALLBACK_BG = QColor("#0b0c10")
_COLOR_TEXT = QColor("#b8c0f0")
_COLOR_TEXT_DIM = QColor("#555e7a")
_COLOR_ACCENT_GREEN = QColor("#99EA85")


class StartupSplash(QWidget):
    """Wayland-friendly splash screen with fade/scale animations and status messages."""

    def __init__(self, min_duration_ms: int = 1000) -> None:
        super().__init__()

        # Window setup
        self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)

        # A widget's own stylesheet always wins over the app-wide one, so this
        # keeps the splash transparent even after the theme QSS is applied
        # mid-startup (its global "QWidget { background-color: ... }" rule
        # would otherwise repaint the splash's background and change its look
        # partway through loading).
        self.setObjectName("startupSplash")
        self.setStyleSheet("QWidget#startupSplash { background: transparent; border: none; }")

        # Load splash image
        self.pixmap = self._load_splash_image()
        self.resize(self.pixmap.deviceIndependentSize().toSize())

        # Animation state
        self._opacity = 0.0
        self._scale = 0.8
        self._message: str | None = None
        self._progress: int | None = None

        # Minimum duration
        self.min_duration_ms = min_duration_ms
        self.finish_requested = False
        self._min_duration_elapsed = False

        # Center splash using full pixmap size so position never shifts
        # during the scale animation. The window stays fixed; only the
        # painted content grows.
        self._set_initial_position()

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
        self.update()  # Triggers paintEvent only — window position does not move

    scale = Property(float, get_scale, set_scale)

    # --- Opacity property (required for fade animations) ---
    def get_opacity(self) -> float:
        return self._opacity

    def set_opacity(self, value: float) -> None:
        self._opacity = value
        self.setWindowOpacity(value)

    opacity = Property(float, get_opacity, set_opacity)

    # --- Core Behavior ---
    def finish(self) -> None:
        """Request splash finish respecting minimum duration."""
        if not self._min_duration_elapsed:
            self.finish_requested = True
        else:
            self._animate_exit()

    def _on_min_duration_elapsed(self) -> None:
        self._min_duration_elapsed = True
        if self.finish_requested:
            self._animate_exit()

    def update_status(self, message: str, progress: int | None = None) -> None:
        """Update status message and optional progress."""
        self._message = message
        self._progress = progress
        self.update()

    # --- Helpers ---
    def _set_initial_position(self) -> None:
        """Position the window once using the full pixmap size.

        Uses the optical center: slightly above true center (40% down instead
        of 50%) which feels more balanced to the human eye.
        """
        screen_geom = QApplication.primaryScreen().availableGeometry()
        logical_size = self.pixmap.deviceIndependentSize().toSize()
        w = logical_size.width()
        h = logical_size.height()

        x = (screen_geom.width() - w) // 2 + screen_geom.x()

        # Optical center: place the vertical midpoint of the window at 40%
        # of the screen height instead of 50%. This makes it feel centered
        # without sitting too low.
        optical_center_y = int(screen_geom.height() * 0.40)
        y = optical_center_y - h // 2 + screen_geom.y()

        self.setGeometry(x, y, w, h)

    def _load_splash_image(self) -> QPixmap:
        screen = QApplication.primaryScreen()
        dpr = screen.devicePixelRatio()
        size = screen.availableSize()
        # Logical (device-independent) target size, then scale the source
        # image to the device-pixel equivalent so it stays crisp on HiDPI
        # screens. setDevicePixelRatio makes deviceIndependentSize() report
        # the logical target back to callers positioning/resizing the window.
        target = QSize(int(size.width() * 0.6), int(size.height() * 0.6))
        target_device = QSize(int(target.width() * dpr), int(target.height() * dpr))

        path = ASSETS_DIR / "splash.png"
        pixmap = QPixmap(str(path))
        if pixmap.isNull():
            logger.warning("Splash image not found, using fallback")
            pixmap = QPixmap(target_device)
            pixmap.fill(_COLOR_FALLBACK_BG)
        else:
            pixmap = pixmap.scaled(target_device, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        pixmap.setDevicePixelRatio(dpr)
        return pixmap

    # --- Painting ---
    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)

        # The window is always full-size. We draw the content scaled and
        # centered within it so the animation looks like it's growing in place.
        logical_size = self.pixmap.deviceIndependentSize().toSize()
        full_w = logical_size.width()
        full_h = logical_size.height()
        scaled_w = int(full_w * self._scale)
        scaled_h = int(full_h * self._scale)

        # Offset so the scaled content stays centered inside the window
        offset_x = (full_w - scaled_w) // 2
        offset_y = (full_h - scaled_h) // 2

        painter.drawPixmap(QRect(offset_x, offset_y, scaled_w, scaled_h), self.pixmap)

        # Draw status area
        if self._message:
            status_rect = QRect(
                offset_x, offset_y + int(scaled_h * 2 / 3), scaled_w, int(scaled_h / 3)
            )
            surface = QColor(_COLOR_SURFACE)
            surface.setAlpha(220)
            painter.fillRect(status_rect, surface)

            # Explicit family/size rather than QFont() so the splash text
            # doesn't visibly change when the theme applies its own app-wide
            # font mid-startup (display_settings._apply_font -> app.setFont).
            base_font = QFont("Cambria")

            # Title
            title_font = QFont(base_font)
            title_font.setPointSize(16)
            title_font.setBold(True)
            painter.setFont(title_font)
            painter.setPen(_COLOR_TEXT)
            painter.drawText(
                status_rect.adjusted(0, 10, 0, -40), Qt.AlignCenter, "Baby Yak Studios"
            )

            # Message
            message_font = QFont(base_font)
            message_font.setPointSize(12)
            painter.setFont(message_font)
            painter.drawText(
                status_rect.adjusted(20, 40, -20, -40),
                Qt.AlignCenter | Qt.TextWordWrap,
                self._message,
            )

            # Progress bar
            if self._progress is not None:
                bar_height = 12
                bar_margin_sides = int(status_rect.width() * 0.1)
                bar_rect = QRect(
                    status_rect.left() + bar_margin_sides,
                    status_rect.bottom() - bar_height - 10,
                    status_rect.width() - bar_margin_sides * 2,
                    bar_height,
                )
                painter.fillRect(bar_rect, _COLOR_TEXT_DIM)
                if self._progress > 0:
                    fill_width = max(1, int(bar_rect.width() * self._progress / 100))
                    painter.fillRect(
                        bar_rect.adjusted(0, 0, fill_width - bar_rect.width(), 0),
                        _COLOR_ACCENT_GREEN,
                    )

        painter.end()
