"""
place_matching.py

detect_known_places(lyrics, place_names) -> list[str]: finds which of the
library's *existing* Place names are name-dropped in a track's lyrics.

Deliberately does not ship a bundled city/country gazetteer and never
creates new Place rows -- it only ever matches against place names already
present in the library's own `places` table (populated by MusicBrainz
imports, manual entry, etc.), per docs/specs/lyrics_mood_tagging.md. A
match is written by the caller as a PlaceAssociation using the existing
"Song About" association type -- no new schema.
"""

import re
from functools import lru_cache


@lru_cache(maxsize=4096)
def _compile_place_pattern(place_name: str) -> re.Pattern:
    return re.compile(r"\b" + re.escape(place_name) + r"\b", re.IGNORECASE)


def detect_known_places(lyrics, place_names) -> list[str]:
    """Return the subset of `place_names` that appear as a whole-word,
    case-insensitive match in `lyrics`."""
    if not lyrics or not lyrics.strip():
        return []

    return [name for name in place_names if _compile_place_pattern(name).search(lyrics)]
