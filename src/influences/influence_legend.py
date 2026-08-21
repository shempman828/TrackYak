from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from src.common.layout_utils import clear_layout
from src.core.config_setup import app_config
from src.display.display_settings import apply_scaled_style


class LegendRow(QWidget):
    """One legend entry: a color swatch plus the cluster's name/count."""

    def __init__(self, color, count, name, parent=None):
        super().__init__(parent)

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)

        swatch = QLabel()
        swatch.setFixedSize(10, 10)
        apply_scaled_style(swatch, f"background-color: {color.name()}; border-radius: 3px;")
        row.addWidget(swatch)

        label_text = (
            f"{name} ({count})" if name else f"{count} artist{'s' if count != 1 else ''}"
        )
        row.addWidget(QLabel(label_text))
        row.addStretch()


class LegendPanel(QFrame):
    """Small floating overlay explaining what the node colors mean.

    User-resizable from any edge or corner, and movable by dragging the
    title bar, so cluster lists longer than the default size can be
    reviewed without a hard-coded row cap; overflow beyond the chosen size
    just scrolls. Size and position are persisted across sessions once the
    user interacts with either.
    """

    _MIN_WIDTH = 160
    _MIN_HEIGHT = 90
    _MAX_WIDTH = 480
    _MAX_HEIGHT = 640
    _EDGE_MARGIN = 8

    _CURSOR_BY_MODE = {
        "left": Qt.SizeHorCursor,
        "right": Qt.SizeHorCursor,
        "top": Qt.SizeVerCursor,
        "bottom": Qt.SizeVerCursor,
        "top-left": Qt.SizeFDiagCursor,
        "bottom-right": Qt.SizeFDiagCursor,
        "top-right": Qt.SizeBDiagCursor,
        "bottom-left": Qt.SizeBDiagCursor,
    }

    def __init__(self, parent=None, on_interact=None, on_rename_all=None, on_level_changed=None):
        super().__init__(parent)
        self._on_interact = on_interact
        self._on_level_changed = on_level_changed
        self.setAttribute(Qt.WA_StyledBackground, True)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(10, 8, 10, 8)
        self._layout.setSpacing(4)

        header = QHBoxLayout()
        header.setSpacing(6)
        self._title = QLabel("Clusters")
        apply_scaled_style(self._title, "font-weight: 600; font-size: 11px;")
        self._title.setCursor(Qt.SizeAllCursor)
        self._title.installEventFilter(self)
        header.addWidget(self._title)
        header.addStretch()
        if on_rename_all is not None:
            rename_button = QPushButton("Rename…")
            rename_button.setCursor(Qt.PointingHandCursor)
            rename_button.setFlat(True)
            rename_button.clicked.connect(on_rename_all)
            header.addWidget(rename_button)
        self._layout.addLayout(header)

        self._level_row = QHBoxLayout()
        self._level_row.setSpacing(4)
        self._level_group = QButtonGroup(self)
        self._level_group.setExclusive(True)
        self._level_group.idClicked.connect(self._on_level_button_clicked)
        self._layout.addLayout(self._level_row)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._rows_widget = QWidget()
        self._rows_layout = QVBoxLayout(self._rows_widget)
        self._rows_layout.setContentsMargins(0, 0, 0, 0)
        self._rows_layout.setSpacing(4)
        self._scroll.setWidget(self._rows_widget)
        self._layout.addWidget(self._scroll, 1)

        self._resize_mode = ""
        self._resize_start_pos = QPointF()
        self._resize_start_geom = None
        self._dragging = False
        self._drag_start_pos = QPointF()
        self._drag_start_geom = None
        self.setMouseTracking(True)

        width, height = app_config.get_influence_legend_size()
        self.resize(
            self._clamp(width, self._MIN_WIDTH, self._MAX_WIDTH),
            self._clamp(height, self._MIN_HEIGHT, self._MAX_HEIGHT),
        )
        saved_pos = app_config.get_influence_legend_position()
        self._user_positioned = saved_pos is not None
        if saved_pos is not None:
            self.move(*saved_pos)
        self.hide()

    def has_custom_position(self):
        """True once the user has dragged or resized the panel, meaning it
        no longer tracks the default bottom-left anchor."""
        return self._user_positioned

    def clamp_to_parent(self):
        """Keep the panel fully inside the parent view after a window resize."""
        if self.parentWidget() is None:
            return
        self.move(self._clamp_point_to_parent(self.pos()))

    def _clamp_point_to_parent(self, point):
        parent = self.parentWidget()
        if parent is None:
            return point
        max_x = max(0, parent.width() - self.width())
        max_y = max(0, parent.height() - self.height())
        return QPoint(self._clamp(point.x(), 0, max_x), self._clamp(point.y(), 0, max_y))

    def set_level_count(self, count, active_level):
        """Show a level-toggle button per eligible dendrogram level (level 0
        = finest/most granular). Hidden entirely when there's only one
        eligible level -- nothing to toggle between."""
        for button in self._level_group.buttons():
            self._level_group.removeButton(button)
        while self._level_row.count():
            item = self._level_row.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        if count <= 1:
            return

        for level in range(count):
            button = QPushButton(f"L{level}")
            button.setCheckable(True)
            button.setChecked(level == active_level)
            button.setCursor(Qt.PointingHandCursor)
            button.setFlat(True)
            button.setFixedHeight(20)
            self._level_group.addButton(button, level)
            self._level_row.addWidget(button)
        self._level_row.addStretch()

    def _on_level_button_clicked(self, level):
        if self._on_level_changed is not None:
            self._on_level_changed(level)

    def set_communities(self, rows):
        clear_layout(self._rows_layout)

        if len(rows) <= 1:
            self.hide()
            return

        for _community_index, color, count, name, _representative_artists in rows:
            self._rows_layout.addWidget(LegendRow(color, count, name))
        self._rows_layout.addStretch()
        self.show()

    # -----------------------
    # Resize (drag any edge or corner) and move (drag the title bar)
    # -----------------------
    def _hit_test(self, pos):
        m = self._EDGE_MARGIN
        w, h = self.width(), self.height()
        left = pos.x() <= m
        right = pos.x() >= w - m
        top = pos.y() <= m
        bottom = pos.y() >= h - m
        if top and left:
            return "top-left"
        if top and right:
            return "top-right"
        if bottom and left:
            return "bottom-left"
        if bottom and right:
            return "bottom-right"
        if left:
            return "left"
        if right:
            return "right"
        if top:
            return "top"
        if bottom:
            return "bottom"
        return ""

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            mode = self._hit_test(event.position().toPoint())
            if mode:
                self._resize_mode = mode
                self._resize_start_pos = event.globalPosition()
                self._resize_start_geom = self.geometry()
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._resize_mode:
            self._apply_resize(event.globalPosition())
            event.accept()
            return
        mode = self._hit_test(event.position().toPoint())
        cursor = self._CURSOR_BY_MODE.get(mode)
        if cursor is not None:
            self.setCursor(cursor)
        else:
            self.unsetCursor()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._resize_mode and event.button() == Qt.LeftButton:
            self._resize_mode = ""
            self._persist_geometry()
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def _apply_resize(self, global_pos):
        start = self._resize_start_geom
        delta = global_pos - self._resize_start_pos
        x, y = start.x(), start.y()
        width, height = start.width(), start.height()

        if "left" in self._resize_mode:
            width = self._clamp(start.width() - delta.x(), self._MIN_WIDTH, self._MAX_WIDTH)
            x = start.x() + (start.width() - width)
        elif "right" in self._resize_mode:
            width = self._clamp(start.width() + delta.x(), self._MIN_WIDTH, self._MAX_WIDTH)

        if "top" in self._resize_mode:
            height = self._clamp(start.height() - delta.y(), self._MIN_HEIGHT, self._MAX_HEIGHT)
            y = start.y() + (start.height() - height)
        elif "bottom" in self._resize_mode:
            height = self._clamp(start.height() + delta.y(), self._MIN_HEIGHT, self._MAX_HEIGHT)

        self.setGeometry(int(x), int(y), int(width), int(height))
        self._user_positioned = True
        if self._on_interact is not None:
            self._on_interact()

    def eventFilter(self, obj, event):
        if obj is self._title:
            if event.type() == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
                self._dragging = True
                self._drag_start_pos = event.globalPosition()
                self._drag_start_geom = self.geometry()
                return True
            if event.type() == QEvent.MouseMove and self._dragging:
                delta = event.globalPosition() - self._drag_start_pos
                new_pos = self._drag_start_geom.topLeft() + delta.toPoint()
                self.move(self._clamp_point_to_parent(new_pos))
                return True
            if event.type() == QEvent.MouseButtonRelease and self._dragging:
                self._dragging = False
                self._user_positioned = True
                self._persist_geometry()
                return True
        return super().eventFilter(obj, event)

    def _persist_geometry(self):
        app_config.set_influence_legend_size(self.width(), self.height())
        app_config.set_influence_legend_position(self.x(), self.y())
        app_config.save()

    @staticmethod
    def _clamp(value, minimum, maximum):
        return max(minimum, min(maximum, value))
