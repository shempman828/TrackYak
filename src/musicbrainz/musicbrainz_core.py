"""Shared MusicBrainz infrastructure: client setup, query building, error wrapping, ranking and Area resolution."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
import re
import socket
from typing import TYPE_CHECKING, Any

import musicbrainzngs

from src.foundation.logger_config import logger

if TYPE_CHECKING:
    # Type-only import: a real import would be circular.
    from src.musicbrainz.musicbrainz_artist import MBArtistRelations

# musicbrainzngs has no request timeout, so a hung connection blocks forever; a global
# socket default bounds it. 60s, not 30s: musicbrainzngs retries a timeout 8x while holding
# its rate-limit lock, and big release lookups often take more than 30s.
_REQUEST_TIMEOUT_SECONDS = 60

_APP_NAME = "TrackYak"
_APP_VERSION = "0.5"
_CONTACT = "https://github.com/babyyakstudios/trackyak"

_configured = False

# Same special-character set musicbrainzngs escapes (musicbrainzngs.musicbrainz.LUCENE_SPECIAL).
_LUCENE_SPECIAL = re.compile(r'([+\-&|!(){}\[\]^"~*?:\\/])')

# Preferred release country order, best first -- "XW" is MusicBrainz's "Worldwide".
_COUNTRY_PREFERENCE = ("XW", "GB", "US")

# Curly vs. straight quotes vary between MB entries of the same title ("Ain't" / "Ain’t").  # noqa: RUF003
_QUOTE_NORMALIZE = str.maketrans(
    {"‘": "'", "’": "'", "‚": "'", "‛": "'", "“": '"', "”": '"', "„": '"', "‟": '"'}  # noqa: RUF001
)


def _escape_lucene(value: str) -> str:
    """Backslash-escape Lucene special characters in a query value."""
    return _LUCENE_SPECIAL.sub(r"\\\1", value)


def _query_term(term: str, fields: dict[str, Any]) -> str:
    """Escape a positional query term only when musicbrainzngs will not escape it itself."""
    # musicbrainzngs escapes the term itself whenever field kwargs are given -- escaping
    # here too would double-escape titles with &, :, !, () etc.
    return term if fields else _escape_lucene(term)


def _and_query(term_field: str | None, term_value: str, fields: dict[str, Any]) -> str:
    """Build a Lucene query that ANDs the free-text term with every non-blank field filter."""
    # musicbrainzngs joins clauses with a bare space, which Lucene reads as OR, so the
    # artist filter became optional (#364). Lowercasing keeps words like "and" from being
    # parsed as operators. term_field=None keeps MB's default fields (title + alias).
    term_clause = _escape_lucene(term_value).lower()
    clauses = [f"{term_field}:({term_clause})" if term_field else f"({term_clause})"]
    for key, value in fields.items():
        if value is None or not str(value).strip():
            continue  # an empty "key:()" clause is a Lucene parse error
        clauses.append(f"{key}:({_escape_lucene(str(value)).lower()})")
    return " AND ".join(clauses)


def _is_blank(value: str | None) -> bool:
    """True when a search term is None, empty or whitespace only."""
    return value is None or not value.strip()


class MusicBrainzLookupError(Exception):
    """Raised when a MusicBrainz search/lookup call fails."""


def configure() -> None:
    """Set the MusicBrainz User-Agent and the global socket timeout once."""
    global _configured
    if _configured:
        return
    musicbrainzngs.set_useragent(_APP_NAME, _APP_VERSION, _CONTACT)
    socket.setdefaulttimeout(_REQUEST_TIMEOUT_SECONDS)
    _configured = True


def _mb_call(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Run one musicbrainzngs call, wrapping every failure in MusicBrainzLookupError."""
    configure()
    try:
        return fn(*args, **kwargs)
    except Exception as e:
        # Deliberately broad: musicbrainzngs has no single exception hierarchy
        # (network, XML parse, HTTP, rate-limit errors all differ).
        raise MusicBrainzLookupError(str(e)) from e


