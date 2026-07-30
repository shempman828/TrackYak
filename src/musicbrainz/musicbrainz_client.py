"""
musicbrainz_client.py

Thin wrapper around musicbrainzngs for artist / release-group / recording
search. Normalizes results into MBCandidate objects: a display label for a
picker dialog, plus an `enrichment` dict of ORM-field-name -> value
containing only whatever MusicBrainz actually returned (never guesses,
never overwrites — callers apply enrichment fields only where the local
value is currently blank).

Deliberately does not touch the AcoustID web service anywhere — that's a
separate, unrelated API with its own commercial tier. This module only
talks to the free MusicBrainz metadata search service, which has no
paid tier and just asks for courteous rate limiting (handled internally
by musicbrainzngs, enabled by default).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import musicbrainzngs

from src.core.logger_config import logger

_APP_NAME = "TrackYak"
_APP_VERSION = "0.4"
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


class MusicBrainzLookupError(Exception):
    """Raised when a MusicBrainz search/lookup call fails."""


def configure() -> None:
    """Set the required MusicBrainz User-Agent. Safe to call more than once."""
    global _configured
    if _configured:
        return
    musicbrainzngs.set_useragent(_APP_NAME, _APP_VERSION, _CONTACT)
    _configured = True


@dataclass
class MBCandidate:
    id: str
    label: str
    enrichment: dict[str, Any] = field(default_factory=dict)
    relations: MBArtistRelations | None = None


@dataclass
class MBAlias:
    name: str
    type: str  # MusicBrainz alias type, e.g. "Legal name", "Artist name"


@dataclass
class MBGroupRelation:
    """One 'member of band' relation to another MusicBrainz artist. Which
    side (group vs. member) `mbid`/`name` refers to depends on whether the
    enriched candidate itself is a group -- see MBArtistRelations.is_group."""

    mbid: str
    name: str
    role: str | None
    begin_year: int | None
    end_year: int | None
    is_current: bool


@dataclass
class MBArtistRelations:
    """Relational enrichment data that can't just be dropped into a blank
    form field the way scalar enrichment is -- each of these needs
    find-or-skip dedup against existing local data before it's safe to
    write, so callers should review/confirm before applying."""

    is_group: bool = False
    aliases: list[MBAlias] = field(default_factory=list)
    birthplace: str | None = None
    birthplace_mbid: str | None = None
    # Full containing chain for birthplace/deathplace (immediate area first,
    # then its parents up to the outermost country) -- see resolve_area_chain
    # and _resolve_artist_place_chains. Each entry is
    # {"mbid", "name", "type", "latitude", "longitude"}.
    birthplace_chain: list[dict[str, Any]] = field(default_factory=list)
    deathplace: str | None = None
    deathplace_mbid: str | None = None
    deathplace_chain: list[dict[str, Any]] = field(default_factory=list)
    group_relations: list[MBGroupRelation] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
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


def _parse_year(date_str: str | None) -> int | None:
    """Parse just the leading year out of a MusicBrainz partial date."""
    if not date_str:
        return None
    try:
        return int(date_str.split("-")[0])
    except ValueError:
        return None


def _life_span_label(life_span: dict) -> str:
    begin = life_span.get("begin") or ""
    if not begin:
        return ""
    end = life_span.get("end") or (
        "present" if life_span.get("ended") == "false" else ""
    )
    return f"[{begin}–{end}]" if end else f"[{begin}]"


def _extract_scalar_enrichment(a: dict[str, Any]) -> dict[str, Any]:
    """Pull the flat ORM-field-name -> value pairs common to both a search
    result list-item and a full get_artist_by_id lookup -- same shape
    either way, so both search_artists() and fetch_artist_by_mbid() (which
    skips search entirely) populate the same scalar fields."""
    life_span = a.get("life-span") or {}
    enrichment: dict[str, Any] = {"MBID": a["id"]}

    artist_type = a.get("type")
    if artist_type:
        enrichment["isgroup"] = 0 if artist_type == "Person" else 1
    if a.get("gender"):
        enrichment["gender"] = a["gender"]
    if a.get("disambiguation"):
        enrichment["disambiguation"] = a["disambiguation"]
    enrichment.update(_parse_partial_date(life_span.get("begin"), "begin"))
    enrichment.update(_parse_partial_date(life_span.get("end"), "end"))
    return enrichment


# ---------------------------------------------------------------------------
# Artist
# ---------------------------------------------------------------------------


def search_artists(name: str, limit: int = 25) -> list[MBCandidate]:
    configure()
    try:
        # Pass the term as an unrestricted query (not `artist=name`) so it's
        # matched against MusicBrainz's default search field, which covers
        # name, sort-name, AND alias -- same as a plain musicbrainz.org
        # search box query. `artist=name` restricts to the name field only
        # and misses alias-only matches.
        result = musicbrainzngs.search_artists(_escape_lucene(name), limit=limit)
    except Exception as e:
        # Intentional broad boundary catch: musicbrainzngs has no single
        # exception hierarchy covering every failure mode it can raise
        # (network errors, XML parse errors, HTTP errors, auth/rate-limit
        # errors) — wrap all of them into this module's MusicBrainzLookupError
        # so every caller elsewhere in the codebase only ever has to catch
        # one type.
        raise MusicBrainzLookupError(str(e)) from e

    candidates = []
    for a in result.get("artist-list", []):
        life_span = a.get("life-span") or {}
        enrichment = _extract_scalar_enrichment(a)

        label_bits = [a.get("name", "?")]
        artist_type = a.get("type")
        if artist_type:
            label_bits.append(f"({artist_type})")
        span_label = _life_span_label(life_span)
        if span_label:
            label_bits.append(span_label)
        if a.get("disambiguation"):
            label_bits.append(f"— {a['disambiguation']}")

        candidates.append(
            MBCandidate(id=a["id"], label=" ".join(label_bits), enrichment=enrichment)
        )
    return candidates


# Relation "type" strings, per MusicBrainz's url-relationship vocabulary.
_ARTIST_LINK_RELATIONS = {
    "wikipedia": "wikipedia_link",
    "official homepage": "website_link",
}


def _fetch_full_artist(mbid: str) -> dict[str, Any]:
    """Raw get_artist_by_id call with every include this module uses.
    Raises MusicBrainzLookupError on failure -- callers decide whether that
    should be best-effort (complete_artist_enrichment) or surfaced
    (fetch_artist_by_mbid)."""
    configure()
    try:
        result = musicbrainzngs.get_artist_by_id(
            mbid, includes=["url-rels", "aliases", "artist-rels"]
        )
    except Exception as e:
        # Intentional broad boundary catch: musicbrainzngs has no single
        # exception hierarchy covering every failure mode it can raise
        # (network errors, XML parse errors, HTTP errors, auth/rate-limit
        # errors) — wrap all of them into this module's MusicBrainzLookupError
        # so every caller elsewhere in the codebase only ever has to catch
        # one type.
        raise MusicBrainzLookupError(str(e)) from e
    return result.get("artist", {})


def _apply_full_artist(candidate: MBCandidate, artist: dict[str, Any]) -> MBCandidate:
    """Populate a candidate's scalar enrichment and .relations from a full
    get_artist_by_id response. Pure parsing, no network -- shared by
    complete_artist_enrichment (best-effort follow-up after a search pick)
    and fetch_artist_by_mbid (direct fetch when the MBID is already known)."""
    candidate.enrichment.update(_extract_scalar_enrichment(artist))

    for rel in artist.get("url-relation-list", []) or []:
        field_name = _ARTIST_LINK_RELATIONS.get(rel.get("type"))
        if field_name and field_name not in candidate.enrichment:
            candidate.enrichment[field_name] = rel.get("target")

    own_name = (artist.get("name") or "").strip().lower()
    aliases = []
    for al in artist.get("alias-list", []) or []:
        alias_name = al.get("alias")
        alias_type = al.get("type") or ""
        # "Search hint" aliases are typos/misspellings MB indexes for
        # search matching (e.g. "Jhon Williams") -- not real names.
        if not alias_name or alias_type == "Search hint":
            continue
        if alias_name.strip().lower() == own_name:
            continue
        aliases.append(MBAlias(name=alias_name, type=alias_type))

    begin_area = artist.get("begin-area") or {}
    end_area = artist.get("end-area") or {}
    birthplace = begin_area.get("name")
    birthplace_mbid = begin_area.get("id")
    deathplace = end_area.get("name")
    deathplace_mbid = end_area.get("id")

    artist_type = artist.get("type")
    is_group = bool(artist_type) and artist_type != "Person"

    group_relations = []
    for rel in artist.get("artist-relation-list", []) or []:
        if rel.get("type") != "member of band":
            continue
        target = rel.get("artist") or {}
        if not target.get("id") or not target.get("name"):
            continue
        group_relations.append(
            MBGroupRelation(
                mbid=target["id"],
                name=target["name"],
                role=", ".join(rel.get("attribute-list", []) or []) or None,
                begin_year=_parse_year(rel.get("begin")),
                end_year=_parse_year(rel.get("end")),
                is_current=rel.get("ended") == "false",
            )
        )

    candidate.relations = MBArtistRelations(
        is_group=is_group,
        aliases=aliases,
        birthplace=birthplace,
        birthplace_mbid=birthplace_mbid,
        deathplace=deathplace,
        deathplace_mbid=deathplace_mbid,
        group_relations=group_relations,
    )
    return candidate


def _resolve_artist_place_chains(candidate: MBCandidate) -> None:
    """Walk the candidate's birth/death areas up to their full containing
    chain (city -> county -> state -> country, etc) via resolve_area_chain.
    Separate from _apply_full_artist (which is pure parsing) because this
    makes network calls -- run once per fetch, after relations are populated,
    by both complete_artist_enrichment and fetch_artist_by_mbid. Best-effort:
    a failed chain walk just leaves that place as a bare name, same as
    before this existed."""
    relations = candidate.relations
    if relations is None:
        return
    cache: dict[str, list[dict[str, Any]]] = {}
    if relations.birthplace_mbid:
        try:
            relations.birthplace_chain = resolve_area_chain(
                relations.birthplace_mbid, cache
            )
        except MusicBrainzLookupError as e:
            logger.warning(f"Could not resolve birthplace area chain: {e}")
    if relations.deathplace_mbid:
        try:
            relations.deathplace_chain = resolve_area_chain(
                relations.deathplace_mbid, cache
            )
        except MusicBrainzLookupError as e:
            logger.warning(f"Could not resolve deathplace area chain: {e}")


def complete_artist_enrichment(candidate: MBCandidate) -> MBCandidate:
    """Follow up a search result with a lookup for wikipedia/website links,
    aliases, birth/death area (plus its full containing place chain), and
    band-membership relations -- none of which the search endpoint returns.
    Best-effort: any failure just leaves the candidate's enrichment as-is."""
    try:
        artist = _fetch_full_artist(candidate.id)
    except MusicBrainzLookupError as e:
        logger.warning(f"MusicBrainz artist lookup failed for {candidate.id}: {e}")
        return candidate
    candidate = _apply_full_artist(candidate, artist)
    _resolve_artist_place_chains(candidate)
    return candidate


