"""Tools-menu dialog for lyrics-based mood/place auto-tagging and mood-keyword review."""

# Two things live here: "Tag Library Now" (runs MoodAutoTagWorker over every
# track with lyrics, additive-only, cancellable), and the filter row that
# drives word review (surfaces frequent lyrics words/phrases via
# LyricsStatsWorker) -- the review table itself, and its mood-chip/dismiss
# mutations, live in mood_word_review_widget.py's MoodWordReviewWidget,
# which this dialog composes.
# Singleton behavior lives in menu_bar.py's show_mood_autotag_dialog(),
# mirroring show_alias_management_dialog()'s lazy-create-once-then-show/
# raise/activate pattern -- this class itself is a plain QDialog.

import time

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
)
from sqlalchemy.exc import SQLAlchemyError

from src.foundation.logger_config import logger
from src.foundation.status_utility import show_status_message
from src.mood.mood_dialog import MoodDialog
from src.mood.mood_tag_worker import MoodAutoTagWorker
from src.mood.mood_word_review_widget import MoodWordReviewWidget
from src.statistics.workers.lyrics_stats_worker import LyricsStatsWorker


class MoodAutoTagDialog(QDialog):
    """Tools-menu dialog for running lyrics-based auto-tagging and reviewing mood keywords."""

    FILTER_UNASSIGNED = 0
    FILTER_ASSIGNED = 1

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self._tag_worker = None
        self._tag_scan_start_time = None
        self._lyrics_stats_worker = None
        self.setWindowTitle("Mood Tagging")
        self.setMinimumWidth(760)
        self.setMinimumHeight(560)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        layout.addWidget(
            QLabel(
                "Automatically tag tracks with moods and known places by "
                "scoring their lyrics against assets/mood_keywords.json. "
                "Tagging tracks (below) only ever adds associations -- "
                "existing track tags (manual or auto) are never changed or "
                "removed."
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

        layout.addWidget(
            QLabel(
                "Review lyrics words and phrases and their mood keyword "
                "assignments. An entry can belong to more than one mood -- "
                "add or remove moods with the chips below -- or dismiss "
                "one as neutral to stop it being suggested."
            )
        )

        filter_row = QHBoxLayout()
        self._filter_combo = QComboBox()
        self._filter_combo.addItems(["Unassigned words", "Assigned words"])
        self._filter_combo.currentIndexChanged.connect(self._on_filter_changed)
        filter_row.addWidget(self._filter_combo)

        self._search_filter = QLineEdit()
        self._search_filter.setPlaceholderText("Filter…")
        filter_row.addWidget(self._search_filter)

        self._show_dismissed_chk = QCheckBox("Show dismissed")
        filter_row.addWidget(self._show_dismissed_chk)

        self._refresh_btn = QPushButton("Refresh Suggestions")
        self._refresh_btn.clicked.connect(self._load_word_suggestions)
        filter_row.addWidget(self._refresh_btn)

        self._word_status_label = QLabel("")
        filter_row.addWidget(self._word_status_label)

        self._new_mood_btn = QPushButton("+ New Mood")
        self._new_mood_btn.clicked.connect(self._create_new_mood)
        filter_row.addWidget(self._new_mood_btn)

        filter_row.addStretch()
        layout.addLayout(filter_row)

        self._word_review = MoodWordReviewWidget(
            self.controller, self._filter_combo, self._search_filter, self._show_dismissed_chk
        )
        layout.addWidget(self._word_review)

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
        self._tag_progress_bar.setFormat("Scanning library…")
        self._tag_scan_start_time = time.monotonic()
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

    def _on_tag_progress(self, scanned, total, mood_tags_added, place_tags_added):
        self._tag_progress_bar.setRange(0, max(total, 1))
        self._tag_progress_bar.setValue(scanned)

        counts = f"{mood_tags_added:,} mood / {place_tags_added:,} place tags added"
        eta = self._estimate_remaining(scanned, total)
        if eta:
            self._tag_progress_bar.setFormat(
                f"%p%  ({scanned:,}/{total:,} tracks, {counts}, ETA: {eta})"
            )
        else:
            self._tag_progress_bar.setFormat(f"%p%  ({scanned:,}/{total:,} tracks, {counts})")

    def _estimate_remaining(self, current: int, total: int) -> str | None:
        """Human-readable ETA string for the current tag-library scan, or
        None if there isn't enough data yet. Same elapsed/rate estimate as
        DuplicateFinderDialog._estimate_remaining."""
        if not self._tag_scan_start_time or current <= 0 or current >= total:
            return None

        elapsed = time.monotonic() - self._tag_scan_start_time
        if elapsed < 1.0:
            return None

        rate = current / elapsed
        if rate <= 0:
            return None

        remaining_seconds = int((total - current) / rate)
        if remaining_seconds < 60:
            return f"{remaining_seconds}s"
        minutes, seconds = divmod(remaining_seconds, 60)
        return f"{minutes}m {seconds:02d}s"

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
        self._tag_worker.wait()
        self._tag_worker = None
        self._reset_tag_controls()

    def _reset_tag_controls(self):
        self._tag_now_btn.setEnabled(True)
        self._tag_cancel_btn.setVisible(False)
        self._tag_cancel_btn.setEnabled(True)
        self._tag_progress_bar.setVisible(False)

    # ------------------------------------------------------------------
    # Word review -- data loading
    # ------------------------------------------------------------------

    def _load_word_suggestions(self):
        if self._lyrics_stats_worker is not None and self._lyrics_stats_worker.isRunning():
            return
        self._refresh_btn.setEnabled(False)
        self._word_status_label.setText("Loading…")
        self._lyrics_stats_worker = LyricsStatsWorker(self.controller.statistics.lyrics)
        self._lyrics_stats_worker.finished.connect(self._on_word_stats_loaded)
        self._lyrics_stats_worker.error.connect(self._on_word_stats_error)
        self._lyrics_stats_worker.start()

    def _on_word_stats_loaded(self, stats):
        self._sync_refresh_btn_enabled()
        self._word_status_label.setText("")
        # Single words and multi-word phrases are merged into one
        # frequency-ranked feed -- the row-level chip/dismiss machinery in
        # MoodWordReviewWidget doesn't care how many words a candidate
        # string has, so a common phrase ("broke my heart") is just as
        # suggestible as a single word and competes for a slot on its own
        # merits.
        combined = list(stats.get("word_suggestions", [])) + list(
            stats.get("phrase_suggestions", [])
        )
        combined.sort(key=lambda item: item[1], reverse=True)
        self._word_review.set_word_cloud_cache(combined)
        self._word_review._refresh_table()

    def _on_word_stats_error(self, message):
        self._sync_refresh_btn_enabled()
        self._word_status_label.setText("")
        logger.error(f"Failed to load lyrics word stats: {message}")

    def _sync_refresh_btn_enabled(self):
        self._refresh_btn.setEnabled(self._filter_combo.currentIndex() != self.FILTER_ASSIGNED)

    # ------------------------------------------------------------------
    # Word review -- filter row
    # ------------------------------------------------------------------

    def _on_filter_changed(self, _index=None):
        self._sync_refresh_btn_enabled()
        self._show_dismissed_chk.setEnabled(
            self._filter_combo.currentIndex() == self.FILTER_UNASSIGNED
        )
        self._word_review._refresh_table()

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
            self._word_review._refresh_known_moods()
            self._word_review._refresh_table()
