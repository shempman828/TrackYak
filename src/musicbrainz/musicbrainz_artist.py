"""
musicbrainz_artist.py

Artist search and enrichment: name/alias search, full-artist follow-up
lookup (wikipedia/website links, aliases, birth/death place chains,
band-membership relations).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import musicbrainzngs

from src.foundation.logger_config import logger
from src.musicbrainz.musicbrainz_core import (
    MBCandidate,
    MusicBrainzLookupError,
    _escape_lucene,
    _parse_partial_date,
    configure,
    resolve_area_chain,
)


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
    end = life_span.get("end") or ("present" if life_span.get("ended") == "false" else "")
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
    if a.get("sort-name"):
        enrichment["sort_name"] = a["sort-name"]
    if a.get("gender"):
        enrichment["gender"] = a["gender"]
    if a.get("disambiguation"):
        enrichment["disambiguation"] = a["disambiguation"]
    enrichment.update(_parse_partial_date(life_span.get("begin"), "begin"))
    enrichment.update(_parse_partial_date(life_span.get("end"), "end"))
    return enrichment


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


def suggest_artist_names(artist_name: str, limit: int = 5) -> list[str]:
    """Best-effort "did you mean" suggestions for an artist name whose
    canonical-album search came back with zero matches -- e.g. a
    misspelling, or a name MusicBrainz files under a related but
    differently-named entity. Returns display labels (name plus type/
    disambiguation, same formatting as search_artists), purely for showing
    the user something actionable instead of a bare empty result.
    Best-effort: any lookup failure just yields no suggestions rather than
    surfacing a second error on top of the original empty result."""
    try:
        candidates = search_artists(artist_name, limit=limit)
    except MusicBrainzLookupError:
        return []
    return [c.label for c in candidates]


# Relation "type" strings, per MusicBrainz's url-relationship vocabulary.
_ARTIST_LINK_RELATIONS = {"wikipedia": "wikipedia_link", "official homepage": "website_link"}


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
            relations.birthplace_chain = resolve_area_chain(relations.birthplace_mbid, cache)
        except MusicBrainzLookupError as e:
            logger.warning(f"Could not resolve birthplace area chain: {e}")
    if relations.deathplace_mbid:
        try:
            relations.deathplace_chain = resolve_area_chain(relations.deathplace_mbid, cache)
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