def fetch_artist_by_mbid(mbid: str) -> MBCandidate:
    """Fetch full enrichment for an artist already matched to a MusicBrainz
    ID (the MBID field is already filled in), skipping the name-search
    step entirely. Raises MusicBrainzLookupError on failure -- unlike
    complete_artist_enrichment, there's no search result to fall back to."""
    artist = _fetch_full_artist(mbid)
    candidate = MBCandidate(id=mbid, label=artist.get("name") or mbid)
    candidate = _apply_full_artist(candidate, artist)
    _resolve_artist_place_chains(candidate)
    return candidate


# ---------------------------------------------------------------------------
# Release (Album)
#
# Searches the `release` endpoint directly -- not `release-group` -- so a
# single pick carries real per-pressing data (status, language/script,
# catalog number, barcode, credits, recording locations) instead of the
# release-group's abstract aggregate. A release-group can have 100+ member
# releases (regional pressings, reissues, box sets), so search results are
# ranked to put the *canonical* release first: official status, earliest
# date, a fixed country preference, fewest media. The picker dialog still
# shows the ranked list and lets the user override the top pick.
# ---------------------------------------------------------------------------

# ISO 639-2/B codes MusicBrainz returns for text-representation.language,
# mapped to the plain-English names the album_language field already
# suggests (see ALBUM_LANGUAGE_SUGGESTIONS in base_album_edit.py) -- MB's
# "zxx" (no linguistic content) and "mul" (multiple languages) map directly
# onto that list's "Instrumental"/"Multiple" entries.
_MB_LANGUAGE_NAMES = {
    "eng": "English",
    "fra": "French",
    "fre": "French",
    "deu": "German",
    "ger": "German",
    "ita": "Italian",
    "spa": "Spanish",
    "por": "Portuguese",
    "jpn": "Japanese",
    "kor": "Korean",
    "zho": "Chinese",
    "chi": "Chinese",
    "rus": "Russian",
    "zxx": "Instrumental",
    "mul": "Multiple",
}

