"""Filing-name ordering for artists.

``Artist.sort_name`` (the MusicBrainz-style filing name -- "Beatles, The",
"Davis, Miles") is the key for every alphabetical artist ordering in the
UI: the artist browser, artist pickers, and any album/track column that
sorts by artist. Display text is never affected -- only the sort key.

Where ``sort_name`` is null or blank the key falls back to the display
name, so a partly-backfilled library still orders sensibly.
"""


def artist_filing_name(artist) -> str:
    """The string an artist should be *filed* under: ``sort_name`` if set,
    otherwise the display name, otherwise an empty string."""
    sort_name = getattr(artist, "sort_name", None)
    if sort_name and sort_name.strip():
        return sort_name
    return getattr(artist, "artist_name", None) or ""


def artist_sort_key(artist) -> str:
    """Case-folded :func:`artist_filing_name`, for use as a ``sorted(key=...)``
    callable anywhere artists are ordered alphabetically."""
    return artist_filing_name(artist).lower()
