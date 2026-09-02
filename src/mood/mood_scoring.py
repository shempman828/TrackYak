"""
mood_scoring.py

score_moods(lyrics) -> list[str]: scores a track's lyrics against every
mood's keyword list in assets/mood_keywords.json and returns the names of
moods that clear the tagging threshold.

score_moods_detailed(lyrics) -> dict[str, MoodMatch]: same scoring, same
threshold and opposite-pair resolution, but keeps the per-mood match
detail (density, distinct/raw hit counts) instead of discarding it.
score_moods() is a thin wrapper over it. The density is what the mood-
tagging write path persists on each MoodTrackAssociation row, powering the
"most representative tracks per mood" statistic
(docs/specs/mood_representative_tracks.md).

Mirrors src/foundation/censor.py's cached, mtime-reloaded pattern approach (one
compiled `\\b(word1|word2|...)\\b` regex per keyword list, no restart
needed to pick up an edited word list) but scores rather than binary-
matches, since a single incidental keyword hit must not auto-tag a mood
(see docs/specs/lyrics_mood_tagging.md). Reuses the tokenizer already
built for the Lyrics tab's word cloud (src/statistics/stats/lyrics.py) so
"total words in this lyric" is counted the same way everywhere in the app.
"""

from dataclasses import dataclass
import json
from pathlib import Path
import re

from src.foundation.asset_paths import asset
from src.foundation.logger_config import logger
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

_OPPOSITES_PATH = Path(asset("mood_opposites.json"))

_cache = {"mtime": None, "keyword_patterns": None}
_opposites_cache = {"mtime": None, "pairs": None}


@dataclass(frozen=True)
class MoodMatch:
    """Per-mood match detail for one track's lyrics. `density` (raw_hits /
    total_lyric_tokens) is the cross-mood-comparable signal -- the opposite-
    pair tiebreak ranks on it, and it's what gets persisted on the
    MoodTrackAssociation row for the representativeness stat."""

    density: float
    distinct_hits: int
    raw_hits: int


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


def _get_opposite_pairs() -> list[tuple[str, str]]:
    """Return [(mood_a, mood_b), ...] pairs that should never both tag the
    same track (assets/mood_opposites.json), reloaded whenever its mtime
    changes -- same convention as the keyword list. Missing/corrupt file
    reads as no pairs, i.e. purely additive multi-label tagging."""
    try:
        mtime = _OPPOSITES_PATH.stat().st_mtime
    except OSError:
        return _opposites_cache["pairs"] or []

    if mtime == _opposites_cache["mtime"]:
        return _opposites_cache["pairs"] or []

    try:
        raw = json.loads(_OPPOSITES_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        logger.warning(f"Failed to load mood opposites list: {e}")
        return _opposites_cache["pairs"] or []

    pairs = [tuple(pair) for pair in raw if isinstance(pair, list) and len(pair) == 2]
    _opposites_cache["mtime"] = mtime
    _opposites_cache["pairs"] = pairs
    return pairs


def known_mood_names() -> set[str]:
    """Every mood name that appears as a top-level key in
    assets/mood_keywords.json -- the live source of truth for what the
    tagger can match, independent of whether a `Mood` DB row exists for
    it yet (see mood_autotag.build_autotag_context, which uses this to
    self-heal drift between the two)."""
    patterns = _get_keyword_patterns()
    return set(patterns.keys()) if patterns else set()


def score_moods_detailed(lyrics) -> dict[str, MoodMatch]:
    """Return {mood_name: MoodMatch} for every mood whose keyword list
    clears the tagging threshold against `lyrics`, after opposite-pair
    resolution. Empty/whitespace-only lyrics score no moods. Insertion
    order matches assets/mood_keywords.json's key order, so
    `list(score_moods_detailed(x))` is exactly `score_moods(x)`."""
    if not lyrics or not lyrics.strip():
        return {}

    patterns = _get_keyword_patterns()
    if not patterns:
        return {}

    total_tokens = len(_tokenize(lyrics))
    if total_tokens == 0:
        return {}

    matched: dict[str, MoodMatch] = {}
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
            matched[mood_name] = MoodMatch(density, distinct_hits, raw_hits)

    # A few incidental/ironic keyword hits for a mood's tonal opposite
    # (e.g. a handful of "happy" words in an overwhelmingly sad lyric)
    # can independently clear the threshold above -- both moods pass on
    # their own merits, since the per-mood gate has no cross-mood
    # awareness. For any declared opposite pair where both matched, keep
    # only the one with higher density (the normalized, cross-mood-
    # comparable signal); an exact tie means no real signal either way,
    # so both are left tagged rather than guessing.
    for mood_a, mood_b in _get_opposite_pairs():
        if mood_a in matched and mood_b in matched:
            if matched[mood_a].density > matched[mood_b].density:
                del matched[mood_b]
            elif matched[mood_b].density > matched[mood_a].density:
                del matched[mood_a]

    return matched


def score_moods(lyrics) -> list[str]:
    """Return every mood name whose keyword list clears the tagging
    threshold against `lyrics`. Empty/whitespace-only lyrics score no
    moods. Thin wrapper over score_moods_detailed()."""
    return list(score_moods_detailed(lyrics))
