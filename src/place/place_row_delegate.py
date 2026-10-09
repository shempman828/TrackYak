"""Painted row for the place tree: type-color dot, name, type pill, a
data-gap warning mark, and a right-aligned association count badge.

The row background (hover/selection) is drawn by the style, so it follows
the QTreeWidget::item rules in dark_mode.qss; only the content is painted
here. Row data is precomputed into item roles by the list (see the *_ROLE
constants) so painting never touches the ORM.
"""

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPen
from PySide6.QtWidgets import QStyle, QStyledItemDelegate, QStyleOptionViewItem

from src.place.place_types import type_color

TYPE_LABEL_ROLE = Qt.UserRole + 2  # str: normalized type label
COUNTS_ROLE = Qt.UserRole + 3  # (direct, recursive) association counts
WARNING_ROLE = Qt.UserRole + 4  # str: data-gap summary, "" when complete

_TEXT = QColor("#b8c0f0")
_TEXT_DIM = QColor("#7a82a8")
_WARN = QColor("#EA8599")
_PILL_BORDER = QColor(133, 153, 234, 64)
_BADGE_BG = QColor(133, 153, 234, 26)

_ROW_HEIGHT = 28
_DOT = 8
_GAP = 8
_PAD = 6


class PlaceRowDelegate(QStyledItemDelegate):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.show_type = True

    def sizeHint(self, option, index):
        height = max(_ROW_HEIGHT, QFontMetrics(option.font).height() + 10)
        return QSize(super().sizeHint(option, index).width(), height)

    def paint(self, painter, option, index):
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.text = ""
        widget = opt.widget
        style = widget.style() if widget else None
        if style:
            style.drawPrimitive(QStyle.PE_PanelItemViewItem, opt, painter, widget)

        name = index.data(Qt.DisplayRole) or ""
        label = index.data(TYPE_LABEL_ROLE) or ""
        _direct, recursive = index.data(COUNTS_ROLE) or (0, 0)
        warning = index.data(WARNING_ROLE) or ""

        rect = QRectF(option.rect).adjusted(_PAD, 0, -_PAD, 0)
        mid_y = rect.center().y()

        painter.save()
        painter.setRenderHint(painter.RenderHint.Antialiasing)

        # Type-color dot (same color as the map marker)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(type_color(label)))
        painter.drawEllipse(QRectF(rect.left(), mid_y - _DOT / 2, _DOT, _DOT))
        left = rect.left() + _DOT + _GAP
        right = rect.right()

        small = QFont(option.font)
        small.setPointSizeF(max(option.font.pointSizeF() * 0.85, 7.0))
        small_fm = QFontMetrics(small)

        # Count badge, right-aligned
        if recursive:
            text = str(recursive)
            width = small_fm.horizontalAdvance(text) + 12
            badge = QRectF(right - width, mid_y - 9, width, 18)
            painter.setBrush(_BADGE_BG)
            painter.drawRoundedRect(badge, 9, 9)
            painter.setFont(small)
            painter.setPen(_TEXT_DIM)
            painter.drawText(badge, Qt.AlignCenter, text)
            right = badge.left() - _GAP

        # Data-gap warning mark
        if warning:
            painter.setFont(option.font)
            painter.setPen(_WARN)
            mark_w = QFontMetrics(option.font).horizontalAdvance("⚠") + 2
            painter.drawText(QRectF(right - mark_w, rect.top(), mark_w, rect.height()), Qt.AlignCenter, "⚠")
            right -= mark_w + _GAP

        # Type pill
        if self.show_type and label:
            width = small_fm.horizontalAdvance(label) + 14
            if right - width > left + 60:
                pill = QRectF(right - width, mid_y - 9, width, 18)
                painter.setBrush(Qt.NoBrush)
                painter.setPen(QPen(_PILL_BORDER, 1))
                painter.drawRoundedRect(pill, 9, 9)
                painter.setFont(small)
                painter.setPen(_TEXT_DIM)
                painter.drawText(pill, Qt.AlignCenter, label)
                right = pill.left() - _GAP

        # Name, elided to whatever is left
        painter.setFont(option.font)
        painter.setPen(_TEXT)
        fm = QFontMetrics(option.font)
        name_rect = QRectF(left, rect.top(), max(right - left, 0), rect.height())
        painter.drawText(name_rect, Qt.AlignVCenter | Qt.AlignLeft, fm.elidedText(name, Qt.ElideRight, int(name_rect.width())))
        painter.restore()