# Preferred release country order, best first, used as a canonical-release
# tie-breaker -- "XW" is MusicBrainz's "Worldwide" pseudo-country.
_COUNTRY_PREFERENCE = ("XW", "GB", "US")

# Artist-relation types treated as a credit, whether attached to a recording
# (track credit) or directly to the release (album credit): performer/
# instrument/vocal relations carry the actual instrument/vocal name in
# attribute-list (e.g. "piano", "lead vocals"); production relations are
# credited under the relation type itself when no attribute refines it
# further. Anything outside this set (e.g. "samples material", "cover art")
# is not imported as a credit.
_PERFORMER_RELATION_TYPES = {"performer", "vocal", "instrument"}
_PRODUCTION_RELATION_TYPES = {
    "producer",
    "engineer",
    "mix",
    "mastering",
    "arranger",
    "orchestrator",
    "conductor",
    "programming",
    "remixer",
}
_CREDIT_RELATION_TYPES = _PERFORMER_RELATION_TYPES | _PRODUCTION_RELATION_TYPES

_SIDE_TRACK_NUMBER_RE = re.compile(r"^([A-Za-z])(\d+)$")


@dataclass
class MBTrackCredit:
    artist_mbid: str | None
    artist_name: str
    role_name: str
    # The artist's canonical MB name, distinct from artist_name (the
    # as-credited/target-credit name, which can be a variant like "H. Arlen"
    # for canonical "Harold Arlen") when a release prints a different credit
    # than the artist's registered name. Empty when unavailable.
    canonical_name: str = ""


