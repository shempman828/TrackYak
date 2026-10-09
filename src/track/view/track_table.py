"""
track_table.py — TrackTable (the QTableView both track views use) and its
row delegate.

The delegate paints the track list's row semantics -- the now-playing row,
an explicit badge on the title, star ratings, muted secondary columns, and
right-aligned numbers -- on top of the stylesheet's own item background
(hover/selection still come from dark_mode.qss).

Colors are Qt properties on TrackTable, so the theme sets them like any
other style:

    TrackTable { qproperty-nowPlayingColor: #8599ea; ... }
"""

from pathlib import Path

from PySide6.QtCore import Property, QRect, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath
from PySide6.QtWidgets import QStyle, QStyledItemDelegate, QStyleOptionViewItem, QTableView

# Columns drawn in the primary text color; every other column recedes.
PRIMARY_COLUMNS = frozenset({"track_name", "primary_artist_names"})

# Numeric columns whose display text isn't a plain int/float but still
# reads best right-aligned.
RIGHT_ALIGNED_COLUMNS = frozenset({"duration", "file_size"})

_NOW_PLAYING_GLYPH = "▶"
_EXPLICIT_LABEL = "E"
_CELL_PADDING = 8


class TrackTable(QTableView):
    """QTableView with themable row-semantics colors and now-playing state."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("TrackTable")
        self._now_playing_color = QColor("#8599ea")
        self._muted_color = QColor("#7a82a8")
        self._text_color = QColor("#b8c0f0")
        self._hover_color = QColor("#EA8599")
        self._badge_color = QColor("#EA8599")
        self._rating_color = QColor("#EAD685")
        self._now_playing_path: str | None = None
        self.setMouseTracking(True)

    # ── Now playing ───────────────────────────────────────────────────────

    def set_now_playing(self, path) -> None:
        """Slot for the player's track_changed(Path) signal."""
        self._now_playing_path = str(Path(path)) if path else None
        self.viewport().update()

    def now_playing_path(self) -> str | None:
        return self._now_playing_path

    # ── Themable colors (set from QSS via qproperty-*) ────────────────────

    def _get_now_playing(self):
        return self._now_playing_color

    def _set_now_playing(self, c):
        self._now_playing_color = QColor(c)

    def _get_muted(self):
        return self._muted_color

    def _set_muted(self, c):
        self._muted_color = QColor(c)

    def _get_text(self):
        return self._text_color

    def _set_text(self, c):
        self._text_color = QColor(c)

    def _get_hover(self):
        return self._hover_color

    def _set_hover(self, c):
        self._hover_color = QColor(c)

    def _get_badge(self):
        return self._badge_color

    def _set_badge(self, c):
        self._badge_color = QColor(c)

    def _get_rating(self):
        return self._rating_color

    def _set_rating(self, c):
        self._rating_color = QColor(c)

    nowPlayingColor = Property(QColor, _get_now_playing, _set_now_playing)
    mutedColor = Property(QColor, _get_muted, _set_muted)
    textColor = Property(QColor, _get_text, _set_text)
    hoverColor = Property(QColor, _get_hover, _set_hover)
    badgeColor = Property(QColor, _get_badge, _set_badge)
    ratingColor = Property(QColor, _get_rating, _set_rating)


