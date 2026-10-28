"""Best-effort ``{entity_id: context}`` builders for the dimmed text in entity completer popups."""

# Callers must cap their candidate list first: the artist/place/track/album
# builders each touch a lazy relationship per row.

from __future__ import annotations

_SEP = " · "  # middle dot, separating the two halves of a context string
_DASH = "\u2013"  # en dash, matching Artist.career_span's separator


def _clean(value) -> str:
    return value.strip() if isinstance(value, str) else ""


def _year_span(begin, end) -> str:
    """An en-dash year span (e.g. 1998-2004, 1998-present), or '' without a begin year."""
    if not begin:
        return ""
    return f"{begin}{_DASH}{end}" if end else f"{begin}{_DASH}present"


# ── Artist ────────────────────────────────────────────────────────────────


def artist_context_map(artists) -> dict:
    """{artist_id: disambiguation, career span, or Group/Person} for each row."""
    return {a.artist_id: _artist_context(a) for a in artists if getattr(a, "artist_id", None) is not None}


def _artist_context(artist) -> str:
    disambiguation = _clean(getattr(artist, "disambiguation", None))
    if disambiguation:
        return disambiguation
    span = getattr(artist, "career_span", None)
    if span:
        return span
    isgroup = getattr(artist, "isgroup", None)
    if isgroup == 1:
        return "Group"
    if isgroup == 0:
        return "Person"
    return ""


# ── Place ─────────────────────────────────────────────────────────────────


def place_context_map(places) -> dict:
    """{place_id: place type and its ancestor chain} for each row."""
    return {p.place_id: _place_context(p) for p in places if getattr(p, "place_id", None) is not None}


def _place_context(place) -> str:
    place_type = _clean(getattr(place, "place_type", None))
    own_name = _clean(getattr(place, "place_name", None)).lower()
    chain = [name for name in _ancestor_chain(place) if name.lower() != own_name]
    location = ", ".join(chain)
    if place_type and location:
        return f"{place_type}{_SEP}{location}"
    return place_type or location or ""


def _ancestor_chain(place) -> list:
    """Ancestor names, nearest first, up to and including the nearest 'Country' (else the top)."""
    seen: set = set()
    current = getattr(place, "parent", None)
    chain: list = []
    while current is not None and getattr(current, "place_id", None) not in seen:
        seen.add(current.place_id)
        name = _clean(getattr(current, "place_name", None))
        if name:
            chain.append(name)
        if _clean(getattr(current, "place_type", None)).lower() == "country":
            break
        current = getattr(current, "parent", None)
    return chain


# ── Track ─────────────────────────────────────────────────────────────────


def track_context_map(tracks) -> dict:
    """{track_id: primary artists and album} for each row."""
    return {t.track_id: _track_context(t) for t in tracks if getattr(t, "track_id", None) is not None}


def _track_context(track) -> str:
    names = [_clean(getattr(a, "artist_name", None)) for a in (getattr(track, "primary_artists", None) or [])]
    artist = ", ".join(n for n in names if n)
    album = _clean(getattr(track, "album_name", None))
    if artist and album:
        return f"{artist}{_SEP}{album}"
    return artist or album or ""


# ── Album ─────────────────────────────────────────────────────────────────


def album_context_map(albums) -> dict:
    """{album_id: album artists and release year} for each row."""
    return {a.album_id: _album_context(a) for a in albums if getattr(a, "album_id", None) is not None}


def _album_context(album) -> str:
    names = [_clean(getattr(a, "artist_name", None)) for a in (getattr(album, "album_artists", None) or [])]
    artist = ", ".join(n for n in names if n)
    year = getattr(album, "release_year", None)
    year_str = str(year) if year else ""
    if artist and year_str:
        return f"{artist}{_SEP}{year_str}"
    return artist or year_str or ""


# ── Publisher ─────────────────────────────────────────────────────────────


def publisher_context_map(publishers) -> dict:
    """{publisher_id: parent publisher and active years} for each row."""
    return {p.publisher_id: _publisher_context(p) for p in publishers if getattr(p, "publisher_id", None) is not None}


def _publisher_context(publisher) -> str:
    parent = getattr(publisher, "parent", None)
    parts = [_clean(getattr(parent, "publisher_name", None)) if parent is not None else "", _year_span(getattr(publisher, "begin_year", None), getattr(publisher, "end_year", None))]
    return _SEP.join(part for part in parts if part)