@dataclass
class MBReleaseTrack:
    disc_number: int
    disc_title: str | None
    track_number: int | None
    side: str | None
    title: str
    recording_mbid: str
    credits: list[MBTrackCredit] = field(default_factory=list)
    location_place_mbid: str | None = None
    # Sequential position within the medium's tracklist, spanning both
    # sides for vinyl (e.g. "B1" on a 7-track-per-side LP is absolute
    # position 8). Same value as track_number for non-vinyl releases
    # (no side letter to make them diverge). Local track numbering is
    # absolute, so matching against local tracks must use this, not the
    # side-relative track_number.
    absolute_position: int | None = None


@dataclass
class MBReleaseDetail:
    release_group_mbid: str | None
    mbid: str | None = None
    status: str | None = None
    language: str | None = None
    catalog_number: str | None = None
    discogs_master_url: str | None = None
    barcode: str | None = None
    release_year: int | None = None
    release_month: int | None = None
    release_day: int | None = None
    # Credits attached to the release itself (e.g. an album-wide producer or
    # "mastered by" relation not tied to any one recording) -- distinct from
    # each MBReleaseTrack's own per-recording credits.
    credits: list[MBTrackCredit] = field(default_factory=list)
    tracks: list[MBReleaseTrack] = field(default_factory=list)
    # place MBID -> chain from that place up to the outermost area, each
    # entry {"mbid", "name", "type", "latitude", "longitude"} -- index 0 is
    # the place itself (e.g. the studio), the rest are its containing areas
    # in order (district, city, subdivision, country, ...).
    place_chains: dict[str, list[dict[str, Any]]] = field(default_factory=dict)


