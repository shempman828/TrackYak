from PySide6.QtCore import QRectF
from PySide6.QtGui import QBrush, QColor, QFont, QLinearGradient, QPainter, QPen, Qt
from PySide6.QtWidgets import QGraphicsItem, QGraphicsRectItem, QGraphicsTextItem

# Matches the "Cambria, Georgia, serif" stack the app's QSS themes apply to
# every widget. QGraphicsTextItem isn't a QWidget, so it never picks that up
# automatically and previously fell back to a hardcoded Arial.
_LABEL_FONT_FAMILIES = ["Cambria", "Georgia", "serif"]


def _make_label_font(size):
    font = QFont(_LABEL_FONT_FAMILIES[0], size, QFont.Medium)
    font.setFamilies(_LABEL_FONT_FAMILIES)
    return font


class ArtistNode(QGraphicsRectItem):
    def __init__(self, artist_id, artist_name, x, y, width=80, height=40):
        super().__init__(-width / 2, -height / 2, width, height)
        self.artist_id = artist_id
        self.artist_name = artist_name
        self.setPos(x, y)

        # Colors
        self.box_color = QColor(0xEA, 0x85, 0x99)
        self.text_color = QColor(0x0B, 0x0C, 0x10)
        self.hover_color = QColor(0xF0, 0x95, 0xA8)
        self.border_color = QColor(0xC0, 0x50, 0x68)

        # Corner radius for rounded rect
        self.corner_radius = 6.0

        # Styling — hide the default QPen/QBrush so we control paint() fully
        self.setBrush(QBrush(Qt.NoBrush))
        self.setPen(QPen(Qt.NoPen))
        self.setFlag(QGraphicsItem.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.ItemSendsGeometryChanges, True)
        self.setAcceptHoverEvents(True)

        # Track hover/selected state for paint()
        self._hovered = False
        self._selected_highlight = False

        # Add text label inside the box
        self.text = QGraphicsTextItem("", self)
        self.text.setDefaultTextColor(self.text_color)

        # Store node dimensions for text fitting
        self.node_width = width
        self.node_height = height

        # Set the text with proper font sizing
        self.update_text(artist_name)

        # Animation properties
        self.normal_scale = 1.0
        self.hover_scale = 1.05
        self.original_z_value = self.zValue()

    # ------------------------------------------------------------------
    # Custom paint — rounded rect with border
    # ------------------------------------------------------------------
    def paint(self, painter: QPainter, option, widget=None):
        painter.setRenderHint(QPainter.Antialiasing)

        r = self.rect()

        if self._selected_highlight:
            fill = QColor(0xFF, 0xE0, 0x60)
            border = QColor(0xFF, 0xC0, 0x00)
            border_width = 3.0
        elif self._hovered:
            fill = self.hover_color
            border = self.border_color
            border_width = 2.0
        else:
            fill = self.box_color
            border = self.border_color
            border_width = 1.5

        # Soft manual drop shadow — a single extra rounded-rect fill rather
        # than a QGraphicsDropShadowEffect, which is too costly to apply
        # per-node across graphs with hundreds/thousands of nodes.
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(QColor(0, 0, 0, 60)))
        painter.drawRoundedRect(
            r.translated(0, 2), self.corner_radius, self.corner_radius
        )

        # Hover glow ring, cheap to draw alongside the shadow above.
        if self._hovered and not self._selected_highlight:
            glow = QColor(fill)
            glow.setAlpha(70)
            painter.setBrush(QBrush(glow))
            painter.drawRoundedRect(
                r.adjusted(-4, -4, 4, 4),
                self.corner_radius + 3,
                self.corner_radius + 3,
            )

        # Glassy vertical gradient fill instead of a flat color.
        gradient = QLinearGradient(r.topLeft(), r.bottomLeft())
        gradient.setColorAt(0.0, fill.lighter(122))
        gradient.setColorAt(1.0, fill)

        painter.setBrush(QBrush(gradient))
        painter.setPen(QPen(border, border_width))
        painter.drawRoundedRect(r, self.corner_radius, self.corner_radius)

    def boundingRect(self):
        # Margin covers the shadow offset, hover glow expansion, and AA edges.
        r = self.rect()
        m = 8
        return QRectF(r.x() - m, r.y() - m, r.width() + m * 2, r.height() + m * 2)

    # ------------------------------------------------------------------
    # Size helpers
    # ------------------------------------------------------------------
    def calculate_initial_size(self, text):
        """Calculate initial node size based on text length"""
        base_width = 100
        base_height = 40
        estimated_text_width = len(text) * 7
        width = max(base_width, estimated_text_width + 30)
        height = base_height
        return width, height

    def calculate_optimal_font_size(self, text):
        """Calculate the maximum font size that fits the text within the node"""
        padding = 12
        max_text_width = self.node_width - padding
        max_text_height = self.node_height - padding

        font_size = min(16, int(self.node_height * 0.7))

        for test_size in range(font_size, 10, -1):
            test_font = _make_label_font(test_size)
            temp_text = QGraphicsTextItem(text)
            temp_text.setFont(test_font)
            text_rect = temp_text.boundingRect()

            if (
                text_rect.width() <= max_text_width
                and text_rect.height() <= max_text_height
            ):
                return test_size

        return 10

    def set_community_color(self, base_color: QColor):
        """Recolor the node to match its Louvain community."""
        self.box_color = base_color
        self.hover_color = base_color.lighter(115)
        self.border_color = base_color.darker(140)
        self.update()

    def update_size(self, new_width, new_height):
        """Update the node size and reposition text"""
        self.node_width = new_width
        self.node_height = new_height
        self.setRect(-new_width / 2, -new_height / 2, new_width, new_height)
        self.update_text(self.artist_name)

    def center_text(self):
        """Center the text within the current rectangle"""
        text_rect = self.text.boundingRect()
        self.text.setPos(-text_rect.width() / 2, -text_rect.height() / 2)

    def update_text(self, new_name):
        """Update the artist name with proper font sizing"""
        self.artist_name = new_name

        estimated_width = len(new_name) * 7 + 30
        if estimated_width > self.node_width:
            self.update_size(estimated_width, self.node_height)
            return

        font_size = self.calculate_optimal_font_size(new_name)
        font = _make_label_font(font_size)
        self.text.setFont(font)
        self.text.setPlainText(new_name)
        self.center_text()

    # ------------------------------------------------------------------
    # Hover / selection
    # ------------------------------------------------------------------
    def hoverEnterEvent(self, event):
        self._hovered = True
        self.setScale(self.hover_scale)
        self.setZValue(self.original_z_value + 1)
        self.update()
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event):
        self._hovered = False
        self.setScale(self.normal_scale)
        self.setZValue(self.original_z_value)
        self.update()
        super().hoverLeaveEvent(event)

    def set_selected(self, selected):
        """Highlight the node when selected"""
        self._selected_highlight = selected
        self.setZValue(self.original_z_value + (2 if selected else 0))
        self.update()

    def contextMenuEvent(self, event):
        """Handle right-click context menu"""
        self.show_context_menu()
        event.accept()
