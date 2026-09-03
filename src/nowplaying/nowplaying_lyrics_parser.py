import re

_TS_RE = re.compile(r"^\[(\d{1,2}):(\d{2})(?:[.,](\d+))?\](.*)")


def parse_lyrics(raw: str) -> tuple[bool, list[tuple[int, str]]]:
    """
    Parse raw lyrics string.

    Returns (is_synced, lines) where lines is a list of (timestamp_ms, text).
    For plain lyrics, all timestamps are 0.
    """
    lines = []
    timed_ms: list[int] = []
    is_synced = False
    for line in raw.splitlines():
        m = _TS_RE.match(line.strip())
        if m:
            is_synced = True
            mins, secs = int(m.group(1)), int(m.group(2))
            frac = m.group(3) or "0"
            # Normalise fraction to milliseconds (handles 2- or 3-digit fracs)
            ms = (mins * 60 + secs) * 1000 + int(frac.ljust(3, "0")[:3])
            text = m.group(4).strip()
            lines.append((ms, text))
            timed_ms.append(ms)
        else:
            lines.append((0, line.strip()))

    if not lines:
        return False, []

    if is_synced and _is_fake_timing(timed_ms):
        # Placeholder timing: line N stamped at exactly N seconds (or every
        # line on the same stamp). Not real sync -- render as plain text.
        return False, [(0, text) for _, text in lines]

    # If mixed (some timed, some not), treat as plain
    if is_synced:
        lines.sort(key=lambda x: x[0])

    return is_synced, lines


def _is_fake_timing(stamps: list[int]) -> bool:
    """True when *stamps* are a trivial sequence rather than real timing:
    four or more timestamps that are exactly 0s, 1s, 2s, ... (the shape of
    fabricated per-line placeholder timestamps), or every stamp identical."""
    if len(stamps) < 4:
        return False
    if len(set(stamps)) == 1:
        return True
    return all(ms == i * 1000 for i, ms in enumerate(sorted(stamps)))


def active_index(lines: list[tuple[int, str]], position_ms: int) -> int:
    """Return the index of the line that should be shown at position_ms."""
    idx = 0
    for i, (ts, _) in enumerate(lines):
        if ts <= position_ms:
            idx = i
        else:
            break
    return idx
