# ──────────────────────────────────────────────────────────────────────────────
#  Karaoke label (animated)
# ──────────────────────────────────────────────────────────────────────────────
from typing import Optional

from PySide6.QtCore import Property, QEasingCurve, QPropertyAnimation, Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QLabel, QSizePolicy


class _KaraokeLine(QLabel):
    """Single-line karaoke label that fades in on each new line."""

    def __init__(self, parent=None):
        super().__init__(parent)
        font = QFont("Georgia", 22, QFont.Bold)
        self.setFont(font)
        self.setAlignment(Qt.AlignCenter)
        self.setWordWrap(True)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._opacity: float = 1.0
        self._anim: Optional[QPropertyAnimation] = None
        self._set_opacity(1.0)

    def _get_opacity(self) -> float:
        return self._opacity

    def _set_opacity(self, v: float):
        self._opacity = v
        alpha = int(v * 230)
        self.setStyleSheet(
            f"color: rgba(230, 235, 255, {alpha});"
            " background: transparent; border: none;"
        )

    lineOpacity = Property(float, _get_opacity, _set_opacity)

    def show_line(self, text: str):
        self.setText(text)
        if self._anim:
            self._anim.stop()
        self._set_opacity(0.0)
        self._anim = QPropertyAnimation(self, b"lineOpacity")
        self._anim.setDuration(350)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.start()

    def clear_line(self):
        if self._anim:
            self._anim.stop()
        self.setText("")
        self._set_opacity(0.0)
