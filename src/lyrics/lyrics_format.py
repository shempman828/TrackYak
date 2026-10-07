"""Serialise a lyrics search result into the plain string stored in Track.lyrics."""

from __future__ import annotations

import re

# lyriq's plain-lyrics placeholder key: the line index, then ".00" ("00.00", "01.00", ..., "100.00").
_FABRICATED_KEY = re.compile(r"^(\d+)\.00$")


def _fabricated_order(keys) -> list[str] | None:
    """Return *keys* in line order if they are lyriq's line-index placeholders, else None."""
    matches = [_FABRICATED_KEY.match(k) for k in keys]
    if not matches or not all(matches):
        return None
    by_index = sorted(zip((int(m.group(1)) for m in matches), keys, strict=True))
    # A real fabricated set is exactly the run 0..n-1; anything else may be real timing.
    if [i for i, _ in by_index] != list(range(len(by_index))):
        return None
    return [k for _, k in by_index]


def _render_dict(lyrics_dict: dict[str, str], none_char: str = "♪") -> str:
    """Render a timestamp -> line dict as LRC, or as plain text if the keys are fabricated."""
    fabricated = _fabricated_order(lyrics_dict.keys())
    if fabricated is not None:
        return "\n".join("" if str(lyrics_dict[k]).strip() == none_char else str(lyrics_dict[k]) for k in fabricated)
    lines = []
    for ts in sorted(lyrics_dict.keys()):
        line = lyrics_dict[ts]
        lines.append("" if str(line).strip() == none_char else f"[{ts}] {line}")
    return "\n".join(lines)


def format_lyrics_for_storage(lyrics_obj) -> str:
    """Convert a lyriq Lyrics object, str or bare dict into the Track.lyrics string."""
    if lyrics_obj is None:
        return ""
    if isinstance(lyrics_obj, str):
        return lyrics_obj

    # lyriq always fills its .lyrics dict (with fake index keys for plain-only
    # results), so trust synced_lyrics / plain_lyrics, which are honest about timing.
    synced = getattr(lyrics_obj, "synced_lyrics", None)
    plain = getattr(lyrics_obj, "plain_lyrics", None)
    if synced is not None or plain is not None:
        if synced and synced.strip():
            return synced.strip()
        return (plain or "").strip()

    if isinstance(lyrics_obj, dict):
        return _render_dict(lyrics_obj)

    return str(lyrics_obj)