@dataclass
class MBCandidate:
    """One picker row returned by a search_* function."""

    id: str
    label: str
    enrichment: dict[str, Any] = field(default_factory=dict)
    relations: MBArtistRelations | None = None
    # Other releases collapsed into this row (e.g. other-country pressings of the same
    # release-group), offered by the picker as a same-row variant dropdown.
    alternates: list[MBCandidate] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers shared across artist / release / recording modules
# ---------------------------------------------------------------------------


def _parse_partial_date(date_str: str | None, prefix: str) -> dict[str, int]:
    """Parse a MusicBrainz partial date into {"<prefix>_year": ..., ...}, omitting absent parts."""
    result: dict[str, int] = {}
    if not date_str:
        return result
    suffixes = ("year", "month", "day")
    for suffix, part in zip(suffixes, date_str.split("-"), strict=False):
        try:
            result[f"{prefix}_{suffix}"] = int(part)
        except (TypeError, ValueError):
            break
    return result


def _to_float(value: Any) -> float | None:
    """Convert to float, or None when missing or not numeric."""
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _to_int(value: Any, default: int | None = None) -> int | None:
    """Convert to int, or `default` when missing or not numeric."""
    try:
        return int(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def _ext_score(r: dict[str, Any]) -> float:
    """The Lucene relevance score (`ext:score`) of one search result, 0.0 if absent."""
    try:
        return float(r.get("ext:score", 0))
    except (TypeError, ValueError):
        return 0.0


def _normalize_title(title: str) -> str:
    """Case- and quote-insensitive key for exact title comparison."""
    return title.strip().translate(_QUOTE_NORMALIZE).lower()


def _matching_track_recording_id(release: dict[str, Any], title_key: str) -> str | None:
    """Recording MBID of the track on `release` whose normalized title equals `title_key`."""
    # Exact match, not substring: "In the Mood" must not match "In the Mood for Love".
    for medium in release.get("medium-list", []) or []:
        for track in medium.get("track-list", []) or []:
            recording = track.get("recording", {})
            title = recording.get("title") or track.get("title") or ""
            if _normalize_title(title) == title_key:
                return recording.get("id")
    return None


def _canonical_rank_parts(r: dict[str, Any]) -> tuple[int, tuple[int, int, int], int]:
    """(status, date, country) sort parts that put the canonical release first."""
    status_rank = 0 if (r.get("status") or "").lower() == "official" else 1
    parts = _parse_partial_date(r.get("date"), "d")
    date_key = (parts.get("d_year", 9999), parts.get("d_month", 99), parts.get("d_day", 99))
    country = r.get("country") or ""
    country_rank = _COUNTRY_PREFERENCE.index(country) if country in _COUNTRY_PREFERENCE else len(_COUNTRY_PREFERENCE)
    return status_rank, date_key, country_rank


def _resolve_place_area(place_mbid: str) -> tuple[str | None, str | None]:
    """Best-effort (area_mbid, area_name) of a Place's containing Area, or (None, None)."""
    # A "recorded at" relation's embedded place never includes its area (#248).
    try:
        result = _mb_call(musicbrainzngs.get_place_by_id, place_mbid)
    except MusicBrainzLookupError as e:
        logger.warning(f"Could not resolve area for place {place_mbid}: {e}")
        return None, None
    area = (result.get("place") or {}).get("area") or {}
    return area.get("id"), area.get("name")


def resolve_area_chain(area_mbid: str, cache: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Walk Area "part of" relations upward from `area_mbid`, immediate area first, memoized in `cache`."""
    if area_mbid in cache:
        return cache[area_mbid]

    result = _mb_call(musicbrainzngs.get_area_by_id, area_mbid, includes=["area-rels"])

    area = result.get("area", {})
    # Areas carry no coordinates on MB, so latitude/longitude stay None.
    chain = [{"mbid": area_mbid, "name": area.get("name") or "", "type": area.get("type"), "latitude": None, "longitude": None}]

    for rel in area.get("area-relation-list", []) or []:
        # The parent link is "part of" with direction="backward"; "forward" points at a
        # child area, and following it inverts the chain (#246).
        if rel.get("type") != "part of" or rel.get("direction") != "backward":
            continue
        parent = rel.get("area") or {}
        if not parent.get("id"):
            continue
        chain.extend(resolve_area_chain(parent["id"], cache))
        break

    cache[area_mbid] = chain
    return chain
