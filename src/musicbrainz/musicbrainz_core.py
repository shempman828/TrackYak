"""
musicbrainz_core.py

Shared infrastructure for the musicbrainz_* modules: User-Agent setup,
Lucene query escaping, the MusicBrainzLookupError exception every module
wraps its musicbrainzngs calls in, the MBCandidate picker-row dataclass
returned by every search_* function, and Area/Place resolution (shared by
both artist birth/death chains and release recording-location/label-HQ
chains).

Deliberately does not touch the AcoustID web service anywhere -- that's a
separate, unrelated API with its own commercial tier. This module only
talks to the free MusicBrainz metadata search service, which has no
paid tier and just asks for courteous rate limiting (handled internally
by musicbrainzngs, enabled by default).
"""

from __future__ import annotations

import re
import socket
from dataclasses import dataclass, field
from typing import Any

import musicbrainzngs

from src.core.logger_config import logger

# musicbrainzngs has no native request-timeout option -- it opens every
# request via a plain urllib opener with no timeout passed (confirmed by
# reading musicbrainzngs.musicbrainz._safe_read's opener.open() calls), so a
# connection that hangs (server accepts the TCP connection but never
# responds, or the connection silently dies with no FIN/RST reaching the
# client) blocks forever with no exception raised. Caught live: an awards
# backfill run hung all night on a single stuck request, using ~0 CPU the
# entire time -- confirmed via `ss -tnp` showing an ESTABLISHED connection
# with an empty send/recv queue. socket.setdefaulttimeout() is the standard
# workaround for this exact gap; it applies globally to every socket opened
# without its own explicit timeout, which covers musicbrainzngs's requests.
_REQUEST_TIMEOUT_SECONDS = 30

_APP_NAME = "TrackYak"
_APP_VERSION = "0.5"
_CONTACT = "https://github.com/babyyakstudios/trackyak"

_configured = False

# Same special-character set musicbrainzngs escapes internally for
# field-restricted queries (see musicbrainzngs.musicbrainz.LUCENE_SPECIAL).
_LUCENE_SPECIAL = re.compile(r'([+\-&|!(){}\[\]^"~*?:\\/])')


def _escape_lucene(value: str) -> str:
    return _LUCENE_SPECIAL.sub(r"\\\1", value)


def _query_term(term: str, fields: dict[str, Any]) -> str:
    """musicbrainzngs escapes the positional query term itself whenever any
    field kwargs are also given (see musicbrainzngs.musicbrainz._do_mb_search),
    but leaves it completely unescaped when there are none. Pre-escaping here
    must mirror that exact condition, or a term combined with field kwargs
    (e.g. album_name + artist_name) gets double-escaped, corrupting the query
    for any title containing Lucene special characters (&, :, !, (), etc --
    common in deluxe/reissue titles)."""
    return term if fields else _escape_lucene(term)


def _and_query(term_field: str | None, term_value: str, fields: dict[str, Any]) -> str:
    """Build a Lucene query string that ANDs the free-text title term
    together with every field filter (e.g. arid/artist), instead of handing
    the term and fields separately to musicbrainzngs.search_*() and letting
    its own query-building (_do_mb_search) join them.

    Unless strict=True, _do_mb_search joins the term clause and every field
    clause with a bare space -- which Lucene's classic parser reads as OR,
    not AND. That makes an artist filter merely optional: a release can
    score as a top candidate by satisfying just the free-text words (or even
    just the artist), with the other clause never enforced. Confirmed live
    (#364): searching "Open Up And Say . . . Ahh!" (Poison) returned
    unrelated ellipsis-titled releases by other artists ranked above the
    real album, because the bare "." tokens plus common words ("open", "up",
    "and", "say") satisfied the free-text clause on their own and the arid
    filter was never required to match.

    strict=True was tried and rejected: MB stores this title with a single
    "…" ellipsis character, not spaced periods, so a quoted phrase match
    against the raw local title returns zero results. Wrapping the lowercased,
    escaped term in parens (`field:(word word word)`) keeps the same
    tokenized, typo-tolerant matching musicbrainzngs's non-strict mode
    already uses for field values -- the only change is joining every
    clause with an explicit " AND " so filters are no longer optional.

    `term_field=None` leaves the term clause without a field prefix, so MB
    applies its own default-field expansion instead of restricting to one
    field -- needed by search_recordings(), which deliberately searches the
    unrestricted default (title + alias, per MB's indexed-search docs) so a
    track known only by an alias still matches; scoping it to `recording:`
    would silently drop that.
    """
    term_clause = _escape_lucene(term_value).lower()
    clauses = [f"{term_field}:({term_clause})" if term_field else f"({term_clause})"]
    for key, value in fields.items():
        clauses.append(f"{key}:({_escape_lucene(value).lower()})")
    return " AND ".join(clauses)


class MusicBrainzLookupError(Exception):
    """Raised when a MusicBrainz search/lookup call fails."""


def configure() -> None:
    """Set the required MusicBrainz User-Agent and a global socket timeout
    (see _REQUEST_TIMEOUT_SECONDS). Safe to call more than once."""
    global _configured
    if _configured:
        return
    musicbrainzngs.set_useragent(_APP_NAME, _APP_VERSION, _CONTACT)
    socket.setdefaulttimeout(_REQUEST_TIMEOUT_SECONDS)
    _configured = True


