"""Explicit-word text censorship for display purposes.

Words are read from assets/explicit_words.txt (one per line, '#' comments
allowed) and re-read automatically whenever the file changes, so users can
edit the list without restarting the app.
"""

import re
from pathlib import Path

from PySide6.QtWidgets import QApplication

from src.core.asset_paths import asset
from src.core.logger_config import logger

_WORDLIST_PATH = Path(asset("explicit_words.txt"))

_cache = {"mtime": None, "pattern": None}


def _get_pattern():
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

    pattern = (
        re.compile(
            r"\b(" + "|".join(re.escape(w) for w in words) + r")\b", re.IGNORECASE
        )
        if words
        else None
    )
    _cache["mtime"] = mtime
    _cache["pattern"] = pattern
    return pattern


def _mask(match: re.Match) -> str:
    word = match.group(0)
    return word[0] + "*" * (len(word) - 1)


def censoring_enabled() -> bool:
    """Return whether the "Censor explicit words" display option is on."""
    app = QApplication.instance()
    display = getattr(app, "display_settings", None)
    return bool(getattr(display, "censor_explicit_words", False))


def censor_text(text, force: bool = False):
    """Mask explicit words in `text` with asterisks (e.g. "shit" -> "s***").

    Returns `text` unchanged if it's empty, the display option is off, or the
    word list can't be loaded. Pass `force=True` to censor regardless of the
    current display setting.
    """
    if not text:
        return text
    if not force and not censoring_enabled():
        return text

    pattern = _get_pattern()
    if pattern is None:
        return text

    return pattern.sub(_mask, text)
