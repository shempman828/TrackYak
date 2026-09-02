"""
mood_autotag_dialog.py

MoodAutoTagDialog: Tools-menu singleton dialog for lyrics-based mood/place
auto-tagging (docs/specs/lyrics_mood_tagging.md). Two things live here:

  - "Tag Library Now": runs MoodAutoTagWorker over every track with
    lyrics, additive-only, cancellable.
  - Word review: surfaces frequent lyrics words and phrases (via the same
    LyricsStats/LyricsStatsWorker the Lyrics stats tab already uses --
    word_suggestions and phrase_suggestions, merged and re-ranked by
    frequency here), lets each be assigned to one or more moods
    (assets/mood_keywords.json
    already allows the same keyword under several moods -- e.g.
    "ex-girlfriend" under both Heartbreak and Sad -- so multi-mood support
    needs no schema change, just a UI that doesn't hide a word the moment
    it has one association), and lets a word be dismissed as "neutral"
    (assets/mood_dismissed_words.json) so it stops being suggested. A
    filter switches between reviewing brand-new suggestions and editing
    moods already assigned to a word.

Singleton behavior lives in menu_bar.py's show_mood_autotag_dialog(),
mirroring show_alias_management_dialog()'s lazy-create-once-then-show/
raise/activate pattern -- this class itself is a plain QDialog.
"""

import json
from pathlib import Path
import time

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from sqlalchemy.exc import SQLAlchemyError

from src.album.album_flowlayout import FlowLayout
from src.common.entity_completer_edit import EntityCompleterEdit, find_or_create_by_name
from src.foundation.asset_paths import asset
from src.foundation.logger_config import logger
from src.foundation.status_utility import show_status_message
from src.mood.mood_dialog import MoodDialog
from src.mood.mood_tag_worker import MoodAutoTagWorker
from src.statistics.workers.lyrics_stats_worker import LyricsStatsWorker

_KEYWORDS_PATH = Path(asset("mood_keywords.json"))
_DISMISSED_PATH = Path(asset("mood_dismissed_words.json"))
WORD_SUGGESTION_LIMIT = 50


