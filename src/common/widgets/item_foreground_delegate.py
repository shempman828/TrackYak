"""Item delegate that lets per-item foreground colours show through the theme."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush
from PySide6.QtWidgets import QApplication, QStyle, QStyledItemDelegate, QStyleOptionViewItem


class ItemForegroundDelegate(QStyledItemDelegate):
    """Draws item text in its ForegroundRole colour, which dark_mode.qss would otherwise override."""

    def paint(self, painter, option, index):
        foreground = index.data(Qt.ForegroundRole)
        if foreground is None:
            super().paint(painter, option, index)
            return
        # setForeground() stores a QBrush; setData(ForegroundRole, QColor) stores a QColor.
        color = foreground.color() if isinstance(foreground, QBrush) else foreground

        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        text = opt.text
        opt.text = ""
        style = opt.widget.style() if opt.widget else QApplication.style()
        style.drawControl(QStyle.CE_ItemViewItem, opt, painter, opt.widget)

        opt.text = text  # text rect is measured with the text present
        rect = style.subElementRect(QStyle.SE_ItemViewItemText, opt, opt.widget)
        painter.save()
        painter.setFont(opt.font)
        painter.setPen(color)
        elided = opt.fontMetrics.elidedText(text, opt.textElideMode, rect.width())
        painter.drawText(rect, int(opt.displayAlignment), elided)
        painter.restore()
