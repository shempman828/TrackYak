# ──────────────────────────────────────────────────────────────────────────────
#  Credits panel
# ──────────────────────────────────────────────────────────────────────────────

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QScrollArea, QSizePolicy, QVBoxLayout, QWidget
from sqlalchemy.exc import SQLAlchemyError

from src.common.widgets.layout_utils import clear_layout
from src.foundation.censor import censor_text
from src.foundation.logger_config import logger


class _CreditsPanel(QWidget):
    """Track credits that auto-scroll like film credits when they overflow, reversing at each end."""

    _SPEED = 0.55
    _TICK_MS = 40
    _PAUSE_MS = 2800
    _SETTLE_MS = 800  # wait for the new rows to lay out before measuring overflow

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
        self._cards_layout.setContentsMargins(0, 16, 0, 48)
        self._cards_layout.setSpacing(12)
        self._cards_layout.setAlignment(Qt.AlignTop)
        self._area.setWidget(self._container)

        root.addWidget(self._area)

        self._pos: float = 0.0
        self._direction = 1
        self._paused = True
        self._self_scrolling = False  # True while _tick moves the bar, to tell it from a user scroll

        self._timer = QTimer(self)
        self._timer.setInterval(self._TICK_MS)
        self._timer.timeout.connect(self._tick)

        # Member timers (not QTimer.singleShot) so stop() and a new track can cancel them.
        self._settle_timer = QTimer(self)
        self._settle_timer.setSingleShot(True)
        self._settle_timer.setInterval(self._SETTLE_MS)
        self._settle_timer.timeout.connect(self._maybe_start_scroll)
        self._pause_timer = QTimer(self)
        self._pause_timer.setSingleShot(True)
        self._pause_timer.setInterval(self._PAUSE_MS)
        self._pause_timer.timeout.connect(self._resume)

        self._area.verticalScrollBar().valueChanged.connect(self._on_scroll_value)

    def stop(self):
        """Stop the auto-scroll and cancel any pending start or resume."""
        self._timer.stop()
        self._settle_timer.stop()
        self._pause_timer.stop()

    def load_credits(self, track):
        """Rebuild the credit rows for ``track`` (or a placeholder) and schedule the auto-scroll."""
        self.stop()
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
                artist_name = censor_text(getattr(ar, "credited_name", "") or "")
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

        self._settle_timer.start()

    def hideEvent(self, event):
        """Pause the auto-scroll while the panel is not on screen."""
        super().hideEvent(event)
        self.stop()

    def showEvent(self, event):
        """Re-check the overflow and resume the auto-scroll when the panel shows again."""
        super().showEvent(event)
        if self._cards_layout.count():
            self._settle_timer.start()

    def _show_placeholder(self, text: str):
        lbl = QLabel(text)
        lbl.setAlignment(Qt.AlignCenter)
        lbl.setProperty("npRole", "creditsPlaceholder")
        self._cards_layout.addWidget(lbl)

    @staticmethod
    def _make_card(role: str, name: str) -> QWidget:
        """One film-credits row: role right-aligned left of a centre gutter, name left-aligned right of it."""
        row = QWidget()
        row.setProperty("bgTransparent", True)
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(18)

        role_lbl = QLabel(role.upper())
        role_lbl.setProperty("npRole", "creditsRole")
        role_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        role_lbl.setWordWrap(True)

        name_lbl = QLabel(name)
        name_lbl.setProperty("npRole", "creditsName")
        name_lbl.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        name_lbl.setWordWrap(True)

        # Equal stretch keeps the gutter on the centre line for every row.
        lay.addWidget(role_lbl, stretch=1)
        lay.addWidget(name_lbl, stretch=1)
        return row

    def _maybe_start_scroll(self):
        """Start the auto-scroll, after a pause at the top, when the credits overflow."""
        if self._area.verticalScrollBar().maximum() > 20:
            self._paused = True
            self._pause_timer.start()

    def _tick(self):
        """Move one step; pause and reverse at each end."""
        if self._paused:
            return
        sb = self._area.verticalScrollBar()
        self._pos += self._SPEED * self._direction
        val = max(0, min(int(self._pos), sb.maximum()))
        self._self_scrolling = True
        try:
            sb.setValue(val)
        finally:
            self._self_scrolling = False

        if val >= sb.maximum():
            self._direction = -1
            self._paused = True
            self._pause_timer.start()
        elif val <= 0 and self._direction == -1:
            self._direction = 1
            self._paused = True
            self._pause_timer.start()

    def _resume(self):
        """End a pause and (re)start the tick timer."""
        self._paused = False
        self._timer.start()

    def _on_scroll_value(self, value: int):
        """A user scroll (wheel, keys) moves the auto-scroll there and pauses it for a moment."""
        if self._self_scrolling or not (self._timer.isActive() or self._pause_timer.isActive()):
            return
        self._pos = float(value)
        self._paused = True
        self._pause_timer.start()