class TrackRowDelegate(QStyledItemDelegate):
    """Paints one cell of a TrackTable row. `column_keys` maps logical
    column index -> TRACK_FIELDS name (the host's `self.columns` order)."""

    def __init__(self, table: TrackTable, column_keys: list[str]):
        super().__init__(table)
        self._table = table
        self._keys = column_keys
        self._col = {key: i for i, key in enumerate(column_keys)}

    def _sibling_value(self, index, field_name: str, role=Qt.DisplayRole):
        col = self._col.get(field_name)
        if col is None:
            return None
        return index.siblingAtColumn(col).data(role)

    def _is_now_playing(self, index) -> bool:
        playing = self._table.now_playing_path()
        if not playing:
            return False
        path = self._sibling_value(index, "track_file_path")
        return bool(path) and str(Path(path)) == playing

    def paint(self, painter: QPainter, option, index):
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        field_name = self._keys[index.column()] if index.column() < len(self._keys) else ""
        text = opt.text

        # Background only (hover / selection / alternate) from the stylesheet.
        opt.text = ""
        style = opt.widget.style() if opt.widget else self._table.style()
        style.drawControl(QStyle.CE_ItemViewItem, opt, painter, opt.widget)

        table = self._table
        selected = bool(opt.state & QStyle.State_Selected)
        hovered = bool(opt.state & QStyle.State_MouseOver)
        now_playing = self._is_now_playing(index)

        if now_playing:
            color = table.nowPlayingColor
        elif hovered and not selected:
            color = table.hoverColor
        elif selected or field_name in PRIMARY_COLUMNS:
            color = table.textColor
        else:
            color = table.mutedColor

        rect = opt.rect.adjusted(_CELL_PADDING, 0, -_CELL_PADDING, 0)
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        font = QFont(opt.font)
        if now_playing and field_name in PRIMARY_COLUMNS:
            font.setBold(True)
        painter.setFont(font)
        painter.setPen(color)

        if field_name == "track_name":
            if now_playing:
                glyph_w = painter.fontMetrics().horizontalAdvance(_NOW_PLAYING_GLYPH + " ")
                painter.drawText(rect, Qt.AlignLeft | Qt.AlignVCenter, _NOW_PLAYING_GLYPH)
                rect = rect.adjusted(glyph_w, 0, 0, 0)
            if self._sibling_value(index, "is_explicit", Qt.UserRole):
                rect = self._draw_explicit_badge(painter, rect, font)
            painter.setPen(color)
            painter.setFont(font)
        elif field_name == "user_rating":
            self._draw_stars(painter, rect, index.data(Qt.UserRole))
            painter.restore()
            return

        alignment = index.data(Qt.TextAlignmentRole)
        alignment = Qt.AlignmentFlag(alignment) if alignment else Qt.AlignLeft | Qt.AlignVCenter
        elided = painter.fontMetrics().elidedText(text, Qt.ElideRight, rect.width())
        painter.drawText(rect, int(alignment), elided)
        painter.restore()

    def _draw_explicit_badge(self, painter: QPainter, rect: QRect, font: QFont) -> QRect:
        """Small rounded "E" at the right edge of the title cell; returns the
        rect left over for the title text."""
        badge_font = QFont(font)
        badge_font.setBold(True)
        badge_font.setPointSizeF(max(6.0, font.pointSizeF() * 0.75))
        painter.setFont(badge_font)
        fm = painter.fontMetrics()
        w = fm.horizontalAdvance(_EXPLICIT_LABEL) + 8
        h = fm.height() + 2
        badge = QRectF(rect.right() - w + 1, rect.center().y() - h / 2 + 1, w, h)
        bg = QColor(self._table.badgeColor)
        bg.setAlphaF(0.16)
        path = QPainterPath()
        path.addRoundedRect(badge, 3, 3)
        painter.fillPath(path, bg)
        painter.setPen(self._table.badgeColor)
        painter.drawText(badge, Qt.AlignCenter, _EXPLICIT_LABEL)
        return rect.adjusted(0, 0, -(w + 6), 0)

    def _draw_stars(self, painter: QPainter, rect: QRect, value) -> None:
        """user_rating is 0-10 with half steps; show it as five stars."""
        try:
            rating = float(value)
        except (TypeError, ValueError):
            return
        stars = max(0, min(5, round(rating / 2)))
        on_color = self._table.ratingColor
        off_color = QColor(self._table.mutedColor)
        off_color.setAlphaF(0.35)
        fm = painter.fontMetrics()
        x = rect.left()
        for i in range(5):
            painter.setPen(on_color if i < stars else off_color)
            painter.drawText(QRect(x, rect.top(), fm.horizontalAdvance("★"), rect.height()), Qt.AlignLeft | Qt.AlignVCenter, "★")
            x += fm.horizontalAdvance("★") + 1
