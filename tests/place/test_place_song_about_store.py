"""Tests for src/place/place_song_about_store.py: the JSON-backed pending
review queue and decision cache for lyric-detected "Song About" places.
"""

import json

import pytest

from src.place import place_song_about_store as store


@pytest.fixture(autouse=True)
def _isolated_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "_QUEUE_PATH", tmp_path / "place_song_about_review_queue.json")
    monkeypatch.setattr(store, "_DECISIONS_PATH", tmp_path / "place_song_about_decisions.json")
    yield


def test_load_queue_defaults_to_empty_on_missing_file():
    assert store.load_queue() == []


def test_load_decisions_defaults_to_empty_on_missing_file():
    assert store.load_decisions() == {}


def test_enqueue_writes_new_entries():
    store.enqueue([{"track_id": 1, "place_name": "Bath", "place_id": 10}])

    assert store.load_queue() == [{"track_id": 1, "place_name": "Bath", "place_id": 10}]


def test_enqueue_dedupes_by_track_and_place_name():
    store.enqueue([{"track_id": 1, "place_name": "Bath", "place_id": 10}])
    store.enqueue([{"track_id": 1, "place_name": "Bath", "place_id": 10}])

    assert len(store.load_queue()) == 1


def test_enqueue_keeps_different_tracks_for_same_place():
    store.enqueue([{"track_id": 1, "place_name": "Bath", "place_id": 10}])
    store.enqueue([{"track_id": 2, "place_name": "Bath", "place_id": 10}])

    assert len(store.load_queue()) == 2


def test_enqueue_empty_list_does_not_create_file():
    store.enqueue([])

    assert not store._QUEUE_PATH.exists()


def test_save_decision_approved_round_trips():
    store.save_decision("Paris", store.DECISION_APPROVED)

    decisions = store.load_decisions()
    assert decisions["Paris"] == {"decision": store.DECISION_APPROVED}


def test_save_decision_remapped_stores_target_place():
    store.save_decision(
        "Kingston", store.DECISION_REMAPPED, place_id=42, remap_place_name="Kingston, Jamaica"
    )

    decisions = store.load_decisions()
    assert decisions["Kingston"] == {
        "decision": store.DECISION_REMAPPED,
        "place_id": 42,
        "place_name": "Kingston, Jamaica",
    }


def test_save_decision_overwrites_previous_decision_for_same_name():
    store.save_decision("Bath", store.DECISION_REJECTED)
    store.save_decision("Bath", store.DECISION_APPROVED)

    assert store.load_decisions()["Bath"]["decision"] == store.DECISION_APPROVED


def test_remove_place_from_queue_drops_only_matching_entries():
    store.enqueue(
        [
            {"track_id": 1, "place_name": "Bath", "place_id": 10},
            {"track_id": 2, "place_name": "Bath", "place_id": 10},
            {"track_id": 3, "place_name": "Paris", "place_id": 20},
        ]
    )

    removed = store.remove_place_from_queue("Bath")

    assert len(removed) == 2
    remaining = store.load_queue()
    assert len(remaining) == 1
    assert remaining[0]["place_name"] == "Paris"


def test_remove_place_from_queue_is_a_noop_when_absent():
    assert store.remove_place_from_queue("Nowhere") == []
    assert not store._QUEUE_PATH.exists()


def test_queue_file_is_pretty_printed_json():
    store.enqueue([{"track_id": 1, "place_name": "Bath", "place_id": 10}])

    raw = store._QUEUE_PATH.read_text(encoding="utf-8")
    assert json.loads(raw) == [{"track_id": 1, "place_name": "Bath", "place_id": 10}]
    assert raw.endswith("\n")
