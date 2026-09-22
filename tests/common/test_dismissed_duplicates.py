"""Tests for src/common/dismissed_duplicates.py
(docs/specs/dismiss_duplicate_suggestions.md).

Maps to AC9, AC11, AC12, AC13 -- order-independence, no duplicate entry on
a repeat dismiss, missing/corrupt-file handling, and persistence across a
fresh read of the same file (standing in for an app restart). AC1-8, AC10
are exercised at the dialog/scan level, not here.
"""

import json

from src.common.dismissed_duplicates import dismiss_pair, load_dismissed_pairs


def test_dismiss_pair_round_trip(tmp_path):
    path = tmp_path / "dismissed_duplicates.json"

    changed = dismiss_pair(path, "Artist", 12, 45)
    assert changed is True
    assert load_dismissed_pairs(path, "Artist") == {(12, 45)}


def test_dismiss_pair_is_order_independent(tmp_path):
    path = tmp_path / "dismissed_duplicates.json"

    dismiss_pair(path, "Artist", 45, 12)

    assert load_dismissed_pairs(path, "Artist") == {(12, 45)}


def test_redismissing_an_already_dismissed_pair_is_a_noop(tmp_path):
    path = tmp_path / "dismissed_duplicates.json"

    assert dismiss_pair(path, "Artist", 12, 45) is True
    assert dismiss_pair(path, "Artist", 12, 45) is False
    # Reversed order still counts as the same pair.
    assert dismiss_pair(path, "Artist", 45, 12) is False

    assert load_dismissed_pairs(path, "Artist") == {(12, 45)}


def test_dismissed_pairs_are_namespaced_by_entity_type(tmp_path):
    path = tmp_path / "dismissed_duplicates.json"

    dismiss_pair(path, "Artist", 5, 6)
    dismiss_pair(path, "Place", 5, 6)

    assert load_dismissed_pairs(path, "Artist") == {(5, 6)}
    assert load_dismissed_pairs(path, "Place") == {(5, 6)}
    assert load_dismissed_pairs(path, "Publisher") == set()


def test_load_dismissed_pairs_defaults_to_empty_on_missing_file(tmp_path):
    path = tmp_path / "does_not_exist.json"

    assert load_dismissed_pairs(path, "Artist") == set()


def test_load_dismissed_pairs_defaults_to_empty_on_corrupt_file(tmp_path):
    path = tmp_path / "dismissed_duplicates.json"
    path.write_text("not valid json{{{")

    assert load_dismissed_pairs(path, "Artist") == set()


def test_load_dismissed_pairs_ignores_malformed_entries(tmp_path):
    path = tmp_path / "dismissed_duplicates.json"
    path.write_text(json.dumps({"Artist": ["12-45", "not-a-pair-either", "abc-def"]}))

    assert load_dismissed_pairs(path, "Artist") == {(12, 45)}


def test_dismiss_pair_persists_across_a_fresh_read(tmp_path):
    path = tmp_path / "dismissed_duplicates.json"
    dismiss_pair(path, "Album", 3, 7)

    # Simulates an app restart: a brand-new read of the same file, no
    # in-memory state carried over.
    reloaded = json.loads(path.read_text())
    assert reloaded == {"Album": ["3-7"]}
    assert load_dismissed_pairs(path, "Album") == {(3, 7)}
