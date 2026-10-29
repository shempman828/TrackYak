"""Persisted "not a duplicate" pairs for the fuzzy-duplicate-scan dialogs."""

import json
from pathlib import Path

from src.foundation.asset_paths import config
from src.foundation.logger_config import logger

DEFAULT_DISMISSED_DUPLICATES_PATH = Path(config("dismissed_duplicates.json"))


def normalize_pair(id_1: int, id_2: int) -> tuple[int, int]:
    """Order-independent pair key -- (12, 45) and (45, 12) are the same pair."""
    return (id_1, id_2) if id_1 <= id_2 else (id_2, id_1)


def load_dismissed_pairs(path: Path, entity_type: str) -> set[tuple[int, int]]:
    """Pairs marked "not a duplicate" for entity_type; a missing/corrupt file reads as none."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return set()  # normal before the first dismissal
    except (OSError, ValueError) as e:
        logger.warning(f"Failed to read dismissed duplicates list: {e}")
        return set()

    # Keyed per entity type: Artist ID 5 and Place ID 5 are unrelated.
    entries = raw.get(entity_type, []) if isinstance(raw, dict) else []
    pairs = set()
    for entry in entries:
        try:
            a_str, b_str = entry.split("-", 1)
            pairs.add((int(a_str), int(b_str)))
        except (ValueError, AttributeError):
            logger.warning(f"Ignoring malformed dismissed-pair entry: {entry!r}")
    return pairs


def _write_dismissed_pairs(path: Path, entity_type: str, pairs: set[tuple[int, int]]) -> bool:
    """Write entity_type's pairs back, keeping other types' entries; False if the write failed."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    raw[entity_type] = sorted(f"{a}-{b}" for a, b in pairs)
    try:
        path.write_text(json.dumps(raw, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    except OSError as e:
        logger.error(f"Failed to write dismissed duplicates list: {e}")
        return False
    return True


def dismiss_pair(path: Path, entity_type: str, id_1: int, id_2: int) -> bool:
    """Mark a pair as not-a-duplicate, permanently; False if already dismissed or not saved."""
    pair = normalize_pair(id_1, id_2)
    pairs = load_dismissed_pairs(path, entity_type)
    if pair in pairs:
        return False
    pairs.add(pair)
    return _write_dismissed_pairs(path, entity_type, pairs)
