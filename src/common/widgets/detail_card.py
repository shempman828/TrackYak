"""Reusable titled card for entity detail panes."""

from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout, QWidget


class DetailCard(QFrame):
    """A titled, styled section container for a detail-pane widget.

    Add content to `.body` (a QVBoxLayout) the way you would to any layout:
    `card.body.addWidget(...)` / `card.body.addLayout(...)`.
    """

    def __init__(self, title: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("DetailCard")
        self.setFrameStyle(QFrame.StyledPanel)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 12, 14, 14)
        outer.setSpacing(8)

        title_label = QLabel(title)
        title_label.setObjectName("DetailCardTitle")
        outer.addWidget(title_label)

        self.body = QVBoxLayout()
        self.body.setSpacing(6)
        outer.addLayout(self.body)
