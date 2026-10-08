"""Explicit-word detection and display censorship from assets/explicit_words.txt."""

# The word list has one entry per line ('#' comments allowed) and reloads when the file changes.

from pathlib import Path
import re
import time

from PySide6.QtWidgets import QApplication

from src.foundation.asset_paths import asset
from src.foundation.logger_config import logger

_WORDLIST_PATH = Path(asset("explicit_words.txt"))

# Minimum seconds between mtime checks; text_contains_explicit_words runs per track in backfills.
_RECHECK_INTERVAL_S = 1.0

_cache = {"mtime": None, "pattern": None, "checked_at": None, "path": None}


def _get_pattern():
    """Return the compiled explicit-word regex, reloading it if the word list changed."""
    now = time.monotonic()
    if _cache["mtime"] is not None and _cache["path"] == _WORDLIST_PATH and _cache["checked_at"] is not None and now - _cache["checked_at"] < _RECHECK_INTERVAL_S:
        return _cache["pattern"]
    _cache["checked_at"] = now
    _cache["path"] = _WORDLIST_PATH

    try:
        mtime = _WORDLIST_PATH.stat().st_mtime
    except OSError:
        return _cache["pattern"]

    if mtime == _cache["mtime"]:
        return _cache["pattern"]

    words = []
    try:
        for line in _WORDLIST_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                words.append(line)
    except OSError as e:
        logger.warning(f"Failed to load explicit words list: {e}")
        return _cache["pattern"]

    pattern = re.compile(r"\b(" + "|".join(re.escape(w) for w in words) + r")\b", re.IGNORECASE) if words else None
    _cache["mtime"] = mtime
    _cache["pattern"] = pattern
    return pattern


def _mask(match: re.Match) -> str:
    """Replace all but the first character of a match with asterisks."""
    word = match.group(0)
    return word[0] + "*" * (len(word) - 1)


def censoring_enabled() -> bool:
    """Return whether the "Censor explicit words" display option is on."""
    app = QApplication.instance()
    display = getattr(app, "display_settings", None)
    return bool(getattr(display, "censor_explicit_words", False))


def text_contains_explicit_words(text) -> bool:
    """Return True if `text` contains any entry from the explicit word list."""
    # Ignores the display setting: this calculates Track.is_explicit, it does not mask text.
    if not text:
        return False
    pattern = _get_pattern()
    if pattern is None:
        return False
    return pattern.search(text) is not None


def censor_text(text, force: bool = False):
    """Mask explicit words in `text` (e.g. "shit" -> "s***") when censoring is on or `force` is set."""
    if not text:
        return text
    if not force and not censoring_enabled():
        return text

    pattern = _get_pattern()
    if pattern is None:
        return text

    return pattern.sub(_mask, text)
