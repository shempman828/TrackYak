from PySide6.QtCore import Qt
from PySide6.QtGui import QAbstractTextDocumentLayout, QColor, QTextDocument
from PySide6.QtWidgets import (
    QStyle,
    QStyledItemDelegate,
)


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
            .assoc-count { color: #0b0c10; }
            .no-assoc { color: #0b0c10; }
            """
        else:
            css = """
            body { color: #b8c0f0; }
            .place-name { color: #8599ea; }
            .place-type { color: #ea8599; }
            .assoc-count { color: #99ea85; }
            .no-assoc { color: #777777; }
            """

        doc.setDefaultStyleSheet(css)
        doc.setHtml(text)

        # Handle selection highlighting
        if option.state & QStyle.StateFlag.State_Selected:
            painter.fillRect(option.rect, QColor("#eaeaea"))
        else:
            painter.fillRect(option.rect, QColor("transparent"))

        painter.save()
        painter.translate(option.rect.topLeft())

        # Clip and draw
        context = QAbstractTextDocumentLayout.PaintContext()
        doc.documentLayout().draw(painter, context)

        painter.restore()

    def sizeHint(self, option, index):
        """Return the correct height for the HTML-rendered content."""
        doc = QTextDocument()
        doc.setHtml(index.data(Qt.DisplayRole))
        return doc.size().toSize()