def _parse_track_number_side(
    number: str | None, position: str | None
) -> tuple[str | None, int | None]:
    """MB's per-track `number` is a display string -- for vinyl-style media
    it's often side+position (e.g. "A1", "B2"); split that into a side
    letter and an in-side track number. Otherwise fall back to the medium's
    plain sequential `position`."""
    if number:
        m = _SIDE_TRACK_NUMBER_RE.match(number.strip())
        if m:
            return m.group(1).upper(), int(m.group(2))
    try:
        return None, int(position)
    except (TypeError, ValueError):
        return None, None


def _relation_role_name(rel: dict[str, Any]) -> str | None:
    """One credit's Role name for an artist-relation: the attribute
    (instrument/vocal/sub-role) if present, else the relation type itself
    -- both title-cased. Same rule for performer and production relations,
    since MB doesn't consistently model a nuance as a `type` vs. an
    `attribute` across relation kinds."""
    attributes = rel.get("attribute-list") or []
    if attributes:
        return attributes[0].title()
    rel_type = rel.get("type")
    return rel_type.title() if rel_type else None


def _parse_artist_credits(entity: dict[str, Any]) -> list[MBTrackCredit]:
    """Parse credits from `entity`'s artist-relation-list -- works for both
    a recording (track credit) and a release (album credit), since MB
    shapes the relation dicts the same way at either level."""
    credits = []
    for rel in entity.get("artist-relation-list", []) or []:
        if rel.get("type") not in _CREDIT_RELATION_TYPES:
            continue
        artist = rel.get("artist") or {}
        if not artist.get("id"):
            continue
        role_name = _relation_role_name(rel)
        if not role_name:
            continue
        credits.append(
            MBTrackCredit(
                artist_mbid=artist["id"],
                artist_name=rel.get("target-credit") or artist.get("name") or "",
                role_name=role_name,
                canonical_name=artist.get("name") or "",
            )
        )
    return credits


def _parse_recording_location(recording: dict[str, Any]) -> dict[str, Any] | None:
    """Extract the "recorded at" place relation on a recording, if any, as a
    flat dict -- the immediate place plus its immediate area (id/name only;
    the full area chain is resolved separately via resolve_area_chain)."""
    for rel in recording.get("place-relation-list", []) or []:
        if rel.get("type") != "recorded at":
            continue
        place = rel.get("place") or {}
        if not place.get("id"):
            continue
        coords = place.get("coordinates") or {}
        area = place.get("area") or {}
        return {
            "place_mbid": place["id"],
            "place_name": place.get("name") or "",
            "place_type": place.get("type"),
            "latitude": _to_float(coords.get("latitude")),
            "longitude": _to_float(coords.get("longitude")),
            "area_mbid": area.get("id"),
            "area_name": area.get("name"),
        }
    return None


def _to_float(value) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _resolve_artist_mbid(artist_name: str) -> str | None:
    """Resolve an artist name to a MusicBrainz artist MBID via the
    alias-aware artist search, so release search can filter by `arid` --
    exact and unambiguous -- instead of a literal `artist:` name match.

    The `artist:` field only matches an artist's canonical name/sort-name,
    not their aliases -- so a name that's only on file as an alias (e.g. a
    stylized band-logo spelling like "KoЯn", credited-in-DB name for Korn)
    fails to filter anything there, and the release search silently falls
    back to ranking by date/status alone, letting an unrelated same-titled
    release from a different artist outrank the real one. search_artists()
    already does this alias matching correctly (see its own docstring).
    """
    try:
        candidates = search_artists(artist_name, limit=1)
    except MusicBrainzLookupError:
        return None
    return candidates[0].id if candidates else None


