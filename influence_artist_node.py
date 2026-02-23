from PySide6.QtGui import QBrush, QColor, QFont, QPen, Qt
from PySide6.QtWidgets import QGraphicsItem, QGraphicsRectItem, QGraphicsTextItem


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

        # Styling
        self.setBrush(QBrush(self.box_color))
        self.setPen(QPen(Qt.black, 2))
        self.setFlag(QGraphicsItem.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.ItemSendsGeometryChanges, True)
        self.setAcceptHoverEvents(True)

        # Add text label inside the box
        self.text = QGraphicsTextItem("", self)  # Start with empty text
        self.text.setDefaultTextColor(self.text_color)

        # Store node dimensions for text fitting
        self.node_width = width
        self.node_height = height

        # Set the text with proper font sizing
        self.update_text(artist_name)

        # Animation properties
        self.normal_scale = 1.0
        self.hover_scale = 1.05
        self.normal_opacity = 1.0
        self.hover_opacity = 1.0

        # Store original properties for hover effects
        self.original_brush = self.brush()
        self.original_z_value = self.zValue()

    def calculate_initial_size(self, text):
        """Calculate initial node size based on text length"""
        # Base dimensions with padding
        base_width = 100
        base_height = 40

        # Estimate text width - roughly 7px per character for default font
        estimated_text_width = len(text) * 7

        # Add padding and ensure minimum size
        width = max(base_width, estimated_text_width + 30)
        height = base_height

        return width, height

    def calculate_optimal_font_size(self, text):
        """Calculate the maximum font size that fits the text within the node"""
        # More generous padding
        padding = 12
        max_text_width = self.node_width - padding
        max_text_height = self.node_height - padding

        # Start with larger font sizes and have a higher minimum
        font_size = min(16, int(self.node_height * 0.7))  # Larger initial guess

        for test_size in range(font_size, 10, -1):  # Minimum size increased to 10px
            test_font = QFont("Arial", test_size, QFont.Medium)

            # Create a temporary text item to measure
            temp_text = QGraphicsTextItem(text)
            temp_text.setFont(test_font)
            text_rect = temp_text.boundingRect()

            if (
                text_rect.width() <= max_text_width
                and text_rect.height() <= max_text_height
            ):
                return test_size

        return 10  # Increased minimum readable size

    def update_size(self, new_width, new_height):
        """Update the node size and reposition text"""
        self.node_width = new_width
        self.node_height = new_height
        self.setRect(-new_width / 2, -new_height / 2, new_width, new_height)

        # Recalculate font size and reposition text
        self.update_text(self.artist_name)

    def center_text(self):
        """Center the text within the current rectangle"""
        text_rect = self.text.boundingRect()
        self.text.setPos(-text_rect.width() / 2, -text_rect.height() / 2)

    def update_text(self, new_name):
        """Update the artist name with proper font sizing"""
        self.artist_name = new_name

        # Recalculate node size if text is too long
        estimated_width = len(new_name) * 7 + 30
        if estimated_width > self.node_width:
            self.update_size(estimated_width, self.node_height)
            return

        # Calculate optimal font size
        font_size = self.calculate_optimal_font_size(new_name)

        # Set the font and text
        font = QFont("Arial", font_size, QFont.Medium)
        self.text.setFont(font)
        self.text.setPlainText(new_name)

        # Center the text
        self.center_text()

    def hoverEnterEvent(self, event):
        self.setScale(self.hover_scale)
        self.setBrush(QBrush(self.hover_color))
        self.setZValue(self.original_z_value + 1)
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event):
        self.setScale(self.normal_scale)
        self.setBrush(self.original_brush)
        self.setZValue(self.original_z_value)
        super().hoverLeaveEvent(event)

    def set_selected(self, selected):
        """Highlight the node when selected"""
        if selected:
            self.setPen(QPen(Qt.yellow, 3))
            self.setZValue(self.original_z_value + 2)
        else:
            self.setPen(QPen(Qt.black, 2))
            self.setZValue(self.original_z_value)

    def contextMenuEvent(self, event):
        """Handle right-click context menu"""
        self.show_context_menu()
        event.accept()
