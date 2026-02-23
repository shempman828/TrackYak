from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QToolButton,
    QFrame,
    QLabel,
    QSizePolicy,
)
from PySide6.QtCore import Qt, QPropertyAnimation, QEasingCurve


class SectionContainer(QWidget):
    def __init__(
        self,
        title: str,
        collapsible: bool = True,
        collapsed: bool = False,
        parent=None,
    ):
        super().__init__(parent)

        self._title = title
        self._collapsible = collapsible

        # --- Root layout ---
        self.root_layout = QVBoxLayout(self)
        self.root_layout.setContentsMargins(0, 0, 0, 0)
        self.root_layout.setSpacing(6)

        # --- Header ---
        self.header = QWidget()
        self.header_layout = QHBoxLayout(self.header)
        self.header_layout.setContentsMargins(0, 0, 0, 0)
        self.header_layout.setSpacing(6)

        self.toggle_button = QToolButton()
        self.toggle_button.setText(title)
        self.toggle_button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.toggle_button.setArrowType(Qt.DownArrow)
        self.toggle_button.setCheckable(True)
        self.toggle_button.setChecked(not collapsed)
        self.toggle_button.setEnabled(collapsible)
        self.toggle_button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        self.header_layout.addWidget(self.toggle_button)
        self.header_layout.addStretch()

        self.root_layout.addWidget(self.header)

        # --- Content area ---
        self.content_area = QFrame()
        self.content_area.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        self.content_layout = QVBoxLayout(self.content_area)
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        self.content_layout.setSpacing(8)

        self.root_layout.addWidget(self.content_area)

        # --- Animation ---
        self.animation = QPropertyAnimation(self.content_area, b"maximumHeight")
        self.animation.setDuration(180)
        self.animation.setEasingCurve(QEasingCurve.InOutCubic)

        # Initial state
        self.content_area.setMaximumHeight(
            self._content_height() if not collapsed else 0
        )
        self.content_area.setVisible(not collapsed)

        # Signals
        self.toggle_button.toggled.connect(self._toggle)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_title(self, title: str):
        self._title = title
        self.toggle_button.setText(title)

    def set_collapsed(self, collapsed: bool):
        if not self._collapsible:
            return
        self.toggle_button.setChecked(not collapsed)

    def set_content(self, widget: QWidget):
        self.clear_content()
        self.content_layout.addWidget(widget)
        self._refresh_height()

    def add_widget(self, widget: QWidget):
        self.content_layout.addWidget(widget)
        self._refresh_height()

    def set_empty(self, message="No information available"):
        label = QLabel(message)
        label.setObjectName("EmptyState")
        label.setWordWrap(True)
        self.set_content(label)

    def clear_content(self):
        while self.content_layout.count():
            item = self.content_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _toggle(self, expanded: bool):
        self.toggle_button.setArrowType(Qt.DownArrow if expanded else Qt.RightArrow)

        start = self.content_area.maximumHeight()
        end = self._content_height() if expanded else 0

        self.animation.stop()
        self.animation.setStartValue(start)
        self.animation.setEndValue(end)

        if expanded:
            self.content_area.setVisible(True)

        self.animation.start()

        if not expanded:
            # Hide at the end to avoid tab focus issues
            self.animation.finished.connect(lambda: self.content_area.setVisible(False))

    def _content_height(self) -> int:
        """Calculate the natural height of the content."""
        self.content_area.setMaximumHeight(10_000)
        self.content_area.adjustSize()
        return self.content_area.sizeHint().height()

    def _refresh_height(self):
        if self.toggle_button.isChecked():
            self.content_area.setMaximumHeight(self._content_height())
