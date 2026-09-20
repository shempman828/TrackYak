"""Word-review table for MoodAutoTagDialog: surfaces frequent lyrics words/
phrases and lets each be assigned to one or more moods (assets/
mood_keywords.json already allows the same keyword under several moods,
e.g. "ex-girlfriend" under both Heartbreak and Sad) or dismissed as
"neutral" (assets/mood_dismissed_words.json)."""

import json
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from sqlalchemy.exc import SQLAlchemyError

from src.album.album_flowlayout import FlowLayout
from src.common.widgets.entity_completer_edit import EntityCompleterEdit, find_or_create_by_name
from src.common.widgets.qt_text import esc_amp
from src.foundation.asset_paths import asset
from src.foundation.logger_config import logger
from src.foundation.status_utility import show_status_message

_KEYWORDS_PATH = Path(asset("mood_keywords.json"))
_DISMISSED_PATH = Path(asset("mood_dismissed_words.json"))
WORD_SUGGESTION_LIMIT = 50


def append_keyword_to_mood_file(keywords_path: Path, mood_name: str, word: str) -> bool:
    """Append `word` to `mood_name`'s keyword list in the JSON file at `keywords_path`."""
    # Creates the mood's entry, and the file itself (from an empty mapping),
    # if either doesn't exist yet. No-op (returns False) if the word is
    # already present. Pulled out of the widget class so this file-write
    # behavior is testable without a live QWidget.
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
    """Remove `word` from `mood_name`'s keyword list."""
    # No-op (returns False) if the mood or the word within it doesn't exist
    # -- including when the file itself doesn't exist yet, which is just an
    # empty-keywords case.
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
    """Every literal keyword/phrase across all moods, mapped to the mood names that contain it."""
    # The reverse of the mood->keywords shape `raw` is stored in; this is
    # what lets a word carry more than one mood in the review UI (insertion-
    # ordered, deduped per word).
    result: dict = {}
    for mood_name, keywords in raw.items():
        # The file is hand-editable, so a mood's value can be malformed
        # (e.g. a bare string instead of a list) -- skip it rather than
        # silently iterating its characters as one-letter "keywords".
        if not isinstance(keywords, list) or not all(isinstance(kw, str) for kw in keywords):
            logger.warning(f"Ignoring malformed keyword list for mood '{mood_name}'")
            continue
        for kw in keywords:
            moods = result.setdefault(kw, [])
            if mood_name not in moods:
                moods.append(mood_name)
    return result


def load_dismissed_words(path: Path) -> set:
    """Words marked "neutral" -- excluded from suggestions unless explicitly shown."""
    # Missing/corrupt file reads as no dismissed words.
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


def _assigned_words() -> set:
    """Every individual word already present in some mood's keyword list
    (phrases are split so a component word is also considered assigned,
    not just an exact-phrase match) -- used to keep a word that's already
    part of some keyword phrase out of the raw word-cloud suggestion list."""
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


class _WordTable(QTableWidget):
    """QTableWidget that keeps row heights matched to wrapped mood-chip content."""

    # Column 1 stretches to fill available width, so how many chip lines fit
    # per row changes whenever the widget is resized -- Qt doesn't recompute
    # row heights on its own when a stretched column's width changes, so we
    # do it explicitly here. Mirrors track_edit_roles.py's _RolesTable.
    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.resizeRowsToContents()


class MoodWordReviewWidget(QWidget):
    """Table of lyrics words/phrases with per-word mood-chip and dismiss controls.

    Reads `filter_combo`/`search_filter`/`show_dismissed_chk` (owned and laid
    out by the host dialog's filter row) to decide which rows to show; the
    host is expected to call `_refresh_table()` after loading a fresh word
    cloud (`set_word_cloud_cache`) or after the known-mood list changes
    elsewhere (`_refresh_known_moods()`, e.g. once a mood is created via the
    host's own "+ New Mood" button).
    """

    FILTER_UNASSIGNED = 0
    FILTER_ASSIGNED = 1

    def __init__(self, controller, filter_combo, search_filter, show_dismissed_chk, parent=None):
        super().__init__(parent)
        self.controller = controller
        self._filter_combo = filter_combo
        self._search_filter = search_filter
        self._show_dismissed_chk = show_dismissed_chk
        self._word_cloud_cache = []
        self._known_moods = []
        self._mood_index = {}

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

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._word_table)

        self._search_filter.textChanged.connect(lambda _text: self._refresh_table())
        self._show_dismissed_chk.toggled.connect(lambda _checked: self._refresh_table())

        self._refresh_known_moods()

    def set_word_cloud_cache(self, word_cloud: list) -> None:
        """Set the frequency-ranked word/phrase feed `_refresh_table()` draws
        Unassigned-mode suggestions from (loaded by the host's LyricsStatsWorker)."""
        self._word_cloud_cache = word_cloud

    # ------------------------------------------------------------------
    # Known moods
    # ------------------------------------------------------------------

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
    # Table population
    # ------------------------------------------------------------------

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
            assigned = _assigned_words()
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
            chip = QPushButton(f"{esc_amp(mood_name)}  x")
            chip.setFlat(True)
            chip.setProperty("class", "moodChip")
            chip.setToolTip(f"Remove '{mood_name}' from '{word}'")
            chip.setAccessibleName(f"Remove {mood_name}")
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
    # Mutations
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
