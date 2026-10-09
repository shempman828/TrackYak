"""
ItemForegroundDelegate -- lets per-item colours (QTreeWidgetItem.setForeground)
show through the theme.

dark_mode.qss sets `color` on QTreeView::item / QListView::item, and the style
sheet wins over an item's ForegroundRole, so a status colour or a muted count
column set in code never shows. This delegate draws the item normally (row
background, hover, selection, check box, icon) with no text, then draws the
text itself in the item's own foreground colour. Items with no foreground set
are left entirely to the default painting.
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QStyle, QStyledItemDelegate, QStyleOptionViewItem


class ItemForegroundDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        brush = index.data(Qt.ForegroundRole)
        if brush is None:
            super().paint(painter, option, index)
            return

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
        painter.setPen(brush.color())
        elided = opt.fontMetrics.elidedText(text, opt.textElideMode, rect.width())
        painter.drawText(rect, int(opt.displayAlignment), elided)
        painter.restore()
