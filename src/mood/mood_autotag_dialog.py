"""
mood_autotag_dialog.py

MoodAutoTagDialog: Tools-menu singleton dialog for lyrics-based mood/place
auto-tagging (docs/specs/lyrics_mood_tagging.md). Two things live here:

  - "Tag Library Now": runs MoodAutoTagWorker over every track with
    lyrics, additive-only, cancellable.
  - Word review: surfaces frequent lyrics words (via the same
    LyricsStats/LyricsStatsWorker the Lyrics stats tab already uses) that
    aren't in any mood's keyword list yet, so words can be assigned to a
    mood (appending to assets/mood_keywords.json) or a brand-new mood can
    be created for one, via the existing MoodDialog.

Singleton behavior lives in menu_bar.py's show_mood_autotag_dialog(),
mirroring show_alias_management_dialog()'s lazy-create-once-then-show/
raise/activate pattern -- this class itself is a plain QDialog.
"""

import json
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from sqlalchemy.exc import SQLAlchemyError

from src.core.asset_paths import asset
from src.core.logger_config import logger
from src.core.status_utility import show_status_message
from src.lyrics.mood_tag_worker import MoodAutoTagWorker
from src.mood.mood_dialog import MoodDialog
from src.statistics.lyrics_stats_worker import LyricsStatsWorker

_KEYWORDS_PATH = Path(asset("mood_keywords.json"))
WORD_SUGGESTION_LIMIT = 50


