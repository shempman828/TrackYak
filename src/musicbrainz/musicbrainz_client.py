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
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

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
    enrichment: Dict[str, Any] = field(default_factory=dict)
    relations: Optional["MBArtistRelations"] = None


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
    role: Optional[str]
    begin_year: Optional[int]
    end_year: Optional[int]
    is_current: bool


@dataclass
class MBArtistRelations:
    """Relational enrichment data that can't just be dropped into a blank
    form field the way scalar enrichment is -- each of these needs
    find-or-skip dedup against existing local data before it's safe to
    write, so callers should review/confirm before applying."""

    is_group: bool = False
    aliases: List[MBAlias] = field(default_factory=list)
    birthplace: Optional[str] = None
    deathplace: Optional[str] = None
    group_relations: List[MBGroupRelation] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_partial_date(date_str: Optional[str], prefix: str) -> Dict[str, int]:
    """Parse a MusicBrainz partial date ('YYYY', 'YYYY-MM', or 'YYYY-MM-DD')
    into e.g. {"release_year": 1997, "release_month": 5} for the given
    field-name prefix, omitting any part that isn't present."""
    result: Dict[str, int] = {}
    if not date_str:
        return result
    suffixes = ("year", "month", "day")
    for suffix, part in zip(suffixes, date_str.split("-")):
        try:
            result[f"{prefix}_{suffix}"] = int(part)
        except (TypeError, ValueError):
            break
    return result


def _parse_year(date_str: Optional[str]) -> Optional[int]:
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
    end = life_span.get("end") or ("present" if life_span.get("ended") == "false" else "")
    return f"[{begin}–{end}]" if end else f"[{begin}]"


def _extract_scalar_enrichment(a: Dict[str, Any]) -> Dict[str, Any]:
    """Pull the flat ORM-field-name -> value pairs common to both a search
    result list-item and a full get_artist_by_id lookup -- same shape
    either way, so both search_artists() and fetch_artist_by_mbid() (which
    skips search entirely) populate the same scalar fields."""
    life_span = a.get("life-span") or {}
    enrichment: Dict[str, Any] = {"MBID": a["id"]}

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


def search_artists(name: str, limit: int = 25) -> List[MBCandidate]:
    configure()
    try:
        # Pass the term as an unrestricted query (not `artist=name`) so it's
        # matched against MusicBrainz's default search field, which covers
        # name, sort-name, AND alias -- same as a plain musicbrainz.org
        # search box query. `artist=name` restricts to the name field only
        # and misses alias-only matches.
        result = musicbrainzngs.search_artists(_escape_lucene(name), limit=limit)
    except Exception as e:
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


def _fetch_full_artist(mbid: str) -> Dict[str, Any]:
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
        raise MusicBrainzLookupError(str(e)) from e
    return result.get("artist", {})


def _apply_full_artist(candidate: MBCandidate, artist: Dict[str, Any]) -> MBCandidate:
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

    birthplace = (artist.get("begin-area") or {}).get("name")
    deathplace = (artist.get("end-area") or {}).get("name")

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
        deathplace=deathplace,
        group_relations=group_relations,
    )
    return candidate


def complete_artist_enrichment(candidate: MBCandidate) -> MBCandidate:
    """Follow up a search result with a lookup for wikipedia/website links,
    aliases, birth/death area, and band-membership relations -- none of
    which the search endpoint returns. Best-effort: any failure just leaves
    the candidate's enrichment as-is."""
    try:
        artist = _fetch_full_artist(candidate.id)
    except MusicBrainzLookupError as e:
        logger.warning(f"MusicBrainz artist lookup failed for {candidate.id}: {e}")
        return candidate
    return _apply_full_artist(candidate, artist)


