"""Reusable titled card for entity detail panes."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget


class DetailCard(QFrame):
    """A titled, styled section container for a detail-pane widget.

    Add content to `.body` (a QVBoxLayout) the way you would to any layout:
    `card.body.addWidget(...)` / `card.body.addLayout(...)`.

    The header row can also carry a count badge (`set_count`) and/or a
    right-aligned action widget (`add_header_action`); neither is required.
    """

    def __init__(self, title: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("DetailCard")
        self.setFrameStyle(QFrame.StyledPanel)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 12, 14, 14)
        outer.setSpacing(8)

        header = QWidget()
        header.setObjectName("DetailCardHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 8)
        header_layout.setSpacing(8)

        self.title_label = QLabel(title.upper())
        self.title_label.setObjectName("DetailCardTitle")
        header_layout.addWidget(self.title_label, alignment=Qt.AlignVCenter)

        self.count_badge = QLabel()
        self.count_badge.setProperty("badgeState", "neutral")
        self.count_badge.hide()
        header_layout.addWidget(self.count_badge, alignment=Qt.AlignVCenter)

        header_layout.addStretch()

        self._header_actions = QHBoxLayout()
        self._header_actions.setSpacing(6)
        header_layout.addLayout(self._header_actions, stretch=0)
        header_layout.setAlignment(self._header_actions, Qt.AlignVCenter)

        outer.addWidget(header)

        self.body = QVBoxLayout()
        self.body.setSpacing(6)
        outer.addLayout(self.body)

    def set_count(self, count: int, noun: str = "") -> None:
        """Show a small pill badge next to the title, e.g. "4 places".

        `noun` is the singular form; an "s" is appended when count != 1.
        Call with `count=0` to hide the badge again.
        """
        if not count:
            self.count_badge.hide()
            return
        label = f"{count} {noun}{'s' if noun and count != 1 else ''}".strip()
        self.count_badge.setText(label)
        self.count_badge.show()

    def add_header_action(self, widget: QWidget) -> None:
        """Place `widget` (typically a QPushButton) in the header's right-aligned action slot."""
        self._header_actions.addWidget(widget)
