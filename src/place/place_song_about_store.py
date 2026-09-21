"""JSON persistence for lyric-detected "Song About" place candidates.

detect_known_places() can match a common word (e.g. "Bath", "England") that
isn't really what a lyric is about, so a fresh match is no longer written to
place_associations right away. Instead it's looked up against a decision
cache (config/place_song_about_decisions.json) keyed by place name: a name
the user has already approved, rejected, or remapped to a different place
resolves automatically, every time, with no re-prompt -- the "set it and
forget it" behavior. A name with no decision yet is appended to a pending
review queue (config/place_song_about_review_queue.json) instead, until
PlaceSongAboutReviewDialog resolves it.
"""

import json
from pathlib import Path

from src.foundation.asset_paths import config
from src.foundation.logger_config import logger

_QUEUE_PATH = Path(config("place_song_about_review_queue.json"))
_DECISIONS_PATH = Path(config("place_song_about_decisions.json"))

DECISION_APPROVED = "approved"
DECISION_REJECTED = "rejected"
DECISION_REMAPPED = "remapped"


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except (OSError, ValueError) as e:
        logger.warning(f"Failed to read {path.name}: {e}")
        return default


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def load_decisions() -> dict:
    """place_name -> {"decision": ..., ["place_id": ..., "place_name": ...]}."""
    return _read_json(_DECISIONS_PATH, {})


def save_decision(
    place_name: str,
    decision: str,
    *,
    place_id: int | None = None,
    remap_place_name: str | None = None,
) -> None:
    """Record `decision` (approved/rejected/remapped) for `place_name`, so
    every later detection of it resolves without a review prompt."""
    decisions = load_decisions()
    entry = {"decision": decision}
    if decision == DECISION_REMAPPED:
        entry["place_id"] = place_id
        entry["place_name"] = remap_place_name
    decisions[place_name] = entry
    _write_json(_DECISIONS_PATH, decisions)


def load_queue() -> list:
    """[{"track_id": ..., "place_name": ..., "place_id": ...}, ...]."""
    return _read_json(_QUEUE_PATH, [])


def enqueue(entries: list) -> None:
    """Append `entries` not already present (deduped by track_id+place_name)."""
    if not entries:
        return
    queue = load_queue()
    seen = {(e["track_id"], e["place_name"]) for e in queue}
    changed = False
    for entry in entries:
        key = (entry["track_id"], entry["place_name"])
        if key in seen:
            continue
        queue.append(entry)
        seen.add(key)
        changed = True
    if changed:
        _write_json(_QUEUE_PATH, queue)


def remove_place_from_queue(place_name: str) -> list:
    """Drop every queued entry for `place_name` (a decision just resolved
    all of them at once). Returns the removed entries."""
    queue = load_queue()
    removed = [e for e in queue if e["place_name"] == place_name]
    if removed:
        remaining = [e for e in queue if e["place_name"] != place_name]
        _write_json(_QUEUE_PATH, remaining)
    return removed
