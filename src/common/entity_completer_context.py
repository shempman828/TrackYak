"""Shared "secondary context" builders for entity completer popups.

Each builder takes an already-loaded list of ORM rows and returns
``{entity_id: context_string}``. The context string is rendered dimmed
beside the entity name in the completer popup (see ``ContextItemDelegate``
in :mod:`src.common.entity_completer_edit`); it never becomes part of the
completion value, so a caller's name-based resolution
(``find_or_create_by_name``) is unaffected.

Builders are best-effort: a missing relationship or null field yields an
empty context, never an exception. Callers must cap their candidate list
before calling these -- the artist/place/track/album builders each touch a
lazy relationship per row.
"""

from __future__ import annotations

_SEP = " · "  # middle dot, separating the two halves of a context string
_DASH = "\u2013"  # en dash, matching Artist.career_span's separator


def _clean(value) -> str:
    return value.strip() if isinstance(value, str) else ""


def _year_span(begin, end) -> str:
    """An en-dash-separated year span (e.g. 1998-2004, 1998-present), or ''
    when there is no begin year. Mirrors Artist.career_span."""
    if not begin:
        return ""
    return f"{begin}{_DASH}{end}" if end else f"{begin}{_DASH}present"


# ── Artist ────────────────────────────────────────────────────────────────


def artist_context_map(artists) -> dict:
    return {
        a.artist_id: _artist_context(a)
        for a in artists
        if getattr(a, "artist_id", None) is not None
    }


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
    return {
        p.place_id: _place_context(p) for p in places if getattr(p, "place_id", None) is not None
    }


def _place_context(place) -> str:
    place_type = _clean(getattr(place, "place_type", None))
    country = _country_of(place)
    if country and country.lower() == _clean(getattr(place, "place_name", None)).lower():
        country = ""
    if place_type and country:
        return f"{place_type}{_SEP}{country}"
    return place_type or country or ""


def _country_of(place) -> str:
    """Name of the nearest ancestor whose ``place_type`` is 'Country', else
    the topmost ancestor's name, walking ``parent``. '' when the place has
    no parent chain. The visited set guards the self-referential FK against
    a cycle."""
    seen: set = set()
    current = getattr(place, "parent", None)
    topmost = ""
    while current is not None and getattr(current, "place_id", None) not in seen:
        seen.add(current.place_id)
        if _clean(getattr(current, "place_type", None)).lower() == "country":
            return _clean(getattr(current, "place_name", None))
        topmost = _clean(getattr(current, "place_name", None)) or topmost
        current = getattr(current, "parent", None)
    return topmost


# ── Track ─────────────────────────────────────────────────────────────────


def track_context_map(tracks) -> dict:
    return {
        t.track_id: _track_context(t) for t in tracks if getattr(t, "track_id", None) is not None
    }


def _track_context(track) -> str:
    names = [
        _clean(getattr(a, "artist_name", None))
        for a in (getattr(track, "primary_artists", None) or [])
    ]
    artist = ", ".join(n for n in names if n)
    album = _clean(getattr(track, "album_name", None))
    if artist and album:
        return f"{artist}{_SEP}{album}"
    return artist or album or ""


# ── Album ─────────────────────────────────────────────────────────────────


def album_context_map(albums) -> dict:
    return {
        a.album_id: _album_context(a) for a in albums if getattr(a, "album_id", None) is not None
    }


def _album_context(album) -> str:
    names = [
        _clean(getattr(a, "artist_name", None))
        for a in (getattr(album, "album_artists", None) or [])
    ]
    artist = ", ".join(n for n in names if n)
    year = getattr(album, "release_year", None)
    year_str = str(year) if year else ""
    if artist and year_str:
        return f"{artist}{_SEP}{year_str}"
    return artist or year_str or ""


# ── Publisher ─────────────────────────────────────────────────────────────


def publisher_context_map(publishers) -> dict:
    return {
        p.publisher_id: _publisher_context(p)
        for p in publishers
        if getattr(p, "publisher_id", None) is not None
    }


def _publisher_context(publisher) -> str:
    parent = getattr(publisher, "parent", None)
    parts = [
        _clean(getattr(parent, "publisher_name", None)) if parent is not None else "",
        _year_span(getattr(publisher, "begin_year", None), getattr(publisher, "end_year", None)),
    ]
    return _SEP.join(part for part in parts if part)
