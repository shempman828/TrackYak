# ──────────────────────────────────────────────────────────────────────────────
#  Credits panel
# ──────────────────────────────────────────────────────────────────────────────

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
from sqlalchemy.exc import SQLAlchemyError

from src.common.layout_utils import clear_layout
from src.foundation.logger_config import logger


class _CreditsPanel(QWidget):
    """
    Auto-scrolls like movie credits when content overflows, reverses, loops.
    """

    _SPEED = 0.55
    _TICK_MS = 40
    _PAUSE_MS = 2800

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setProperty("bgTransparent", True)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._area = QScrollArea()
        self._area.setFrameShape(QFrame.NoFrame)
        self._area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._area.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._area.setWidgetResizable(True)

        self._container = QWidget()
        self._container.setProperty("bgTransparent", True)
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

        clear_layout(self._cards_layout)

        if not track:
            self._show_placeholder("No track loaded")
            return

        grouped: dict[int, tuple[str, list[str]]] = {}
        try:
            for ar in getattr(track, "artist_roles", None) or []:
                role = getattr(ar, "role", None)
                role_name = getattr(role, "role_name", "") or ""
                artist_name = getattr(ar, "credited_name", "") or ""
                if role_name == "Primary Artist":
                    continue
                if not (role_name and artist_name):
                    continue
                key = getattr(ar, "artist_id", None)
                if key is None:
                    key = artist_name
                if key not in grouped:
                    grouped[key] = (artist_name, [])
                roles = grouped[key][1]
                if role_name not in roles:
                    roles.append(role_name)
        except SQLAlchemyError as exc:
            logger.warning(f"_CreditsPanel: error reading artist_roles: {exc}")

        if not grouped:
            self._show_placeholder("No credits available")
            return

        for artist_name, role_names in grouped.values():
            card = self._make_card(", ".join(role_names), artist_name)
            self._cards_layout.addWidget(card)

        QTimer.singleShot(800, self._maybe_start_scroll)

    def _show_placeholder(self, text: str):
        lbl = QLabel(text)
        lbl.setAlignment(Qt.AlignCenter)
        lbl.setProperty("npRole", "creditsPlaceholder")
        self._cards_layout.addWidget(lbl)

    @staticmethod
    def _make_card(role: str, name: str) -> QWidget:
        card = QWidget()
        card.setProperty("npCreditCard", True)
        lay = QHBoxLayout(card)
        lay.setContentsMargins(14, 8, 14, 8)
        lay.setSpacing(12)

        role_lbl = QLabel(role)
        role_lbl.setProperty("npRole", "creditsRole")
        role_lbl.setFixedWidth(130)
        role_lbl.setWordWrap(True)

        name_lbl = QLabel(name)
        name_lbl.setProperty("npRole", "creditsName")

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
