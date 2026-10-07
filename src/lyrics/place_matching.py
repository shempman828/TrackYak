"""Find which of the library's existing Place names are name-dropped in lyrics."""

from functools import lru_cache
import re


@lru_cache(maxsize=4096)
def _compile_place_pattern(place_name: str) -> re.Pattern:
    """Compile a whole-word, case-insensitive pattern for one place name."""
    # Lookarounds, not \b: \b fails at a name edge that is punctuation ("Washington, D.C.").
    return re.compile(r"(?<!\w)" + re.escape(place_name) + r"(?!\w)", re.IGNORECASE)


def detect_known_places(lyrics, place_names) -> list[str]:
    """Return the distinct non-blank `place_names` that appear as whole words in `lyrics`."""
    # Matches only names the caller supplies -- never invents or creates Places.
    if not lyrics or not lyrics.strip():
        return []

    matched: list[str] = []
    for name in dict.fromkeys(place_names):
        if name and name.strip() and _compile_place_pattern(name).search(lyrics):
            matched.append(name)
    return matched
