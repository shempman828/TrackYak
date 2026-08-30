from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QTextDocument
from PySide6.QtWidgets import QStyle, QStyledItemDelegate



class HtmlDelegate(QStyledItemDelegate):
    """Delegate that renders HTML in QListWidgetItems, preserving selection and styling."""

    def paint(self, painter, option, index):
        text = index.data(Qt.DisplayRole)
        if not text:
            return

        doc = QTextDocument()

        # Inject CSS based on selection state
        if option.state & QStyle.StateFlag.State_Selected:
            css = """
            body { color: #0b0c10; }
            .place-name { color: #0b0c10; }
            .place-type { color: #0b0c10; }
            .place-type-missing { color: #b00020; font-weight: bold; }
            .assoc-count { color: #0b0c10; }
            .no-assoc { color: #0b0c10; }
            """
        else:
            css = """
            body { color: #b8c0f0; }
            .place-name { color: #8599ea; }
            .place-type { color: #ea8599; }
            .place-type-missing { color: #ff5252; font-weight: bold; }
            .assoc-count { color: #99ea85; }
            .no-assoc { color: #777777; }
            """

        doc.setDefaultStyleSheet(css)
        doc.setHtml(text)
        doc.setTextWidth(option.rect.width())

        # Handle selection highlighting
        if option.state & QStyle.StateFlag.State_Selected:
            painter.fillRect(option.rect, QColor("#eaeaea"))
        else:
            painter.fillRect(option.rect, QColor("transparent"))

        painter.save()
        painter.translate(option.rect.topLeft())
        painter.setClipRect(QRectF(0, 0, option.rect.width(), option.rect.height()))

        doc.drawContents(painter)

        painter.restore()

    def sizeHint(self, option, index):
        """Return the correct height for the HTML-rendered content."""
        text = index.data(Qt.DisplayRole)
        if not text:
            return super().sizeHint(option, index)
        doc = QTextDocument()
        doc.setHtml(text)
        if option.rect.width() > 0:
            doc.setTextWidth(option.rect.width())
        return doc.size().toSize()