def search_canonical_releases(
    album_name: str, artist_name: str | None = None, limit: int = 100
) -> list[MBCandidate]:
    """Search MusicBrainz releases (not release-groups) and rank them so the
    single canonical pressing -- official, earliest, most "worldwide"/
    default-country -- sorts first. `MBCandidate.id` is a release MBID,
    ready to pass straight to fetch_release_detail()."""
    configure()
    fields: dict[str, Any] = {}
    if artist_name:
        artist_mbid = _resolve_artist_mbid(artist_name)
        if artist_mbid:
            fields["arid"] = artist_mbid
        else:
            fields["artist"] = artist_name
    try:
        result = musicbrainzngs.search_releases(
            _query_term(album_name, fields), limit=limit, **fields
        )
    except Exception as e:
        # Intentional broad boundary catch: musicbrainzngs has no single
        # exception hierarchy covering every failure mode it can raise
        # (network errors, XML parse errors, HTTP errors, auth/rate-limit
        # errors) — wrap all of them into this module's MusicBrainzLookupError
        # so every caller elsewhere in the codebase only ever has to catch
        # one type.
        raise MusicBrainzLookupError(str(e)) from e

    releases = result.get("release-list", [])
    if not releases:
        return []

    def _score(r: dict[str, Any]) -> float:
        try:
            return float(r.get("ext:score", 0))
        except (TypeError, ValueError):
            return 0.0

    top_score = max(_score(r) for r in releases)
    # Keep everything within 10 points of MB's own top relevance score --
    # the canonical-ranking heuristic below only needs to choose among
    # releases MB itself considers close matches, not the whole result set.
    candidates_pool = [r for r in releases if top_score - _score(r) <= 10]

    def _rank_key(r: dict[str, Any]):
        status_rank = 0 if (r.get("status") or "").lower() == "official" else 1
        year_month_day = _parse_partial_date(r.get("date"), "d")
        date_key = (
            year_month_day.get("d_year", 9999),
            year_month_day.get("d_month", 99),
            year_month_day.get("d_day", 99),
        )
        country = r.get("country") or ""
        country_rank = (
            _COUNTRY_PREFERENCE.index(country)
            if country in _COUNTRY_PREFERENCE
            else len(_COUNTRY_PREFERENCE)
        )
        media_count = len(r.get("medium-list", []) or [])
        return (status_rank, date_key, country_rank, media_count)

    candidates_pool.sort(key=_rank_key)

    candidates = []
    for r in candidates_pool:
        label_bits = [r.get("title", "?")]
        credit = r.get("artist-credit-phrase")
        if credit:
            label_bits.append(f"by {credit}")
        status = r.get("status")
        date = r.get("date")
        country = r.get("country")
        detail_bits = [b for b in (status, date, country) if b]
        if detail_bits:
            label_bits.append(f"[{' — '.join(detail_bits)}]")
        catalog = next(
            (
                li.get("catalog-number")
                for li in (r.get("label-info-list") or [])
                if li.get("catalog-number")
            ),
            None,
        )
        if catalog:
            label_bits.append(f"({catalog})")

        candidates.append(MBCandidate(id=r["id"], label=" ".join(label_bits)))
    return candidates


