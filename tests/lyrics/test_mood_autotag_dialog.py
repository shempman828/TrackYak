"""Tests for the word-review helpers in
src/lyrics/autotag/mood_word_review_widget.py (docs/specs/lyrics_mood_tagging.md).
Maps to AC14 -- assigning a suggested word to a mood appends it to
config/mood_keywords.json, verified by re-reading the file.

Only the pure file-read/write helper and the word-assignment filter are
exercised here (no live QDialog/controller needed) -- those two pieces are
what AC14 actually specifies.
"""

import json

from PySide6.QtWidgets import QAbstractItemView
import pytest

from src.lyrics.autotag import mood_autotag_dialog
from src.lyrics.autotag.mood_autotag_dialog import MoodAutoTagDialog
from src.lyrics.autotag.mood_word_review_widget import _assigned_words, append_keyword_to_mood_file, dismiss_word, keyword_to_moods, load_dismissed_words, remove_keyword_from_mood_file, undismiss_word


def _write_keywords(tmp_path, data):
    path = tmp_path / "mood_keywords.json"
    path.write_text(json.dumps(data))
    return path


# AC14 --------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("initial", "edit", "mood", "word", "changed", "expected"),
    [
        ({"Happy": ["sunshine"]}, "append", "Happy", "grinning", True, {"Happy": ["sunshine", "grinning"]}),
        ({"Happy": ["sunshine"]}, "append", "Brand New Mood", "gizmo", True, {"Happy": ["sunshine"], "Brand New Mood": ["gizmo"]}),
        ({"Happy": ["sunshine"]}, "append", "Happy", "sunshine", False, {"Happy": ["sunshine"]}),
        (None, "append", "Happy", "sunshine", True, {"Happy": ["sunshine"]}),
        # removal is per-mood, not global
        ({"Heartbreak": ["ex-girlfriend"], "Sad": ["ex-girlfriend"]}, "remove", "Heartbreak", "ex-girlfriend", True, {"Heartbreak": [], "Sad": ["ex-girlfriend"]}),
        ({"Happy": ["sunshine"]}, "remove", "Happy", "nonexistent", False, {"Happy": ["sunshine"]}),
        (None, "remove", "Happy", "sunshine", False, None),
    ],
)
def test_keyword_file_edits(tmp_path, initial, edit, mood, word, changed, expected):
    path = _write_keywords(tmp_path, initial) if initial is not None else tmp_path / "does_not_exist.json"
    func = append_keyword_to_mood_file if edit == "append" else remove_keyword_from_mood_file

    assert func(path, mood, word) is changed
    if expected is None:
        assert not path.exists()
    else:
        assert json.loads(path.read_text()) == expected


def test_assigned_words_includes_component_words_of_phrases(tmp_path, monkeypatch):
    path = _write_keywords(tmp_path, {"Party": ["dance floor"], "Sad": ["crying"]})
    monkeypatch.setattr("src.lyrics.autotag.mood_word_review_widget._KEYWORDS_PATH", path)

    assigned = _assigned_words()

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


def test_keyword_to_moods_skips_malformed_mood_value():
    # A hand-edited mood_keywords.json can have a mood value that isn't a
    # list of strings -- it must be dropped, not iterated character-by-
    # character into bogus single-letter "keywords".
    raw = {"Happy": ["sunshine"], "Broken": "not-a-list"}

    moods = keyword_to_moods(raw)

    assert moods == {"sunshine": ["Happy"]}


def test_dismiss_and_undismiss_word_round_trip(tmp_path):
    path = tmp_path / "mood_dismissed_words.json"
    assert load_dismissed_words(path) == set()
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


def test_tag_progress_shows_percent_running_counts_and_eta(qapp, monkeypatch):
    monkeypatch.setattr(MoodAutoTagDialog, "_load_word_suggestions", lambda self: None)

    dlg = MoodAutoTagDialog(_StubController())
    try:
        dlg._tag_scan_start_time = 1000.0
        # 10s elapsed at 5 tracks/s -> 50 more tracks at 5/s = 10s remaining.
        monkeypatch.setattr(mood_autotag_dialog.time, "monotonic", lambda: 1010.0)

        dlg._on_tag_progress(50, 100, 3, 2, 4)

        fmt = dlg._tag_progress_bar.format()
        assert "%p%" in fmt
        assert "50/100 tracks" in fmt
        assert "3 mood / 2 place tags added" in fmt
        assert "4 place(s) awaiting review" in fmt
        assert "ETA: 10s" in fmt
        assert dlg._tag_progress_bar.value() == 50
        assert dlg._tag_progress_bar.maximum() == 100
    finally:
        dlg.deleteLater()


class _StubWorker:
    def __init__(self):
        self.wait_called = False

    def wait(self):
        self.wait_called = True


def test_on_tag_error_waits_for_worker_before_dropping_it(qapp, monkeypatch):
    """Regression: dropping the worker reference without wait() risks
    "QThread destroyed while still running" if run()'s cleanup hasn't
    finished yet -- _on_tag_finished already waits, _on_tag_error must too.
    """
    monkeypatch.setattr(MoodAutoTagDialog, "_load_word_suggestions", lambda self: None)

    dlg = MoodAutoTagDialog(_StubController())
    try:
        worker = _StubWorker()
        dlg._tag_worker = worker

        dlg._on_tag_error("boom")

        assert worker.wait_called is True
        assert dlg._tag_worker is None
    finally:
        dlg.deleteLater()
