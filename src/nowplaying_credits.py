# ──────────────────────────────────────────────────────────────────────────────
#  Credits panel
# ──────────────────────────────────────────────────────────────────────────────
from typing import List, Tuple

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from src.logger_config import logger


class _CreditsPanel(QWidget):
    """
    Auto-scrolls like movie credits when content overflows, reverses, loops.
    """

    _SPEED = 0.55
    _TICK_MS = 40
    _PAUSE_MS = 2800

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("background: transparent;")
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._area = QScrollArea()
        self._area.setFrameShape(QFrame.NoFrame)
        self._area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._area.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._area.setStyleSheet("background: transparent; border: none;")
        self._area.setWidgetResizable(True)

        self._container = QWidget()
        self._container.setStyleSheet("background: transparent;")
        self._cards_layout = QVBoxLayout(self._container)
        self._cards_layout.setContentsMargins(0, 8, 0, 48)
        self._cards_layout.setSpacing(6)
        self._cards_layout.setAlignment(Qt.AlignTop)
        self._area.setWidget(self._container)

        root.addWidget(self._area)

        self._pos: float = 0.0
        self._direction = 1
        self._paused = True

        self._timer = QTimer(self)
        self._timer.setInterval(self._TICK_MS)
        self._timer.timeout.connect(self._tick)

    def stop(self):
        self._timer.stop()

    def load_credits(self, track):
        self._timer.stop()
        self._pos = 0.0
        self._direction = 1
        self._paused = True
        self._area.verticalScrollBar().setValue(0)

        while self._cards_layout.count():
            item = self._cards_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not track:
            self._show_placeholder("No track loaded")
            return

        rows: List[Tuple[str, str]] = []
        try:
            for ar in getattr(track, "artist_roles", None) or []:
                role = getattr(ar, "role", None)
                artist = getattr(ar, "artist", None)
                role_name = getattr(role, "role_name", "") or ""
                artist_name = getattr(artist, "artist_name", "") or ""
                if role_name == "Primary Artist":
                    continue
                if role_name and artist_name:
                    rows.append((role_name, artist_name))
        except Exception as exc:
            logger.warning(f"_CreditsPanel: error reading artist_roles: {exc}")

        if not rows:
            self._show_placeholder("No credits available")
            return

        for role_name, artist_name in rows:
            card = self._make_card(role_name, artist_name)
            self._cards_layout.addWidget(card)

        QTimer.singleShot(800, self._maybe_start_scroll)

    def _show_placeholder(self, text: str):
        lbl = QLabel(text)
        lbl.setAlignment(Qt.AlignCenter)
        lbl.setStyleSheet(
            "color: rgba(133,153,234,0.28); font-size: 13px; font-style: italic;"
            " background: transparent; border: none;"
        )
        self._cards_layout.addWidget(lbl)

    @staticmethod
    def _make_card(role: str, name: str) -> QWidget:
        card = QWidget()
        card.setStyleSheet(
            "background: rgba(133,153,234,0.07);"
            " border: 1px solid rgba(133,153,234,0.18);"
            " border-radius: 8px;"
        )
        lay = QHBoxLayout(card)
        lay.setContentsMargins(14, 8, 14, 8)
        lay.setSpacing(12)

        role_lbl = QLabel(role)
        role_lbl.setStyleSheet(
            "color: rgba(133,153,234,0.55); font-size: 10px;"
            " background: transparent; border: none;"
        )
        role_lbl.setFixedWidth(130)

        name_lbl = QLabel(name)
        name_lbl.setStyleSheet(
            "color: rgba(210,218,255,0.88); font-size: 13px;"
            " background: transparent; border: none;"
        )

        lay.addWidget(role_lbl)
        lay.addWidget(name_lbl, stretch=1)
        return card

    def _maybe_start_scroll(self):
        sb = self._area.verticalScrollBar()
        if sb.maximum() > 20:
            self._paused = True
            QTimer.singleShot(self._PAUSE_MS, self._start_scroll)

    def _start_scroll(self):
        self._paused = False
        self._timer.start()

    def _tick(self):
        if self._paused:
            return
        sb = self._area.verticalScrollBar()
        self._pos += self._SPEED * self._direction
        val = int(self._pos)
        val = max(0, min(val, sb.maximum()))
        sb.setValue(val)

        if val >= sb.maximum():
            self._direction = -1
            self._paused = True
            QTimer.singleShot(self._PAUSE_MS, self._resume)
        elif val <= 0 and self._direction == -1:
            self._direction = 1
            self._paused = True
            QTimer.singleShot(self._PAUSE_MS, self._resume)

    def _resume(self):
        self._paused = False
