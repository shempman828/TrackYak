from bisect import bisect_right
import re

# One leading LRC timestamp, e.g. [01:02.34]; a line may carry several in a row.
_TS_RE = re.compile(r"\[(\d+):(\d{2})(?:[.,](\d+))?\]")

# LRC ID tags ([ar:Artist], [offset:+500], ...) are metadata, not lyric lines.
# Only the standard tag names match, so plain-text section headers such as
# "[Chorus: Artist]" stay as lyrics.
_ID_TAG_RE = re.compile(r"^\[(?:ar|al|ti|au|by|length|offset|re|ve|tool|la|lr|#)\s*:.*\]$", re.IGNORECASE)


def parse_lyrics(raw: str) -> tuple[bool, list[tuple[int, str]]]:
    """Parse raw lyrics into ``(is_synced, [(timestamp_ms, text), ...])``; plain lyrics get 0 ms stamps."""
    entries: list[tuple[int | None, str]] = []
    timed_ms: list[int] = []
    for raw_line in raw.splitlines():
        line = raw_line.strip()
        if _ID_TAG_RE.match(line):
            continue
        stamps, pos = [], 0
        while m := _TS_RE.match(line, pos):
            mins, secs = int(m.group(1)), int(m.group(2))
            frac = m.group(3) or "0"
            # Normalise the fraction to milliseconds (handles 1- to 3+-digit fractions).
            stamps.append((mins * 60 + secs) * 1000 + int(frac.ljust(3, "0")[:3]))
            pos = m.end()
        text = line[pos:].strip()
        if stamps:
            entries.extend((ms, text) for ms in stamps)
            timed_ms.extend(stamps)
        else:
            entries.append((None, text))

    if not entries:
        return False, []

    if not timed_ms or _is_fake_timing(timed_ms):
        # Fake timing is placeholder stamps (line N at exactly N s); render as plain text.
        return False, [(0, text) for _, text in entries]

    # An untimed line inside synced lyrics (e.g. a blank stanza break) keeps
    # its place by taking the stamp of the line before it; sort is stable.
    lines: list[tuple[int, str]] = []
    prev_ms = 0
    for ms, text in entries:
        prev_ms = prev_ms if ms is None else ms
        lines.append((prev_ms, text))
    lines.sort(key=lambda x: x[0])
    return True, lines


def _is_fake_timing(stamps: list[int]) -> bool:
    """True when four or more stamps are all identical or exactly 0 s, 1 s, 2 s, ..."""
    if len(stamps) < 4:
        return False
    if len(set(stamps)) == 1:
        return True
    return all(ms == i * 1000 for i, ms in enumerate(sorted(stamps)))


def format_timestamp_ms(ms: int) -> str:
    """Render milliseconds as an LRC timestamp: ``mm:ss.xx`` (centiseconds)."""
    ms = max(0, int(ms))
    minutes, rem_ms = divmod(ms, 60_000)
    seconds, rem_ms = divmod(rem_ms, 1000)
    centiseconds = rem_ms // 10
    return f"{minutes:02d}:{seconds:02d}.{centiseconds:02d}"


def build_lrc(lines: list[tuple[int, str]]) -> str:
    """Inverse of ``parse_lyrics``: render ``(timestamp_ms, text)`` pairs as an LRC block."""
    return "\n".join(f"[{format_timestamp_ms(ts)}] {text}" for ts, text in lines)


def active_index(lines: list[tuple[int, str]], position_ms: int) -> int:
    """Index of the line to show at ``position_ms`` (0 before the first line); ``lines`` must be sorted."""
    return max(0, bisect_right(lines, position_ms, key=lambda x: x[0]) - 1)
