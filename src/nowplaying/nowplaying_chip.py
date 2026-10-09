# ──────────────────────────────────────────────────────────────────────────────
#  Chip widgets
# ──────────────────────────────────────────────────────────────────────────────

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QScrollArea, QWidget


class _Chip(QLabel):
    """Pill-shaped metadata chip: an icon glyph plus a value."""

    def __init__(self, icon_str: str, value: str, parent=None, tooltip: str = ""):
        super().__init__(parent)
        self._icon = icon_str
        self._tooltip = tooltip
        self.setProperty("npChip", True)
        self.setToolTip(tooltip)
        self.set_value(value)

    def set_value(self, value: str):
        """Show ``value`` after the icon; screen readers get the tooltip name plus the value."""
        self.setText(f"{self._icon}  {value}" if self._icon else value)
        self.setAccessibleName(f"{self._tooltip}: {value}" if self._tooltip else value)


class _ScrollingChipRow(QScrollArea):
    """Row of chips with no scrollbar that pans back and forth when the chips overflow."""

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
        self.setWidgetResizable(False)

        self._inner = QWidget()
        self._inner.setProperty("bgTransparent", True)
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

    def set_chips(self, chips: list[_Chip]):
        """Show exactly ``chips``, in that order, and hide every other chip."""
        # Show/hide in place, never setParent(None): that deletes the chip on
        # the Qt side and it goes missing on the next track change.
        all_widgets: list[QWidget] = []
        for i in range(self._row.count()):
            item = self._row.itemAt(i)
            if item and item.widget():
                all_widgets.append(item.widget())

        # removeWidget keeps the parent, so re-inserting puts each chip at its
        # given position no matter which track first showed it.
        for i, chip in enumerate(chips):
            if chip in all_widgets:
                self._row.removeWidget(chip)
            else:
                all_widgets.append(chip)
            self._row.insertWidget(i, chip)

        visible_set = set(chips)
        for w in all_widgets:
            w.setVisible(w in visible_set)

        self._inner.adjustSize()

        # Reset pan to the left
        self.horizontalScrollBar().setValue(0)
        self._pan_direction = 1
        self._pan_pausing = False
        self._pause_timer.stop()
        self._update_pan()

    def _overflows(self) -> bool:
        """True when the chips are wider than the visible area."""
        return self._inner.sizeHint().width() > self.viewport().width()

    def _update_pan(self):
        """Pan only while the chips overflow and the row is on screen."""
        if self.isVisible() and self._overflows():
            if not self._pan_timer.isActive():
                self._pan_timer.start()
            return
        self._pan_timer.stop()
        self._pause_timer.stop()
        self._pan_pausing = False
        if not self._overflows():
            self.horizontalScrollBar().setValue(0)

    def resizeEvent(self, event):
        """Re-check the overflow: a narrower row may now need to pan, a wider one may not."""
        super().resizeEvent(event)
        self._update_pan()

    def showEvent(self, event):
        """Resume panning when the row shows again."""
        super().showEvent(event)
        self._update_pan()

    def hideEvent(self, event):
        """Stop panning while the row is not on screen."""
        super().hideEvent(event)
        self._pan_timer.stop()
        self._pause_timer.stop()
        self._pan_pausing = False

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
        """Continue panning after an end pause."""
        self._pan_pausing = False
