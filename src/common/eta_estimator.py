"""Shared elapsed/rate ETA estimate for progress-bar dialogs (DuplicateFinderDialog, ArtworkConsistencyDialog)."""

import time


def estimate_remaining(start_time: float | None, current: int, total: int) -> str | None:
    """Human-readable ETA string for a `current`/`total` progress count, or None if there isn't enough data yet."""
    if not start_time or current <= 0 or current >= total:
        return None
    elapsed = time.monotonic() - start_time
    if elapsed < 1.0:
        return None
    rate = current / elapsed
    if rate <= 0:
        return None
    remaining_seconds = int((total - current) / rate)
    if remaining_seconds < 60:
        return f"{remaining_seconds}s"
    minutes, seconds = divmod(remaining_seconds, 60)
    return f"{minutes}m {seconds:02d}s"
