"""Injects the developer-only "Primary Artist Count" album sort option.

``patch()`` mutates :class:`~src.album.album_sorting.AlbumSortingMixin` in place:

* appends a "Developer" sort group to the class-level ``_SORT_GROUPS`` list
  (only when the flag is on at install time — hence "restart to apply"), and
* wraps ``_sort_key`` so the ``primary_artist_count`` criteria resolves to the
  deduped primary-artist count (or ``0`` when the flag is off, keeping a stale
  persisted criteria harmless).

Nothing in ``src/album`` references this module.
"""

from __future__ import annotations

import contextlib
import functools

from src.album.album_sorting import AlbumSortingMixin
from src.dev import dev_mode

CRITERIA = "primary_artist_count"

# Same shape as AlbumSortingMixin._SORT_GROUPS entries:
#   (group_label, [(item_label, criteria_key, descending), ...])
DEV_GROUP: tuple[str, list[tuple[str, str, bool]]] = (
    "Developer",
    [
        ("Primary Artist Count (Most First)", CRITERIA, True),
        ("Primary Artist Count (Fewest First)", CRITERIA, False),
    ],
)

_orig_sort_key = None
_group_added = False


def owns(criteria: str) -> bool:
    """True for the sort criteria this module is responsible for."""
    return criteria == CRITERIA


def primary_artist_count(album) -> int:
    """Number of distinct primary artists credited across all of the album's
    tracks. Distinctness is by ``artist_id`` (falling back to object identity
    for the odd artist row with no id). No tracks / no "Primary Artist" credits
    ⇒ ``0``."""
    seen: set = set()
    for track in getattr(album, "tracks", None) or []:
        for artist in getattr(track, "primary_artists", None) or []:
            aid = getattr(artist, "artist_id", None)
            seen.add(aid if aid is not None else id(artist))
    return len(seen)


def patch() -> None:
    global _orig_sort_key, _group_added

    if _orig_sort_key is None:
        _orig_sort_key = AlbumSortingMixin._sort_key
        original = _orig_sort_key

        @functools.wraps(original)
        def _sort_key(self, album):
            if owns(getattr(self, "_sort_criteria", None)):
                return primary_artist_count(album) if dev_mode.is_enabled() else 0
            return original(self, album)

        AlbumSortingMixin._sort_key = _sort_key

    if dev_mode.is_enabled() and not _group_added:
        if DEV_GROUP not in AlbumSortingMixin._SORT_GROUPS:
            AlbumSortingMixin._SORT_GROUPS.append(DEV_GROUP)
        _group_added = True


def unpatch() -> None:
    """Restore the mixin. Used by tests; harmless if never patched."""
    global _orig_sort_key, _group_added

    if _orig_sort_key is not None:
        AlbumSortingMixin._sort_key = _orig_sort_key
        _orig_sort_key = None
    if _group_added:
        with contextlib.suppress(ValueError):
            AlbumSortingMixin._SORT_GROUPS.remove(DEV_GROUP)
        _group_added = False
