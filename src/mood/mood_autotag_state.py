"""JSON persistence for what the library-wide lyrics auto-tag scan already covered.

MoodAutoTagWorker used to re-score every track with lyrics on every run,
re-attempting the same mood/place rows each time. This state file
(config/mood_autotag_scan_state.json) records, per track, a hash of the
lyrics it was last scanned with, plus the vocabulary that scan used (a hash
of each mood's keyword list, the opposite-mood pairs, and the library's
place names). A later scan then only does:

- a full scan of tracks whose lyrics are new or changed, and
- a partial scan of every other track, limited to the moods whose keyword
  list (or opposite pairing) changed and the place names that are new.

The per-track lyric-save/search auto-tag path doesn't touch this file.
"""

import hashlib
import json
from pathlib import Path

from src.foundation.asset_paths import config
from src.foundation.logger_config import logger

_STATE_PATH = Path(config("mood_autotag_scan_state.json"))


def lyrics_fingerprint(lyrics: str) -> str:
    return hashlib.sha1(lyrics.encode("utf-8")).hexdigest()


def load_state() -> dict:
    """{"moods": {name: hash}, "opposites": [[a, b], ...], "places": [...], "tracks": {track_id: hash}}."""
    empty = {"moods": {}, "opposites": [], "places": [], "tracks": {}}
    try:
        raw = json.loads(_STATE_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return empty
    except (OSError, ValueError) as e:
        logger.warning(f"Failed to read {_STATE_PATH.name}: {e}")
        return empty
    if not isinstance(raw, dict):
        return empty
    return {key: raw.get(key, default) for key, default in empty.items()}


def save_state(state: dict) -> None:
    try:
        _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _STATE_PATH.write_text(json.dumps(state, ensure_ascii=False) + "\n", encoding="utf-8")
    except OSError as e:
        logger.warning(f"Failed to write {_STATE_PATH.name}: {e}")


def changed_moods(state: dict, mood_fingerprints: dict, pairs) -> set:
    """Moods whose keyword list is new/changed since the last full scan, plus
    every mood in an opposite pair that was added or removed since then
    (the opposite-pair tiebreak can change which of the two gets tagged)."""
    previous = state.get("moods", {})
    changed = {name for name, fp in mood_fingerprints.items() if previous.get(name) != fp}
    old_pairs = {frozenset(p) for p in state.get("opposites", [])}
    new_pairs = {frozenset(p) for p in pairs}
    for pair in old_pairs ^ new_pairs:
        changed.update(name for name in pair if name in mood_fingerprints)
    return changed
