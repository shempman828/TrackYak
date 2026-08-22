"""Tests for the word-review helpers in
src/mood/mood_autotag_dialog.py (docs/specs/lyrics_mood_tagging.md).
Maps to AC14 -- assigning a suggested word to a mood appends it to
assets/mood_keywords.json, verified by re-reading the file.

Only the pure file-read/write helper and the word-assignment filter are
exercised here (no live QDialog/controller needed) -- those two pieces are
what AC14 actually specifies.
"""

import json

from src.mood.mood_autotag_dialog import (
    MoodAutoTagDialog,
    append_keyword_to_mood_file,
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