@dataclass
class MBCandidate:
    id: str
    label: str
    enrichment: dict[str, Any] = field(default_factory=dict)
    relations: "MBArtistRelations | None" = None
    # Other releases collapsed into this same candidate (e.g. other-country
    # pressings of the same release-group) -- populated only by search
    # functions that group multiple releases under one picker row, so a
    # picker UI can offer them as a same-row variant dropdown instead of
    # silently discarding all but the top-ranked one. Empty for candidates
    # that already are the only release for what they represent.
    alternates: list[MBCandidate] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers shared across artist / release / recording modules
# ---------------------------------------------------------------------------


def _parse_partial_date(date_str: str | None, prefix: str) -> dict[str, int]:
    """Parse a MusicBrainz partial date ('YYYY', 'YYYY-MM', or 'YYYY-MM-DD')
    into e.g. {"release_year": 1997, "release_month": 5} for the given
    field-name prefix, omitting any part that isn't present."""
    result: dict[str, int] = {}
    if not date_str:
        return result
    suffixes = ("year", "month", "day")
    for suffix, part in zip(suffixes, date_str.split("-")):
        try:
            result[f"{prefix}_{suffix}"] = int(part)
        except (TypeError, ValueError):
            break
    return result


def _to_float(value) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _ext_score(r: dict[str, Any]) -> float:
    """MusicBrainz search results carry a Lucene relevance score under
    `ext:score` -- used to keep only genuine close matches out of a search
    endpoint's result list (as opposed to browse endpoints, which return
    everything unranked)."""
    try:
        return float(r.get("ext:score", 0))
    except (TypeError, ValueError):
        return 0.0


def _resolve_place_area(place_mbid: str) -> tuple[str | None, str | None]:
    """Look up a Place's own containing Area via a direct place lookup.
    Needed because a "recorded at" relation's embedded place never includes
    it -- without this, a recording location's parent chain silently stopped
    at the place itself (bug #248: "Church of the Holy Trinity" got added
    with no parent). Best-effort: a failed lookup just leaves that one place
    without an area, same as every other place/area resolution failure in
    this module."""
    configure()
    try:
        result = musicbrainzngs.get_place_by_id(place_mbid)
    except Exception as e:  # ruff: ignore[blind-except]
        logger.warning(f"Could not resolve area for place {place_mbid}: {e}")
        return None, None
    area = (result.get("place") or {}).get("area") or {}
    return area.get("id"), area.get("name")


def resolve_area_chain(
    area_mbid: str, cache: dict[str, list[dict[str, Any]]]
) -> list[dict[str, Any]]:
    """Walk MusicBrainz Area "part of" relations upward from `area_mbid` to
    build its full containing chain (e.g. district -> city -> subdivision
    -> country), immediate parent first. `cache` is a plain dict the caller
    owns and reuses across a whole import (keyed by area MBID) so a studio's
    area chain is only ever walked once even when many tracks share it.

    MusicBrainz Area entities generally don't carry their own coordinates
    (unlike Place entities) -- latitude/longitude are left None here rather
    than guessed.
    """
    if area_mbid in cache:
        return cache[area_mbid]

    configure()
    try:
        result = musicbrainzngs.get_area_by_id(area_mbid, includes=["area-rels"])
    except Exception as e:
        # Intentional broad boundary catch: musicbrainzngs has no single
        # exception hierarchy covering every failure mode it can raise
        # (network errors, XML parse errors, HTTP errors, auth/rate-limit
        # errors) — wrap all of them into this module's MusicBrainzLookupError
        # so every caller elsewhere in the codebase only ever has to catch
        # one type.
        raise MusicBrainzLookupError(str(e)) from e

    area = result.get("area", {})
    chain = [
        {
            "mbid": area_mbid,
            "name": area.get("name") or "",
            "type": area.get("type"),
            "latitude": None,
            "longitude": None,
        }
    ]

    for rel in area.get("area-relation-list", []) or []:
        if rel.get("type") != "part of" or rel.get("direction") != "backward":
            # MusicBrainz's "part of" relation runs smaller-area -> larger-
            # area (entity0 -> entity1), but the <direction> tag the API
            # attaches is relative to the *link phrase*, not to which side
            # is bigger: querying the smaller area (entity0, e.g. "North
            # Carolina") returns its own "part of" link to the larger area
            # (entity1, e.g. "United States") tagged direction="backward",
            # with rel["area"] set to that larger parent. Absent/"forward"
            # is what shows up on the *container* side instead, pointing at
            # one of its children -- e.g. querying "North Carolina" for
            # relations where NC is entity1 surfaces a "forward" link to a
            # contained county. Confirmed against the live MusicBrainz API
            # (see bug #246): treating "forward" as "parent" walks into a
            # child instead and silently inverts the whole chain.
            continue
        parent = rel.get("area") or {}
        if not parent.get("id"):
            continue
        chain.extend(resolve_area_chain(parent["id"], cache))
        break

    cache[area_mbid] = chain
    return chain
