"""Persisted "not a duplicate" pairs for the fuzzy-duplicate-scan dialogs
(Artist, Place, Publisher, Album). Mirrors the dismissed-word pattern in
mood_word_review_widget.py -- a small JSON file plus Qt/DB-free load/write
helpers -- but keyed one level deeper, since a pair's identity is
(entity_type, id_a, id_b) rather than a single global string (Artist ID 5
and Place ID 5 are unrelated).
"""

import json
from pathlib import Path

from src.foundation.asset_paths import asset
from src.foundation.logger_config import logger

DEFAULT_DISMISSED_DUPLICATES_PATH = Path(asset("dismissed_duplicates.json"))


def normalize_pair(id_1: int, id_2: int) -> tuple[int, int]:
    """Order-independent pair key -- (12, 45) and (45, 12) are the same pair."""
    return (id_1, id_2) if id_1 <= id_2 else (id_2, id_1)


def load_dismissed_pairs(path: Path, entity_type: str) -> set[tuple[int, int]]:
    """Pairs marked "not a duplicate" for entity_type -- excluded from future scans.

    Missing/corrupt file, or a missing entry for entity_type, reads as no
    dismissed pairs.
    """
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        logger.warning(f"Failed to read dismissed duplicates list: {e}")
        return set()

    entries = raw.get(entity_type, []) if isinstance(raw, dict) else []
    pairs = set()
    for entry in entries:
        try:
            a_str, b_str = entry.split("-", 1)
            pairs.add((int(a_str), int(b_str)))
        except (ValueError, AttributeError):
            logger.warning(f"Ignoring malformed dismissed-pair entry: {entry!r}")
    return pairs


def _write_dismissed_pairs(path: Path, entity_type: str, pairs: set[tuple[int, int]]) -> None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    raw[entity_type] = sorted(f"{a}-{b}" for a, b in pairs)
    path.write_text(json.dumps(raw, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def dismiss_pair(path: Path, entity_type: str, id_1: int, id_2: int) -> bool:
    """Mark a pair as not-a-duplicate, permanently. No-op if already dismissed."""
    pair = normalize_pair(id_1, id_2)
    pairs = load_dismissed_pairs(path, entity_type)
    if pair in pairs:
        return False
    pairs.add(pair)
    _write_dismissed_pairs(path, entity_type, pairs)
    return True
