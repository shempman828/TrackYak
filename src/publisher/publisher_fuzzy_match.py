from collections import defaultdict
from collections.abc import Callable
from difflib import SequenceMatcher
import re
from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QApplication, QCheckBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QProgressBar, QPushButton, QRadioButton, QScrollArea, QVBoxLayout, QWidget

from src.common.cancellable_worker import CancellableWorker
from src.common.dialogs.fuzzy_match_dialog import BaseFuzzyMatchDialog
from src.common.widgets.qt_text import esc_amp
from src.foundation.logger_config import logger
from src.foundation.status_utility import show_status_message

# Longest a publisher name is allowed to render in the match list before
# being elided, so one very long name can't blow out the column width
# every row aligns to.
_MAX_NAME_CHARS = 40

# Similarity ratio a pair must reach to count as a likely duplicate.
FUZZY_THRESHOLD = 0.85

_PUNCT_RE = re.compile(r"[^\w\s]")


def _normalise(text: str | None) -> str:
    """Lower-case, strip punctuation, and collapse whitespace for comparison."""
    text = _PUNCT_RE.sub("", (text or "").lower())
    return " ".join(text.split())


def find_publisher_duplicates(
    entries: list[tuple[int, str]], threshold: float = FUZZY_THRESHOLD, is_cancelled: Callable[[], bool] = lambda: False, on_progress: Callable[[int, int], None] | None = None
) -> list[tuple[int, int, int]]:
    """Return (id_a, id_b, score_pct) for every (id, name) pair at or above threshold."""
    # Blocking on the first 3 normalised chars avoids an all-pairs comparison.
    blocks = defaultdict(list)
    for publisher_id, name in entries:
        normalised = _normalise(name)
        if normalised:
            blocks[normalised[:3]].append((publisher_id, normalised))
    blocks = [block for block in blocks.values() if len(block) >= 2]

    total = sum(len(b) * (len(b) - 1) // 2 for b in blocks)
    done = 0
    matches = []
    for block in blocks:
        if is_cancelled():
            break
        for i, (id_a, name_a) in enumerate(block):
            for id_b, name_b in block[i + 1 :]:
                ratio = SequenceMatcher(None, name_a, name_b).ratio()
                if ratio >= threshold:
                    matches.append((min(id_a, id_b), max(id_a, id_b), round(ratio * 100)))
        done += len(block) * (len(block) - 1) // 2
        if on_progress:
            on_progress(done, total)
    return matches


class PublisherFuzzyScanWorker(CancellableWorker):
    """Background worker that finds fuzzy-duplicate publisher id pairs."""

    progress = Signal(int, int)
    finished = Signal(list)
    error = Signal(str)

    def __init__(self, entries: list[tuple[int, str]], threshold: float = FUZZY_THRESHOLD, parent=None):
        super().__init__(parent)
        # Plain (id, name) tuples only -- ORM objects must not be touched off the GUI thread.
        self._entries = entries
        self._threshold = threshold

    def run(self):
        """Run the scan and emit finished(list[(id_a, id_b, score_pct)]) or error(str)."""
        try:
            matches = find_publisher_duplicates(self._entries, self._threshold, lambda: self.is_cancelled, self.progress.emit)
            self.finished.emit(matches)
        except Exception as e:
            # Intentional broad boundary catch: an exception must not kill the thread silently.
            logger.exception("Publisher fuzzy-match scan failed")
            self.error.emit(str(e))


class PublisherFuzzyMatchDialog(BaseFuzzyMatchDialog):
    """Dialog to display fuzzy publisher matches and allow merging."""

    _ENTITY_TYPE = "Publisher"
    _ID_ATTR = "publisher_id"
    _NAME_ATTR = "publisher_name"

    def __init__(self, matches: list[tuple], controller: Any, parent=None):
        super().__init__(matches, controller, "Merge Publishers", parent)

    @staticmethod
    def _display_name(name: str) -> str:
        """Elide a long publisher name and escape '&' for radio-button text."""
        name = name or ""  # '&' is escaped so Qt does not use it as a mnemonic prefix
        if len(name) > _MAX_NAME_CHARS:
            name = name[: _MAX_NAME_CHARS - 1].rstrip() + "…"
        return esc_amp(name)

    def init_ui(self) -> None:
        """Build the match grid, progress feedback, and action buttons."""
        layout = QVBoxLayout(self)

        # Instructions
        lbl_instructions = QLabel("✔ Check pairs to merge | 🅐🅑 Select which publisher to keep | ✖ Leave unchecked to ignore | Dismiss to never suggest a pair again")
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
            score_label = QLabel(f"Similarity: {score}%")
            grid.addWidget(score_label, row, 3)

            row_widgets = [chk_merge, radio_a, radio_b, score_label]

            separator = None
            if i < len(self.matches) - 1:
                separator = QFrame()
                separator.setFrameShape(QFrame.HLine)
                separator.setFrameShadow(QFrame.Sunken)
                grid.addWidget(separator, row + 1, 0, 1, 4)
                row_widgets.append(separator)

            btn_dismiss = QPushButton("✖ Dismiss")
            btn_dismiss.setToolTip("Not a duplicate -- don't suggest this pair again")
            grid.addWidget(btn_dismiss, row, 4)
            row_widgets.append(btn_dismiss)

            widgets_tuple = (chk_merge, radio_a, radio_b)
            btn_dismiss.clicked.connect(lambda _checked=False, a=publisher_a, b=publisher_b, rw=row_widgets, wt=widgets_tuple: self._dismiss_pair(a, b, rw, wt))

            self.match_widgets.append(widgets_tuple)

        grid.setColumnStretch(5, 1)
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
        """Resize the dialog to fit the match grid, clamped to 90% of the screen."""
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
        """Tell the user that no pairs were merged."""
        show_status_message(self, "No pairs were merged (none checked or errors occurred)")
