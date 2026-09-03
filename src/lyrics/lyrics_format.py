"""Serialise a lyrics search result into the plain string stored in Track.lyrics.

The lyrics provider (lyriq) hands back a ``Lyrics`` object whose ``lyrics``
dict is *always* populated: when the LRCLib entry has real synced lyrics the
keys are genuine ``mm:ss.xx`` timestamps, but when it only has plain lyrics
lyriq fabricates one key per line from the line index (``"00.00"``,
``"01.00"``, ...). Rendering that fabricated dict as ``[00.00] line`` used to
bake fake sequential timestamps into every plain-lyrics track.

So: trust the object's own ``synced_lyrics`` / ``plain_lyrics`` fields, which
are honest about whether real timing exists. The dict is only a fallback for
callers that hand us a bare dict, and even there we sniff out the fabricated
index pattern.
"""

from __future__ import annotations

import re

# lyriq's fabricated plain-lyrics key: two-or-more digits, a dot, two digits.
_FABRICATED_KEY = re.compile(r"^\d{1,3}\.\d{2}$")


def _looks_fabricated(keys: list[str]) -> bool:
    """True when *keys* are lyriq's line-index placeholders rather than real
    timestamps: every key is ``N.NN`` shaped and the integer parts form a
    non-decreasing run starting at 0."""
    if len(keys) < 3 or not all(_FABRICATED_KEY.match(k) for k in keys):
        return False
    nums = [int(k.split(".")[0]) for k in keys]
    return nums[0] == 0 and nums == sorted(nums)


def _render_dict(lyrics_dict: dict[str, str], none_char: str = "♪") -> str:
    keys = sorted(lyrics_dict.keys())
    if _looks_fabricated(keys):
        # Fake timing -- drop the placeholder keys, keep the text.
        return "\n".join(
            "" if str(lyrics_dict[k]).strip() == none_char else str(lyrics_dict[k]) for k in keys
        )
    lines = []
    for ts in keys:
        line = lyrics_dict[ts]
        lines.append("" if str(line).strip() == none_char else f"[{ts}] {line}")
    return "\n".join(lines)


def format_lyrics_for_storage(lyrics_obj) -> str:
    """Convert a lyriq ``Lyrics`` object (or str / bare dict) into the plain
    string persisted to ``Track.lyrics``.

    Real synced lyrics are kept verbatim as an LRC block; plain-only results
    are stored as plain text with no fabricated ``[mm:ss]`` prefixes.
    """
    if isinstance(lyrics_obj, str):
        return lyrics_obj

    # lyriq Lyrics object: its own fields are authoritative about real timing.
    synced = getattr(lyrics_obj, "synced_lyrics", None)
    plain = getattr(lyrics_obj, "plain_lyrics", None)
    if synced is not None or plain is not None:
        if synced and synced.strip():
            return synced.strip()
        return (plain or "").strip()

    if isinstance(lyrics_obj, dict):
        return _render_dict(lyrics_obj)

    # Fallback: stringify whatever we got.
    return str(lyrics_obj)
