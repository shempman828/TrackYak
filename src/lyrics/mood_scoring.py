"""
mood_scoring.py

score_moods(lyrics) -> list[str]: scores a track's lyrics against every
mood's keyword list in assets/mood_keywords.json and returns the names of
moods that clear the tagging threshold.

Mirrors src/core/censor.py's cached, mtime-reloaded pattern approach (one
compiled `\\b(word1|word2|...)\\b` regex per keyword list, no restart
needed to pick up an edited word list) but scores rather than binary-
matches, since a single incidental keyword hit must not auto-tag a mood
(see docs/specs/lyrics_mood_tagging.md). Reuses the tokenizer already
built for the Lyrics tab's word cloud (src/statistics/stats/lyrics.py) so
"total words in this lyric" is counted the same way everywhere in the app.
"""

import json
import re
from pathlib import Path

from src.core.asset_paths import asset
from src.core.logger_config import logger
from src.statistics.stats.lyrics import _tokenize

_KEYWORDS_PATH = Path(asset("mood_keywords.json"))

# A mood is tagged only if it clears BOTH gates:
#   1. distinct_hits >= MIN_DISTINCT_KEYWORDS (thematic breadth), OR
#      raw_hits >= MIN_RAW_HITS (a single keyword repeated, e.g. a chorus)
#   2. density >= MIN_DENSITY (guards against a long lyric racking up a
#      few incidental hits purely from word count)
MIN_DISTINCT_KEYWORDS = 2
MIN_RAW_HITS = 3
MIN_DENSITY = 0.005

_cache = {"mtime": None, "keyword_patterns": None}


def _compile_keyword_pattern(keyword: str) -> re.Pattern:
    return re.compile(r"\b" + re.escape(keyword) + r"\b", re.IGNORECASE)


def _get_keyword_patterns():
    """Return {mood_name: [(keyword, compiled_pattern), ...]}, reloaded
    whenever assets/mood_keywords.json's mtime changes."""
    try:
        mtime = _KEYWORDS_PATH.stat().st_mtime
    except OSError:
        return _cache["keyword_patterns"]

    if mtime == _cache["mtime"]:
        return _cache["keyword_patterns"]

    try:
        raw = json.loads(_KEYWORDS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        logger.warning(f"Failed to load mood keyword list: {e}")
        return _cache["keyword_patterns"]

    patterns = {
        mood_name: [(kw, _compile_keyword_pattern(kw)) for kw in keywords]
        for mood_name, keywords in raw.items()
    }
    _cache["mtime"] = mtime
    _cache["keyword_patterns"] = patterns
    return patterns


def score_moods(lyrics) -> list[str]:
    """Return every mood name whose keyword list clears the tagging
    threshold against `lyrics`. Empty/whitespace-only lyrics score no
    moods."""
    if not lyrics or not lyrics.strip():
        return []

    patterns = _get_keyword_patterns()
    if not patterns:
        return []

    total_tokens = len(_tokenize(lyrics))
    if total_tokens == 0:
        return []

    matched = []
    for mood_name, keyword_patterns in patterns.items():
        distinct_hits = 0
        raw_hits = 0
        for _keyword, pattern in keyword_patterns:
            occurrences = len(pattern.findall(lyrics))
            if occurrences:
                distinct_hits += 1
                raw_hits += occurrences

        density = raw_hits / total_tokens
        if (distinct_hits >= MIN_DISTINCT_KEYWORDS or raw_hits >= MIN_RAW_HITS) and (
            density >= MIN_DENSITY
        ):
            matched.append(mood_name)

    return matched