def append_keyword_to_mood_file(keywords_path: Path, mood_name: str, word: str) -> bool:
    """Append `word` to `mood_name`'s keyword list in the JSON file at
    `keywords_path`, creating the mood's entry if needed. No-op (returns
    False) if the word is already present. Pulled out of the dialog class
    so this file-write behavior is testable without a live QDialog."""
    raw = json.loads(keywords_path.read_text(encoding="utf-8"))
    keywords = raw.setdefault(mood_name, [])
    if word in keywords:
        return False
    keywords.append(word)
    keywords_path.write_text(
        json.dumps(raw, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return True


class MoodAutoTagDialog(QDialog):
    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self._tag_worker = None
        self._lyrics_stats_worker = None
        self.setWindowTitle("Mood Tagging")
        self.setMinimumWidth(700)
        self.setMinimumHeight(500)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        layout.addWidget(
            QLabel(
                "Automatically tag tracks with moods and known places by "
                "scoring their lyrics against assets/mood_keywords.json. "
                "Only ever adds associations -- existing ones (manual or "
                "auto) are never changed or removed."
            )
        )

        tag_row = QHBoxLayout()
        self._tag_now_btn = QPushButton("Tag Library Now")
        self._tag_now_btn.clicked.connect(self._tag_library_now)
        tag_row.addWidget(self._tag_now_btn)
        self._tag_cancel_btn = QPushButton("Cancel")
        self._tag_cancel_btn.clicked.connect(self._cancel_tagging)
        self._tag_cancel_btn.setVisible(False)
        tag_row.addWidget(self._tag_cancel_btn)
        self._tag_status_label = QLabel("")
        tag_row.addWidget(self._tag_status_label)
        tag_row.addStretch()
        layout.addLayout(tag_row)

        self._tag_progress_bar = QProgressBar()
        self._tag_progress_bar.setVisible(False)
        layout.addWidget(self._tag_progress_bar)

        layout.addWidget(QLabel("Common lyrics words not yet assigned to a mood:"))

        review_row = QHBoxLayout()
        self._refresh_btn = QPushButton("Refresh Suggestions")
        self._refresh_btn.clicked.connect(self._load_word_suggestions)
        review_row.addWidget(self._refresh_btn)
        self._new_mood_btn = QPushButton("+ New Mood")
        self._new_mood_btn.clicked.connect(self._create_new_mood)
        review_row.addWidget(self._new_mood_btn)
        review_row.addStretch()
        layout.addLayout(review_row)

        self._word_table = QTableWidget(0, 3)
        self._word_table.setHorizontalHeaderLabels(["Word", "Tracks", "Assign to"])
        self._word_table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self._word_table)

        button_box = QDialogButtonBox(QDialogButtonBox.Close)
        button_box.rejected.connect(self.reject)
        button_box.accepted.connect(self.accept)
        layout.addWidget(button_box)

        self._load_word_suggestions()

    # ------------------------------------------------------------------
    # Tag Library Now
    # ------------------------------------------------------------------

    def _tag_library_now(self):
        if self._tag_worker is not None:
            return
        self._tag_now_btn.setEnabled(False)
        self._tag_cancel_btn.setVisible(True)
        self._tag_progress_bar.setVisible(True)
        self._tag_progress_bar.setRange(0, 0)  # indeterminate until first progress signal
        show_status_message(self, "Tagging library from lyrics…", duration=0)
        self._tag_worker = MoodAutoTagWorker(self.controller)
        self._tag_worker.progress.connect(self._on_tag_progress)
        self._tag_worker.finished.connect(self._on_tag_finished)
        self._tag_worker.error.connect(self._on_tag_error)
        self._tag_worker.start()

    def _cancel_tagging(self):
        if self._tag_worker is not None:
            self._tag_worker.request_cancel()
            self._tag_cancel_btn.setEnabled(False)

    def _on_tag_progress(self, scanned, total):
        self._tag_progress_bar.setRange(0, max(total, 1))
        self._tag_progress_bar.setValue(scanned)

    def _on_tag_finished(self, scanned, mood_tags_added, place_tags_added):
        self._tag_status_label.setText(
            f"{scanned} track(s) scanned, {mood_tags_added} mood tag(s) added, "
            f"{place_tags_added} place tag(s) added"
        )
        show_status_message(self, "Mood tagging complete.")
        self._tag_worker.wait()
        self._tag_worker = None
        self._reset_tag_controls()

    def _on_tag_error(self, message):
        logger.error(f"Mood auto-tag worker failed: {message}")
        show_status_message(self, f"Mood tagging failed: {message}")
        self._tag_worker = None
        self._reset_tag_controls()

    def _reset_tag_controls(self):
        self._tag_now_btn.setEnabled(True)
        self._tag_cancel_btn.setVisible(False)
        self._tag_cancel_btn.setEnabled(True)
        self._tag_progress_bar.setVisible(False)

    # ------------------------------------------------------------------
    # Word review
    # ------------------------------------------------------------------

    def _load_word_suggestions(self):
        if self._lyrics_stats_worker is not None and self._lyrics_stats_worker.isRunning():
            return
        self._refresh_btn.setEnabled(False)
        self._lyrics_stats_worker = LyricsStatsWorker(self.controller.statistics.lyrics)
        self._lyrics_stats_worker.finished.connect(self._on_word_stats_loaded)
        self._lyrics_stats_worker.error.connect(self._on_word_stats_error)
        self._lyrics_stats_worker.start()

    def _on_word_stats_loaded(self, stats):
        self._refresh_btn.setEnabled(True)
        word_cloud = stats.get("word_cloud", [])
        assigned = self._assigned_words()
        suggestions = [
            (word, count) for word, count in word_cloud if word not in assigned
        ][:WORD_SUGGESTION_LIMIT]
        self._populate_word_table(suggestions)

    def _on_word_stats_error(self, message):
        self._refresh_btn.setEnabled(True)
        logger.error(f"Failed to load lyrics word stats: {message}")

    @staticmethod
    def _assigned_words() -> set:
        """Every individual word already present in some mood's keyword
        list (phrases are split so a component word is also considered
        assigned, not just an exact-phrase match)."""
        try:
            raw = json.loads(_KEYWORDS_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            logger.warning(f"Failed to read mood keyword list: {e}")
            return set()

        words = set()
        for keywords in raw.values():
            for keyword in keywords:
                words.update(keyword.lower().split())
        return words

    def _mood_names(self) -> list:
        try:
            moods = self.controller.get.get_all_entities("Mood") or []
        except SQLAlchemyError as e:
            logger.error(f"Failed to load moods: {e}")
            return []
        return sorted((m.mood_name for m in moods), key=str.lower)

    def _populate_word_table(self, suggestions):
        mood_names = self._mood_names()
        self._word_table.setRowCount(len(suggestions))
        for row, (word, count) in enumerate(suggestions):
            self._word_table.setItem(row, 0, QTableWidgetItem(word))
            count_item = QTableWidgetItem(str(count))
            count_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self._word_table.setItem(row, 1, count_item)

            cell = _make_assign_cell(word, mood_names, self._assign_word_to_mood)
            self._word_table.setCellWidget(row, 2, cell)

    def _assign_word_to_mood(self, word: str, mood_name: str):
        try:
            append_keyword_to_mood_file(_KEYWORDS_PATH, mood_name, word)
        except (OSError, ValueError) as e:
            logger.error(f"Failed to update mood keyword list: {e}")
            QMessageBox.critical(self, "Error", f"Failed to save keyword list: {e}")
            return

        show_status_message(self, f"Assigned '{word}' to {mood_name}.")
        self._load_word_suggestions()

    def _create_new_mood(self):
        dialog = MoodDialog(controller=self.controller, parent=self)
        if dialog.exec_() == QDialog.Accepted:
            mood_data = dialog.get_mood_data()
            try:
                self.controller.add.add_entity("Mood", **mood_data)
            except SQLAlchemyError as e:
                logger.error(f"Error creating mood: {e}")
                QMessageBox.critical(self, "Error", f"Failed to create mood: {e}")
                return
            self._load_word_suggestions()


def _make_assign_cell(word, mood_names, on_assign):
    """Build the (mood combo box + Assign button) widget for one
    word-review table row."""
    container = QWidget()
    row_layout = QHBoxLayout(container)
    row_layout.setContentsMargins(2, 0, 2, 0)

    combo = QComboBox()
    combo.addItems(mood_names)
    row_layout.addWidget(combo)

    assign_btn = QPushButton("Assign")
    assign_btn.clicked.connect(
        lambda: on_assign(word, combo.currentText()) if combo.currentText() else None
    )
    row_layout.addWidget(assign_btn)

    return container
