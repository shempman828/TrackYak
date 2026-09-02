from typing import Any

from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from src.common.fuzzy_match_dialog import BaseFuzzyMatchDialog
from src.foundation.status_utility import show_status_message

# Longest a publisher name is allowed to render in the match list before
# being elided, so one very long name can't blow out the column width
# every row aligns to.
_MAX_NAME_CHARS = 40


# Fuzzy Match Dialog
# -------------------------
class PublisherFuzzyMatchDialog(BaseFuzzyMatchDialog):
    """Dialog to display fuzzy publisher matches and allow merging."""

    _ENTITY_TYPE = "Publisher"
    _ID_ATTR = "publisher_id"
    _NAME_ATTR = "publisher_name"

    def __init__(self, matches: list[tuple], controller: Any, parent=None):
        super().__init__(matches, controller, "Merge Publishers", parent)

    @staticmethod
    def _display_name(name: str) -> str:
        """Elide overly long publisher names so one long name can't blow out
        the column width every row's radio buttons align to."""
        if len(name) <= _MAX_NAME_CHARS:
            return name
        return name[: _MAX_NAME_CHARS - 1].rstrip() + "…"

    def init_ui(self) -> None:
        layout = QVBoxLayout(self)

        # Instructions
        lbl_instructions = QLabel(
            "✔ Check pairs to merge | 🅐🅑 Select which publisher to keep | ✖ Leave unchecked to ignore"
        )
        layout.addWidget(lbl_instructions)

        # Scrollable match list
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        # A single grid (rather than one QHBoxLayout per pair) keeps the
        # checkbox/radio/score columns aligned across rows regardless of
        # how long any individual publisher name is.
        grid = QGridLayout(content)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(6)

        # Add each match pair with controls. Each pair occupies two grid
        # rows: the controls, then a separator line before the next pair.
        for i, (publisher_a, publisher_b, score) in enumerate(self.matches):
            row = i * 2

            # Checkbox to enable/disable merging for this pair
            chk_merge = QCheckBox()
            chk_merge.setChecked(False)  # Default to unchecked
            grid.addWidget(chk_merge, row, 0)

            # Radio buttons for publisher selection
            radio_a = QRadioButton(self._display_name(publisher_a.publisher_name))
            radio_a.setToolTip(publisher_a.publisher_name)
            radio_a.entity = publisher_a
            radio_b = QRadioButton(self._display_name(publisher_b.publisher_name))
            radio_b.setToolTip(publisher_b.publisher_name)
            radio_b.entity = publisher_b
            radio_a.setChecked(True)  # Default to first publisher

            grid.addWidget(radio_a, row, 1)
            grid.addWidget(radio_b, row, 2)
            grid.addWidget(QLabel(f"Similarity: {score}%"), row, 3)

            if i < len(self.matches) - 1:
                separator = QFrame()
                separator.setFrameShape(QFrame.HLine)
                separator.setFrameShadow(QFrame.Sunken)
                grid.addWidget(separator, row + 1, 0, 1, 4)

            self.match_widgets.append((chk_merge, radio_a, radio_b))

        grid.setColumnStretch(4, 1)
        scroll.setWidget(content)
        layout.addWidget(scroll)

        # Progress bar (hidden until a merge is running)
        self._progress = QProgressBar()
        self._progress.hide()
        layout.addWidget(self._progress)

        self._status_label = QLabel()
        self._status_label.hide()
        layout.addWidget(self._status_label)

        # Action buttons
        btn_box = QHBoxLayout()
        self.btn_merge = QPushButton("Merge Checked Pairs")
        self.btn_merge.clicked.connect(self._perform_merge)
        btn_box.addWidget(self.btn_merge)

        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.clicked.connect(self.reject)
        btn_box.addWidget(self.btn_cancel)

        layout.addLayout(btn_box)

        self._autosize(content)

    def _autosize(self, content: QWidget) -> None:
        """Grow the dialog to fit the match grid, within reason.

        A handful of matches shouldn't leave most of the window empty, and
        hundreds of matches shouldn't blow the dialog past the screen — so
        the natural content size is clamped to a fraction of the available
        screen space, with the configured minimum as a floor.
        """
        hint = content.sizeHint()

        screen = self.screen() or QApplication.primaryScreen()
        available = screen.availableGeometry()

        # Extra room for the scrollbar plus the instructions/buttons/margins
        # that sit outside the scrolled grid itself.
        width = hint.width() + 60
        height = hint.height() + 160

        width = max(self.minimumWidth(), min(width, int(available.width() * 0.9)))
        height = max(self.minimumHeight(), min(height, int(available.height() * 0.9)))

        self.resize(width, height)

    def _notify_no_jobs(self) -> None:
        show_status_message(self, "No pairs were merged (none checked or errors occurred)")
