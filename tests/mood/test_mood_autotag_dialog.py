"""Tests for the word-review helpers in
src/mood/mood_autotag_dialog.py (docs/specs/lyrics_mood_tagging.md).
Maps to AC14 -- assigning a suggested word to a mood appends it to
assets/mood_keywords.json, verified by re-reading the file.

Only the pure file-read/write helper and the word-assignment filter are
exercised here (no live QDialog/controller needed) -- those two pieces are
what AC14 actually specifies.
"""

import json

from PySide6.QtWidgets import QAbstractItemView

from src.mood.mood_autotag_dialog import (
    MoodAutoTagDialog,
    append_keyword_to_mood_file,
    dismiss_word,
    keyword_to_moods,
    load_dismissed_words,
    remove_keyword_from_mood_file,
    undismiss_word,
)


def _write_keywords(tmp_path, data):
    path = tmp_path / "mood_keywords.json"
    path.write_text(json.dumps(data))
    return path


# AC14 --------------------------------------------------------------------------
def test_append_keyword_adds_word_to_existing_mood(tmp_path):
    path = _write_keywords(tmp_path, {"Happy": ["sunshine"]})

    changed = append_keyword_to_mood_file(path, "Happy", "grinning")

    assert changed is True
    reloaded = json.loads(path.read_text())
    assert reloaded["Happy"] == ["sunshine", "grinning"]


def test_append_keyword_creates_new_mood_entry(tmp_path):
    path = _write_keywords(tmp_path, {"Happy": ["sunshine"]})

    append_keyword_to_mood_file(path, "Brand New Mood", "gizmo")

    reloaded = json.loads(path.read_text())
    assert reloaded["Brand New Mood"] == ["gizmo"]
    # Existing moods untouched.
    assert reloaded["Happy"] == ["sunshine"]


def test_append_keyword_is_a_noop_if_already_present(tmp_path):
    path = _write_keywords(tmp_path, {"Happy": ["sunshine"]})

    changed = append_keyword_to_mood_file(path, "Happy", "sunshine")

    assert changed is False
    reloaded = json.loads(path.read_text())
    assert reloaded["Happy"] == ["sunshine"]


def test_assigned_words_includes_component_words_of_phrases(tmp_path, monkeypatch):
    path = _write_keywords(
        tmp_path, {"Party": ["dance floor"], "Sad": ["crying"]}
    )
    monkeypatch.setattr(
        "src.mood.mood_autotag_dialog._KEYWORDS_PATH", path
    )

    assigned = MoodAutoTagDialog._assigned_words()

    assert assigned == {"dance", "floor", "crying"}


# Multi-mood assignment, editing, and dismiss/neutral --------------------------


def test_keyword_can_be_assigned_to_multiple_moods(tmp_path):
    path = _write_keywords(tmp_path, {"Heartbreak": []})

    append_keyword_to_mood_file(path, "Heartbreak", "ex-girlfriend")
    append_keyword_to_mood_file(path, "Sad", "ex-girlfriend")

    reloaded = json.loads(path.read_text())
    assert reloaded["Heartbreak"] == ["ex-girlfriend"]
    assert reloaded["Sad"] == ["ex-girlfriend"]

    moods = keyword_to_moods(reloaded)
    assert moods["ex-girlfriend"] == ["Heartbreak", "Sad"]


def test_keyword_to_moods_dedupes_and_preserves_order():
    raw = {"Happy": ["sunshine"], "Feel-Good": ["sunshine"], "Sad": ["crying"]}

    moods = keyword_to_moods(raw)

    assert moods == {"sunshine": ["Happy", "Feel-Good"], "crying": ["Sad"]}


def test_remove_keyword_from_mood_file_removes_one_association(tmp_path):
    path = _write_keywords(
        tmp_path, {"Heartbreak": ["ex-girlfriend"], "Sad": ["ex-girlfriend"]}
    )

    changed = remove_keyword_from_mood_file(path, "Heartbreak", "ex-girlfriend")

    assert changed is True
    reloaded = json.loads(path.read_text())
    assert reloaded["Heartbreak"] == []
    # The other mood's association survives -- removal is per-mood, not global.
    assert reloaded["Sad"] == ["ex-girlfriend"]


def test_remove_keyword_from_mood_file_is_a_noop_if_absent(tmp_path):
    path = _write_keywords(tmp_path, {"Happy": ["sunshine"]})

    changed = remove_keyword_from_mood_file(path, "Happy", "nonexistent")

    assert changed is False
    reloaded = json.loads(path.read_text())
    assert reloaded["Happy"] == ["sunshine"]


def test_dismiss_and_undismiss_word_round_trip(tmp_path):
    path = tmp_path / "mood_dismissed_words.json"
    path.write_text("[]")

    changed = dismiss_word(path, "yeah")
    assert changed is True
    assert load_dismissed_words(path) == {"yeah"}

    # Dismissing an already-dismissed word is a no-op.
    assert dismiss_word(path, "yeah") is False

    changed = undismiss_word(path, "yeah")
    assert changed is True
    assert load_dismissed_words(path) == set()

    assert undismiss_word(path, "yeah") is False


def test_load_dismissed_words_defaults_to_empty_on_missing_file(tmp_path):
    path = tmp_path / "does_not_exist.json"

    assert load_dismissed_words(path) == set()


# ---------------------------------------------------------------------------
# _WordTable scroll feel (mirrors track_edit_roles.py's _RolesTable, which
# had the same "still scrolls by row despite ScrollPerPixel" bug -- see
# tests/album/test_track_credits_tab.py::
# test_roles_table_wheel_step_is_fixed_not_row_height_derived)
# ---------------------------------------------------------------------------


class _StubGet:
    def get_all_entities(self, model_name, **kwargs):
        return []


class _StubController:
    def __init__(self):
        self.get = _StubGet()


def test_word_table_wheel_step_is_fixed_not_row_height_derived(qapp, monkeypatch):
    """Regression: ScrollPerPixel mode alone still lets Qt derive the
    scrollbar's wheel-notch step (singleStep) from row height, and this
    table's rows vary a lot (mood chips wrap to multiple lines) -- so a
    single wheel notch could still jump as far as the tallest visible row,
    which still reads as "scrolling by row" rather than smoothly."""
    # _load_word_suggestions spins up a real LyricsStatsWorker against
    # controller.statistics.lyrics, which this stub controller doesn't
    # have -- irrelevant to what this test checks, so short-circuit it.
    monkeypatch.setattr(
        MoodAutoTagDialog, "_load_word_suggestions", lambda self: None
    )

    dlg = MoodAutoTagDialog(_StubController())
    try:
        table = dlg._word_table
        table.insertRow(0)
        table.setRowHeight(0, 36)
        table.insertRow(1)
        table.setRowHeight(1, 90)  # a row with several wrapped mood chips

        assert table.verticalScrollMode() == QAbstractItemView.ScrollPerPixel
        step = table.verticalScrollBar().singleStep()
        assert step < 40, (
            f"wheel step ({step}px) scales with the 90px tall row -- "
            "still feels like scrolling by row, not by pixel"
        )
    finally:
        dlg.deleteLater()