def fetch_release_detail(
    release_mbid: str, progress_callback: Callable[[int, int], None] | None = None
) -> MBReleaseDetail:
    """Fetch every rich per-pressing detail this feature imports for one
    release: album-level scalars, album-level credits, Discogs master link,
    per-track number/side/credits, and recording locations (resolved into a
    full Place parent chain, cached so a studio shared across many tracks is
    only walked once).

    `progress_callback(current, total)`, if given, is called once per
    unique recording-location area resolved -- lets the UI switch from an
    indeterminate spinner to a determinate counter when there's enough
    location data to make that worthwhile (see album_musicbrainz_mixin.py).
    """
    configure()
    try:
        result = musicbrainzngs.get_release_by_id(
            release_mbid,
            includes=[
                "artist-credits",
                "recordings",
                "recording-level-rels",
                "artist-rels",
                "place-rels",
                "work-rels",
                "labels",
                "release-groups",
                "media",
                "url-rels",
            ],
        )
    except Exception as e:
        # Intentional broad boundary catch: musicbrainzngs has no single
        # exception hierarchy covering every failure mode it can raise
        # (network errors, XML parse errors, HTTP errors, auth/rate-limit
        # errors) — wrap all of them into this module's MusicBrainzLookupError
        # so every caller elsewhere in the codebase only ever has to catch
        # one type.
        raise MusicBrainzLookupError(str(e)) from e

    release = result.get("release", {})

    catalog_number = next(
        (
            li.get("catalog-number")
            for li in (release.get("label-info-list") or [])
            if li.get("catalog-number")
        ),
        None,
    )

    discogs_master_url = None
    for rel in release.get("url-relation-list", []) or []:
        if rel.get("type") == "discogs" and "/master/" in (rel.get("target") or ""):
            discogs_master_url = rel["target"]
            break

    language_code = (release.get("text-representation") or {}).get("language")
    date_parts = _parse_partial_date(release.get("date"), "release")

    detail = MBReleaseDetail(
        release_group_mbid=(release.get("release-group") or {}).get("id"),
        mbid=release.get("id") or release_mbid,
        status=release.get("status"),
        language=_MB_LANGUAGE_NAMES.get(language_code, language_code),
        catalog_number=catalog_number,
        discogs_master_url=discogs_master_url,
        barcode=release.get("barcode"),
        release_year=date_parts.get("release_year"),
        release_month=date_parts.get("release_month"),
        release_day=date_parts.get("release_day"),
        credits=_parse_artist_credits(release),
    )

    # First pass: parse every track, collecting each unique immediate area
    # MBID that needs its parent chain resolved, before making any of those
    # extra requests -- this is what lets progress_callback report a real
    # total up front instead of an ever-growing one.
    raw_locations: dict[str, dict[str, Any]] = {}  # place_mbid -> raw location dict
    pending_areas: dict[str, None] = {}  # ordered set of area MBIDs to resolve

    for medium in release.get("medium-list", []) or []:
        disc_number = int(medium.get("position") or 0)
        disc_title = medium.get("title")
        for track in medium.get("track-list", []) or []:
            recording = track.get("recording") or {}
            side, track_number = _parse_track_number_side(
                track.get("number"), track.get("position")
            )
            try:
                absolute_position = int(track.get("position"))
            except (TypeError, ValueError):
                absolute_position = None
            mb_track = MBReleaseTrack(
                disc_number=disc_number,
                disc_title=disc_title,
                track_number=track_number,
                side=side,
                title=recording.get("title") or track.get("title") or "",
                recording_mbid=recording.get("id") or "",
                credits=_parse_artist_credits(recording),
                absolute_position=absolute_position,
            )
            location = _parse_recording_location(recording)
            if location:
                mb_track.location_place_mbid = location["place_mbid"]
                raw_locations[location["place_mbid"]] = location
                if location.get("area_mbid"):
                    pending_areas.setdefault(location["area_mbid"], None)
            detail.tracks.append(mb_track)

    total_areas = len(pending_areas)
    if progress_callback:
        progress_callback(0, total_areas)
    area_cache: dict[str, list[dict[str, Any]]] = {}
    for idx, area_mbid in enumerate(pending_areas, start=1):
        resolve_area_chain(area_mbid, area_cache)
        if progress_callback:
            progress_callback(idx, total_areas)

    for place_mbid, location in raw_locations.items():
        place_node = {
            "mbid": place_mbid,
            "name": location["place_name"],
            "type": location.get("place_type"),
            "latitude": location.get("latitude"),
            "longitude": location.get("longitude"),
        }
        area_chain = (
            area_cache.get(location.get("area_mbid"), [])
            if location.get("area_mbid")
            else []
        )
        detail.place_chains[place_mbid] = [place_node] + area_chain

    return detail