def append_keyword_to_mood_file(keywords_path: Path, mood_name: str, word: str) -> bool:
    """Append `word` to `mood_name`'s keyword list in the JSON file at
    `keywords_path`, creating the mood's entry if needed -- and creating
    the file itself, starting from an empty mapping, if it doesn't exist
    yet. No-op (returns False) if the word is already present. Pulled out
    of the dialog class so this file-write behavior is testable without a
    live QDialog."""
    try:
        raw = json.loads(keywords_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raw = {}
    keywords = raw.setdefault(mood_name, [])
    if word in keywords:
        return False
    keywords.append(word)
    keywords_path.write_text(json.dumps(raw, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return True


def remove_keyword_from_mood_file(keywords_path: Path, mood_name: str, word: str) -> bool:
    """Remove `word` from `mood_name`'s keyword list. No-op (returns False)
    if the mood or the word within it doesn't exist -- including when the
    file itself doesn't exist yet, which is just an empty-keywords case."""
    try:
        raw = json.loads(keywords_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raw = {}
    keywords = raw.get(mood_name)
    if not keywords or word not in keywords:
        return False
    keywords.remove(word)
    keywords_path.write_text(json.dumps(raw, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return True


def keyword_to_moods(raw: dict) -> dict:
    """Every literal keyword/phrase across all moods, mapped to the list of
    mood names whose list contains it verbatim (insertion-ordered, deduped).
    This is the reverse of the mood->keywords shape `raw` is stored in, and
    is what lets a word carry more than one mood: the file format already
    allows the same string to appear under several moods independently,
    this just surfaces that as a per-word view for editing."""
    result: dict = {}
    for mood_name, keywords in raw.items():
        for kw in keywords:
            moods = result.setdefault(kw, [])
            if mood_name not in moods:
                moods.append(mood_name)
    return result


def load_dismissed_words(path: Path) -> set:
    """Words marked "neutral" -- excluded from suggestions unless
    explicitly shown. Missing/corrupt file reads as no dismissed words."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        logger.warning(f"Failed to read dismissed word list: {e}")
        return set()
    return set(raw)


def _write_dismissed_words(path: Path, words: set) -> None:
    path.write_text(
        json.dumps(sorted(words), indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def dismiss_word(path: Path, word: str) -> bool:
    words = load_dismissed_words(path)
    if word in words:
        return False
    words.add(word)
    _write_dismissed_words(path, words)
    return True


def undismiss_word(path: Path, word: str) -> bool:
    words = load_dismissed_words(path)
    if word not in words:
        return False
    words.discard(word)
    _write_dismissed_words(path, words)
    return True


class _WordTable(QTableWidget):
    """QTableWidget that keeps row heights matched to wrapped mood-chip
    content. Column 1 stretches to fill available width, so how many chip
    lines fit per row changes whenever the widget is resized -- Qt doesn't
    recompute row heights on its own when a stretched column's width
    changes, so we do it explicitly here. Without this, rows default to a
    single-line height and the chip/completer cell gets squeezed (the
    original combo+button "Assign to" column was cramped for the same
    reason). Mirrors track_edit_roles.py's _RolesTable."""

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.resizeRowsToContents()


class MoodAutoTagDialog(QDialog):
    FILTER_UNASSIGNED = 0
    FILTER_ASSIGNED = 1

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self._tag_worker = None
        self._tag_scan_start_time = None
        self._lyrics_stats_worker = None
        self._word_cloud_cache = []
        self._known_moods = []
        self._mood_index = {}
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
        self._search_filter.textChanged.connect(lambda _text: self._refresh_table())
        filter_row.addWidget(self._search_filter)

        self._show_dismissed_chk = QCheckBox("Show dismissed")
        self._show_dismissed_chk.toggled.connect(lambda _checked: self._refresh_table())
        filter_row.addWidget(self._show_dismissed_chk)

        self._refresh_btn = QPushButton("Refresh Suggestions")
        self._refresh_btn.clicked.connect(self._load_word_suggestions)
        filter_row.addWidget(self._refresh_btn)

        self._new_mood_btn = QPushButton("+ New Mood")
        self._new_mood_btn.clicked.connect(self._create_new_mood)
        filter_row.addWidget(self._new_mood_btn)

        filter_row.addStretch()
        layout.addLayout(filter_row)

        self._word_table = _WordTable(0, 3)
        self._word_table.setHorizontalHeaderLabels(["Word", "Moods", ""])
        self._word_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self._word_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self._word_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self._word_table.horizontalHeader().sectionResized.connect(
            lambda *_args: self._word_table.resizeRowsToContents()
        )
        self._word_table.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        # ScrollPerPixel alone still lets Qt derive the scrollbar's wheel-
        # notch step (singleStep) from row height, and these rows vary a lot
        # (mood chips wrap to multiple lines) -- so a notch could still jump
        # as far as the tallest visible row, which still feels like
        # "scrolling by row." Pin it to a small fixed pixel step instead.
        # Same fix as track_edit_roles.py's _RolesTable.
        self._word_table.verticalScrollBar().setSingleStep(24)
        layout.addWidget(self._word_table)

        button_box = QDialogButtonBox(QDialogButtonBox.Close)
        button_box.rejected.connect(self.reject)
        button_box.accepted.connect(self.accept)
        layout.addWidget(button_box)

        self._refresh_known_moods()
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
        self._lyrics_stats_worker = LyricsStatsWorker(self.controller.statistics.lyrics)
        self._lyrics_stats_worker.finished.connect(self._on_word_stats_loaded)
        self._lyrics_stats_worker.error.connect(self._on_word_stats_error)
        self._lyrics_stats_worker.start()

    def _on_word_stats_loaded(self, stats):
        self._sync_refresh_btn_enabled()
        # Single words and multi-word phrases are merged into one
        # frequency-ranked feed -- the row-level chip/dismiss machinery
        # below doesn't care how many words a candidate string has, so a
        # common phrase ("broke my heart") is just as suggestible as a
        # single word and competes for a slot on its own merits.
        combined = list(stats.get("word_suggestions", [])) + list(
            stats.get("phrase_suggestions", [])
        )
        combined.sort(key=lambda item: item[1], reverse=True)
        self._word_cloud_cache = combined
        self._refresh_table()

    def _on_word_stats_error(self, message):
        self._sync_refresh_btn_enabled()
        logger.error(f"Failed to load lyrics word stats: {message}")

    def _sync_refresh_btn_enabled(self):
        self._refresh_btn.setEnabled(self._filter_combo.currentIndex() != self.FILTER_ASSIGNED)

    @staticmethod
    def _assigned_words() -> set:
        """Every individual word already present in some mood's keyword
        list (phrases are split so a component word is also considered
        assigned, not just an exact-phrase match) -- used to keep a word
        that's already part of some keyword phrase out of the raw
        word-cloud suggestion list."""
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

    def _fetch_moods(self):
        try:
            return self.controller.get.get_all_entities("Mood") or []
        except SQLAlchemyError as e:
            logger.error(f"Failed to load moods: {e}")
            return []

    def _refresh_known_moods(self):
        self._known_moods = sorted(self._fetch_moods(), key=lambda m: (m.mood_name or "").lower())
        self._mood_index = {m.mood_name: m.mood_id for m in self._known_moods if m.mood_name}

    # ------------------------------------------------------------------
    # Word review -- table population
    # ------------------------------------------------------------------

    def _on_filter_changed(self, _index=None):
        self._sync_refresh_btn_enabled()
        self._show_dismissed_chk.setEnabled(
            self._filter_combo.currentIndex() == self.FILTER_UNASSIGNED
        )
        self._refresh_table()

    def _refresh_table(self):
        try:
            raw = json.loads(_KEYWORDS_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            logger.warning(f"Failed to read mood keyword list: {e}")
            raw = {}
        kw_moods = keyword_to_moods(raw)
        dismissed = load_dismissed_words(_DISMISSED_PATH)
        search = self._search_filter.text().strip().lower()

        if self._filter_combo.currentIndex() == self.FILTER_ASSIGNED:
            rows = [
                (word, kw_moods[word], False, None)
                for word in sorted(kw_moods.keys(), key=str.lower)
            ]
        else:
            assigned = self._assigned_words()
            existing_keywords = {k.lower() for k in kw_moods}
            show_dismissed = self._show_dismissed_chk.isChecked()
            rows = []
            for word, count in self._word_cloud_cache:
                # Single-word candidates are covered by _assigned_words()'s
                # split-phrase check (e.g. "dance" is covered by an existing
                # "dance floor" keyword). That check can't catch a
                # multi-word candidate that's already an exact keyword
                # itself ("so alone" isn't equal to any single split word),
                # so phrases get their own exact-match exclusion instead --
                # a *different* new phrase sharing a word with an existing
                # one (e.g. "dance again") is still a legitimate suggestion.
                if " " in word:
                    if word.lower() in existing_keywords:
                        continue
                elif word in assigned:
                    continue
                is_dismissed = word in dismissed
                if is_dismissed and not show_dismissed:
                    continue
                rows.append((word, [], is_dismissed, count))
            rows = rows[:WORD_SUGGESTION_LIMIT]

        if search:
            rows = [r for r in rows if search in r[0].lower()]

        self._populate_rows(rows)

    def _populate_rows(self, rows):
        table = self._word_table
        table.setRowCount(len(rows))
        for row, (word, moods, is_dismissed, count) in enumerate(rows):
            label = word if count is None else f"{word}  ({count})"
            word_item = QTableWidgetItem(label)
            if is_dismissed:
                font = word_item.font()
                font.setItalic(True)
                word_item.setFont(font)
                word_item.setForeground(Qt.gray)
            table.setItem(row, 0, word_item)

            table.setCellWidget(row, 1, self._build_mood_cell(word, moods))

            table.removeCellWidget(row, 2)
            if count is not None:  # an Unassigned-mode row -- Assigned rows get no action
                table.setCellWidget(row, 2, self._build_action_cell(word, is_dismissed))

        table.resizeColumnToContents(0)
        table.resizeColumnToContents(2)
        table.resizeRowsToContents()

    def _build_action_cell(self, word: str, is_dismissed: bool) -> QWidget:
        # A cell widget fills its QTableWidget cell exactly with no margin
        # of its own, so a flat QPushButton placed directly (as the
        # original version did) renders flush against the cell edges --
        # this padded container is what gives it breathing room, matching
        # the Moods cell's chips (which get padding from FlowLayout's own
        # margin) and RolesTab's actions cell.
        cell = QWidget()
        row_layout = QHBoxLayout(cell)
        row_layout.setContentsMargins(6, 4, 6, 4)

        action_btn = QPushButton("Undismiss" if is_dismissed else "Dismiss")
        action_btn.setFlat(True)
        if is_dismissed:
            action_btn.clicked.connect(lambda _checked, w=word: self._undismiss_word(w))
        else:
            action_btn.clicked.connect(lambda _checked, w=word: self._dismiss_word(w))
        row_layout.addWidget(action_btn)
        return cell

    def _build_mood_cell(self, word: str, moods: list) -> QWidget:
        cell = QWidget()
        flow = FlowLayout(cell, margin=4, h_spacing=6, v_spacing=4)

        for mood_name in moods:
            chip = QPushButton(f"{mood_name}  x")
            chip.setFlat(True)
            chip.setProperty("class", "moodChip")
            chip.setToolTip(f"Remove '{mood_name}' from '{word}'")
            chip.clicked.connect(
                lambda _checked, w=word, m=mood_name: self._remove_mood_from_word(w, m)
            )
            flow.addWidget(chip)

        add_edit = EntityCompleterEdit("+ mood…")
        add_edit.setMaximumWidth(140)
        add_edit.set_index(self._mood_index)
        add_edit.returnPressed.connect(
            lambda w=word, edit=add_edit: self._add_moods_to_word(w, edit)
        )
        flow.addWidget(add_edit)
        return cell

    # ------------------------------------------------------------------
    # Word review -- mutations
    # ------------------------------------------------------------------

    def _add_moods_to_word(self, word: str, edit: EntityCompleterEdit):
        names = edit.split_names()
        if not names:
            return

        # matched_id only names a single typed mood -- with several typed at
        # once (e.g. "Sad;Heartbreak") each is resolved by name instead of
        # relying on that one-shot completer pick.
        single_matched_id = edit.matched_id() if len(names) == 1 else None
        moods = []
        try:
            for name in names:
                if single_matched_id is not None:
                    mood = self.controller.get.get_entity_object("Mood", mood_id=single_matched_id)
                else:
                    mood = find_or_create_by_name(
                        self.controller, "Mood", "mood_name", name, self._known_moods
                    )
                if mood:
                    moods.append(mood)
        except SQLAlchemyError as e:
            logger.error(f"Failed to find/create Mood: {e}")
            return
        if not moods:
            return

        try:
            for mood in moods:
                append_keyword_to_mood_file(_KEYWORDS_PATH, mood.mood_name, word)
            undismiss_word(_DISMISSED_PATH, word)
        except (OSError, ValueError) as e:
            logger.error(f"Failed to update mood keyword list: {e}")
            QMessageBox.critical(self, "Error", f"Failed to save keyword list: {e}")
            return

        show_status_message(self, f"Assigned '{word}' to {', '.join(m.mood_name for m in moods)}.")
        self._refresh_known_moods()
        self._refresh_table()

    def _remove_mood_from_word(self, word: str, mood_name: str):
        try:
            remove_keyword_from_mood_file(_KEYWORDS_PATH, mood_name, word)
        except (OSError, ValueError) as e:
            logger.error(f"Failed to update mood keyword list: {e}")
            QMessageBox.critical(self, "Error", f"Failed to save keyword list: {e}")
            return
        self._refresh_table()

    def _dismiss_word(self, word: str):
        try:
            dismiss_word(_DISMISSED_PATH, word)
        except (OSError, ValueError) as e:
            logger.error(f"Failed to update dismissed word list: {e}")
            return
        self._refresh_table()

    def _undismiss_word(self, word: str):
        try:
            undismiss_word(_DISMISSED_PATH, word)
        except (OSError, ValueError) as e:
            logger.error(f"Failed to update dismissed word list: {e}")
            return
        self._refresh_table()

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
            self._refresh_known_moods()
            self._refresh_table()