def fetch_artist_by_mbid(mbid: str) -> MBCandidate:
    """Fetch full enrichment for an artist already matched to a MusicBrainz
    ID (the MBID field is already filled in), skipping the name-search
    step entirely. Raises MusicBrainzLookupError on failure -- unlike
    complete_artist_enrichment, there's no search result to fall back to."""
    artist = _fetch_full_artist(mbid)
    candidate = MBCandidate(id=mbid, label=artist.get("name") or mbid)
    return _apply_full_artist(candidate, artist)


# ---------------------------------------------------------------------------
# Release group (Album)
# ---------------------------------------------------------------------------


def search_release_groups(
    album_name: str, artist_name: Optional[str] = None, limit: int = 25
) -> List[MBCandidate]:
    configure()
    # Album title is passed as an unrestricted query (not `releasegroup=`) so
    # it also matches the release group's aliases, same as the artist fix
    # above. Artist stays a field-restricted AND-ed refinement.
    # NB: artist_name is passed as a field kwarg, which musicbrainzngs
    # escapes internally -- only the positional query needs pre-escaping.
    kwargs: Dict[str, Any] = {"limit": limit}
    if artist_name:
        kwargs["artist"] = artist_name
    try:
        result = musicbrainzngs.search_release_groups(_escape_lucene(album_name), **kwargs)
    except Exception as e:
        raise MusicBrainzLookupError(str(e)) from e

    candidates = []
    for rg in result.get("release-group-list", []):
        enrichment: Dict[str, Any] = {"MBID": rg["id"]}

        primary_type = rg.get("primary-type")
        if primary_type:
            enrichment["release_type"] = primary_type
        secondary_types = rg.get("secondary-type-list") or []
        if primary_type:
            # Only assert these booleans when we have real release-group type
            # data to base them on -- an authoritative "no" is still useful
            # enrichment, not a guess.
            enrichment["is_live"] = 1 if "Live" in secondary_types else 0
            enrichment["is_compilation"] = 1 if "Compilation" in secondary_types else 0
        enrichment.update(_parse_partial_date(rg.get("first-release-date"), "release"))

        label_bits = [rg.get("title", "?")]
        credit = rg.get("artist-credit-phrase")
        if credit:
            label_bits.append(f"by {credit}")
        if primary_type:
            type_bits = primary_type
            if secondary_types:
                type_bits += f" ({', '.join(secondary_types)})"
            label_bits.append(f"[{type_bits}]")
        if rg.get("first-release-date"):
            label_bits.append(f"({rg['first-release-date']})")

        candidates.append(
            MBCandidate(id=rg["id"], label=" ".join(label_bits), enrichment=enrichment)
        )
    return candidates


# ---------------------------------------------------------------------------
# Recording (Track)
# ---------------------------------------------------------------------------


def search_recordings(
    track_name: str,
    artist_name: Optional[str] = None,
    album_name: Optional[str] = None,
    limit: int = 25,
) -> List[MBCandidate]:
    configure()
    # Track title is passed as an unrestricted query (not `recording=`) so it
    # also matches the recording's aliases, same as the artist fix above.
    # Artist/release stay field-restricted AND-ed refinements.
    # NB: artist_name/album_name are passed as field kwargs, which
    # musicbrainzngs escapes internally -- only the positional query needs
    # pre-escaping.
    kwargs: Dict[str, Any] = {"limit": limit}
    if artist_name:
        kwargs["artist"] = artist_name
    if album_name:
        kwargs["release"] = album_name
    try:
        result = musicbrainzngs.search_recordings(_escape_lucene(track_name), **kwargs)
    except Exception as e:
        raise MusicBrainzLookupError(str(e)) from e

    candidates = []
    for rec in result.get("recording-list", []):
        enrichment: Dict[str, Any] = {"MBID": rec["id"]}

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
    except Exception as e:
        logger.warning(f"MusicBrainz recording ISRC lookup failed for {candidate.id}: {e}")
        return candidate

    isrcs = result.get("recording", {}).get("isrc-list") or []
    if isrcs and "isrc" not in candidate.enrichment:
        candidate.enrichment["isrc"] = isrcs[0]
    return candidate