def fetch_release_group_aliases(release_group_mbid: str) -> list[MBAlias]:
    """Alternate album titles live on the release-group (the abstract
    "album" entity shared by every pressing), not the specific release --
    a separate, small follow-up call from fetch_release_detail."""
    configure()
    try:
        result = musicbrainzngs.get_release_group_by_id(
            release_group_mbid, includes=["aliases"]
        )
    except Exception as e:
        # Intentional broad boundary catch: musicbrainzngs has no single
        # exception hierarchy covering every failure mode it can raise
        # (network errors, XML parse errors, HTTP errors, auth/rate-limit
        # errors) — wrap all of them into this module's MusicBrainzLookupError
        # so every caller elsewhere in the codebase only ever has to catch
        # one type.
        raise MusicBrainzLookupError(str(e)) from e

    aliases = []
    for al in result.get("release-group", {}).get("alias-list", []) or []:
        name = al.get("alias")
        if not name:
            continue
        aliases.append(MBAlias(name=name, type=al.get("type") or ""))
    return aliases


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
        if rel.get("type") != "part of" or rel.get("direction") == "backward":
            # MusicBrainz's "part of" relation runs smaller-area -> larger-
            # area (entity0 -> entity1). The API omits <direction> (so it's
            # absent/"forward") when the fetched area is entity0 -- i.e.
            # THIS area is the part, and rel["area"] is its parent. It's
            # only "backward" when the fetched area is entity1 (the
            # parent) and rel["area"] would be a child, which isn't what
            # we're walking toward here.
            continue
        parent = rel.get("area") or {}
        if not parent.get("id"):
            continue
        chain.extend(resolve_area_chain(parent["id"], cache))
        break

    cache[area_mbid] = chain
    return chain


# ---------------------------------------------------------------------------
# Recording (Track)
# ---------------------------------------------------------------------------


def search_recordings(
    track_name: str,
    artist_name: str | None = None,
    album_name: str | None = None,
    limit: int = 25,
) -> list[MBCandidate]:
    configure()
    # Track title is passed as an unrestricted query (not `recording=`) so it
    # also matches the recording's aliases, same as the artist fix above.
    # Artist/release stay field-restricted refinements.
    fields: dict[str, Any] = {}
    if artist_name:
        fields["artist"] = artist_name
    if album_name:
        fields["release"] = album_name
    try:
        result = musicbrainzngs.search_recordings(
            _query_term(track_name, fields), limit=limit, **fields
        )
    except Exception as e:
        # Intentional broad boundary catch: musicbrainzngs has no single
        # exception hierarchy covering every failure mode it can raise
        # (network errors, XML parse errors, HTTP errors, auth/rate-limit
        # errors) — wrap all of them into this module's MusicBrainzLookupError
        # so every caller elsewhere in the codebase only ever has to catch
        # one type.
        raise MusicBrainzLookupError(str(e)) from e

    candidates = []
    for rec in result.get("recording-list", []):
        enrichment: dict[str, Any] = {"MBID": rec["id"]}

        label_bits = [rec.get("title", "?")]
        credit = rec.get("artist-credit-phrase")
        if credit:
            label_bits.append(f"by {credit}")
        length_ms = rec.get("length")
        if length_ms:
            try:
                total_s = int(length_ms) // 1000
                label_bits.append(f"({total_s // 60}:{total_s % 60:02d})")
            except ValueError:
                pass
        releases = rec.get("release-list") or []
        if releases:
            label_bits.append(f"— {releases[0].get('title', '')}")
        if rec.get("disambiguation"):
            label_bits.append(f"[{rec['disambiguation']}]")

        candidates.append(
            MBCandidate(id=rec["id"], label=" ".join(label_bits), enrichment=enrichment)
        )
    return candidates


def complete_recording_enrichment(candidate: MBCandidate) -> MBCandidate:
    """Follow up a search result with an ISRC lookup, which the search
    endpoint doesn't return. Best-effort: any failure just leaves the
    candidate's enrichment as-is."""
    configure()
    try:
        result = musicbrainzngs.get_recording_by_id(candidate.id, includes=["isrcs"])
    except Exception as e:  # ruff: ignore[blind-except]
        logger.warning(
            f"MusicBrainz recording ISRC lookup failed for {candidate.id}: {e}"
        )
        return candidate

    isrcs = result.get("recording", {}).get("isrc-list") or []
    if isrcs and "isrc" not in candidate.enrichment:
        candidate.enrichment["isrc"] = isrcs[0]
    return candidate
