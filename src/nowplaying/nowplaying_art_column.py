# ──────────────────────────────────────────────────────────────────────────────
#  Art column: art card + slide dots + progress strip as one centred group
# ──────────────────────────────────────────────────────────────────────────────

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QSizePolicy, QWidget


class _SlideDots(QWidget):
    """One dot per distinct slideshow image; the current one is a wider pill.
    Paints nothing when there is only one image."""

    _DOT = 6
    _ACTIVE_W = 16
    _GAP = 7

    _ON = QColor(133, 153, 234, 230)
    _OFF = QColor(184, 192, 240, 70)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setProperty("bgTransparent", True)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._count = 0
        self._index = 0

    def set_count(self, count: int):
        self._count = max(0, count)
        self._index = min(self._index, max(0, self._count - 1))
        self.update()

    def set_index(self, index: int):
        self._index = max(0, min(index, self._count - 1)) if self._count else 0
        self.update()

    def count(self) -> int:
        return self._count

    def index(self) -> int:
        return self._index

    def sizeHint(self) -> QSize:
        return QSize(80, self._DOT + 4)

    def paintEvent(self, event):
        if self._count <= 1:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        total = (self._count - 1) * (self._DOT + self._GAP) + self._ACTIVE_W
        x = (self.width() - total) / 2
        y = (self.height() - self._DOT) / 2
        r = self._DOT / 2
        for i in range(self._count):
            dot_w = self._ACTIVE_W if i == self._index else self._DOT
            painter.setBrush(self._ON if i == self._index else self._OFF)
            painter.drawRoundedRect(QRectF(x, y, dot_w, self._DOT), r, r)
            x += dot_w + self._GAP
        painter.end()


class _ArtColumn(QWidget):
    """Lays out the art card with the slide dots and the progress strip
    directly under it, and centres the group vertically.

    The art gets the largest square that leaves room for the two rows below.
    The card's widget rect is that square grown by the card's shadow pad, so
    the shadow can paint into this widget's contents margins.
    """

    _GAP = 14

    def __init__(self, art_card, dots: QWidget, progress: QWidget, parent=None):
        super().__init__(parent)
        self.setProperty("bgTransparent", True)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._art_card = art_card
        self._dots = dots
        self._progress = progress
        for w in (art_card, dots, progress):
            w.setParent(self)
        # Rows drawn later sit on top of the card's shadow pad.
        dots.raise_()
        progress.raise_()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._relayout()

    def _relayout(self):
        area = self.contentsRect()
        dots_h = self._dots.sizeHint().height()
        prog_h = self._progress.sizeHint().height()
        below = self._GAP + dots_h + self._GAP + prog_h
        side = max(0, min(area.width(), area.height() - below))
        x = area.x() + (area.width() - side) // 2
        y = area.y() + (area.height() - side - below) // 2

        pad = self._art_card.shadow_pad()
        self._art_card.setGeometry(x - pad, y - pad, side + 2 * pad, side + 2 * pad)
        y += side + self._GAP
        self._dots.setGeometry(x, y, side, dots_h)
        y += dots_h + self._GAP
        self._progress.setGeometry(x, y, side, prog_h)
