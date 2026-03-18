# ──────────────────────────────────────────────────────────────────────────────
#  Chip widgets
# ──────────────────────────────────────────────────────────────────────────────
from typing import List

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QScrollArea, QWidget


class _Chip(QLabel):
    """Pill-shaped metadata chip."""

    _SS = (
        "QLabel { background: rgba(133,153,234,0.13);"
        " border: 1px solid rgba(133,153,234,0.28);"
        " border-radius: 10px; color: rgba(200,208,244,0.80);"
        " font-size: 10px; padding: 2px 10px; }"
    )

    def __init__(self, icon_str: str, value: str, parent=None):
        super().__init__(parent)
        self._icon = icon_str
        self.setStyleSheet(self._SS)
        self.set_value(value)

    def set_value(self, value: str):
        self.setText(f"{self._icon}  {value}" if self._icon else value)


class _ScrollingChipRow(QScrollArea):
    """Horizontally scrolling row of chips, no scrollbar visible.

    When chip content is wider than the visible area, the row slowly pans
    left to the end and then pans back — so nothing is ever clipped.
    """

    # How often we nudge the scroll position (ms)
    _PAN_INTERVAL_MS = 30
    # Pixels moved per tick  (lower = slower pan)
    _PAN_SPEED_PX = 1
    # How long to pause (ms) at each end before reversing
    _PAN_PAUSE_MS = 1800

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setFixedHeight(36)
        self.setStyleSheet("background: transparent; border: none;")
        self.setWidgetResizable(False)

        self._inner = QWidget()
        self._inner.setStyleSheet("background: transparent;")
        self._row = QHBoxLayout(self._inner)
        self._row.setContentsMargins(0, 4, 0, 4)
        self._row.setSpacing(6)
        self.setWidget(self._inner)

        # Pan state
        self._pan_direction: int = 1  # +1 = scrolling right, -1 = left
        self._pan_pausing: bool = False

        self._pan_timer = QTimer(self)
        self._pan_timer.setInterval(self._PAN_INTERVAL_MS)
        self._pan_timer.timeout.connect(self._pan_tick)

        self._pause_timer = QTimer(self)
        self._pause_timer.setSingleShot(True)
        self._pause_timer.timeout.connect(self._end_pause)

    # ── chip management ────────────────────────────────────────────────────

    def set_chips(self, chips: List[_Chip]):
        """Show only the chips in *chips*; hide the rest.

        We deliberately do NOT reparent or destroy chip widgets —
        reparenting (setParent(None)) deletes them from Qt's perspective
        which causes chips to go missing on the next track change.
        Instead we just show/hide each chip in-place.
        """
        # Collect every widget currently in the layout
        all_widgets: List[QWidget] = []
        for i in range(self._row.count()):
            item = self._row.itemAt(i)
            if item and item.widget():
                all_widgets.append(item.widget())

        # Figure out which chips need to be added (not yet in the layout)
        existing = set(all_widgets)
        for chip in chips:
            if chip not in existing:
                # Insert before the stretch spacer (last item), if present
                self._row.insertWidget(self._row.count(), chip)
                all_widgets.append(chip)

        # Show chips that are in the visible set; hide everything else
        visible_set = set(chips)
        for w in all_widgets:
            w.setVisible(w in visible_set)

        self._inner.adjustSize()

        # Reset pan to the left and (re)start the pan timer
        sb = self.horizontalScrollBar()
        sb.setValue(0)
        self._pan_direction = 1
        self._pan_pausing = False
        self._pause_timer.stop()

        # Only pan if content is actually wider than the viewport
        if self._inner.sizeHint().width() > self.viewport().width():
            self._pan_timer.start()
        else:
            self._pan_timer.stop()

    # ── pan animation ──────────────────────────────────────────────────────

    def _pan_tick(self):
        """Nudge the scroll position by one step; reverse at the ends."""
        if self._pan_pausing:
            return

        sb = self.horizontalScrollBar()
        new_val = sb.value() + self._PAN_SPEED_PX * self._pan_direction

        if new_val >= sb.maximum():
            sb.setValue(sb.maximum())
            self._begin_pause(reverse_to=-1)
        elif new_val <= sb.minimum():
            sb.setValue(sb.minimum())
            self._begin_pause(reverse_to=1)
        else:
            sb.setValue(new_val)

    def _begin_pause(self, reverse_to: int):
        """Pause scrolling at an end for a moment, then reverse."""
        self._pan_pausing = True
        self._pan_direction = reverse_to
        self._pause_timer.start(self._PAN_PAUSE_MS)

    def _end_pause(self):
        self._pan_pausing = False
