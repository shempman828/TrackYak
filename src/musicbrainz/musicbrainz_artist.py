"""MusicBrainz artist search, identity resolution and full-artist enrichment."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import musicbrainzngs

from src.foundation.logger_config import logger
from src.musicbrainz.musicbrainz_core import MBCandidate, MusicBrainzLookupError, _escape_lucene, _is_blank, _mb_call, _parse_partial_date, resolve_area_chain


@dataclass
class MBAlias:
    """One alternate name of an artist or release-group."""

    name: str
    type: str  # MusicBrainz alias type, e.g. "Legal name", "Artist name"


@dataclass
class MBGroupRelation:
    """One 'member of band' relation; mbid/name is the group or the member, see MBArtistRelations.is_group."""

    mbid: str
    name: str
    role: str | None
    begin_year: int | None
    end_year: int | None
    is_current: bool


@dataclass
class MBArtistRelations:
    """Relational artist enrichment that callers must dedup and confirm before they write it."""

    is_group: bool = False
    aliases: list[MBAlias] = field(default_factory=list)
    birthplace: str | None = None
    birthplace_mbid: str | None = None
    # Containing area chains, immediate area first; entries are
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
    """Picker label for a life-span, e.g. "[1985-present]" (en dash), or "" without a begin date."""
    begin = life_span.get("begin") or ""
    if not begin:
        return ""
    end = life_span.get("end") or ("present" if life_span.get("ended") == "false" else "")
    return f"[{begin}–{end}]" if end else f"[{begin}]"  # noqa: RUF001 -- en dash is display text


def _extract_scalar_enrichment(a: dict[str, Any]) -> dict[str, Any]:
    """ORM-field-name -> value pairs shared by an artist search hit and a full artist lookup."""
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
    """Search artists by name, sort-name and alias; returns [] for a blank name."""
    if _is_blank(name):
        return []
    # Unrestricted query, not `artist=name`: the default field also matches aliases.
    result = _mb_call(musicbrainzngs.search_artists, _escape_lucene(name), limit=limit)

    candidates = []
    for a in result.get("artist-list", []):
        if not a.get("id"):
            continue
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

        candidates.append(MBCandidate(id=a["id"], label=" ".join(label_bits), enrichment=enrichment))
    return candidates


def suggest_artist_names(artist_name: str, limit: int = 5) -> list[str]:
    """Best-effort "did you mean" artist labels for an empty album search; [] on failure."""
    try:
        candidates = search_artists(artist_name, limit=limit)
    except MusicBrainzLookupError:
        return []
    return [c.label for c in candidates]


def _resolve_primary_artist_mbids(artist_name: str) -> list[str]:
    """MBIDs of the top artist hit plus any hit whose name strictly contains the query."""
    # MB often splits an ensemble credit ("Glenn Miller and His Orchestra") from the solo
    # entity; the superset rule catches it without unrelated same-named people.
    if _is_blank(artist_name):
        return []
    result = _mb_call(musicbrainzngs.search_artists, _escape_lucene(artist_name), limit=10)

    artists = [a for a in result.get("artist-list", []) if a.get("id")]
    if not artists:
        return []

    query = artist_name.strip().lower()
    mbids = [artists[0]["id"]]
    for a in artists[1:]:
        name = (a.get("name") or "").lower()
        if name != query and query in name:
            mbids.append(a["id"])
    return mbids


def _resolve_artist_mbid(artist_name: str) -> str | None:
    """Best-effort alias-aware artist MBID for an `arid:` release filter, or None."""
    # The `artist:` field misses alias-only names (e.g. "KoЯn" for Korn).
    try:
        candidates = search_artists(artist_name, limit=1)
    except MusicBrainzLookupError:
        return None
    return candidates[0].id if candidates else None


# Relation "type" strings, per MusicBrainz's url-relationship vocabulary.
_ARTIST_LINK_RELATIONS = {"wikipedia": "wikipedia_link", "official homepage": "website_link"}


def _fetch_full_artist(mbid: str) -> dict[str, Any]:
    """Full artist lookup with url-rels, aliases and artist-rels; raises MusicBrainzLookupError."""
    return _mb_call(musicbrainzngs.get_artist_by_id, mbid, includes=["url-rels", "aliases", "artist-rels"]).get("artist", {})


def _apply_full_artist(candidate: MBCandidate, artist: dict[str, Any]) -> MBCandidate:
    """Fill a candidate's enrichment and .relations from a full artist response, without network calls."""
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
        # "Search hint" aliases are typos MB indexes for search (e.g. "Jhon Williams").
        if not alias_name or alias_type == "Search hint":
            continue
        if alias_name.strip().lower() == own_name:
            continue
        aliases.append(MBAlias(name=alias_name, type=alias_type))

    begin_area = artist.get("begin-area") or {}
    end_area = artist.get("end-area") or {}

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
        birthplace=begin_area.get("name"),
        birthplace_mbid=begin_area.get("id"),
        deathplace=end_area.get("name"),
        deathplace_mbid=end_area.get("id"),
        group_relations=group_relations,
    )
    return candidate


def _resolve_artist_place_chains(candidate: MBCandidate) -> None:
    """Best-effort fill of the birth/death area chains on a candidate's relations."""
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
    """Best-effort follow-up lookup for links, aliases, places and band relations after a search pick."""
    try:
        artist = _fetch_full_artist(candidate.id)
    except MusicBrainzLookupError as e:
        logger.warning(f"MusicBrainz artist lookup failed for {candidate.id}: {e}")
        return candidate
    candidate = _apply_full_artist(candidate, artist)
    _resolve_artist_place_chains(candidate)
    return candidate


def fetch_artist_by_mbid(mbid: str) -> MBCandidate:
    """Full enrichment for an already-known artist MBID; raises MusicBrainzLookupError."""
    artist = _fetch_full_artist(mbid)
    candidate = MBCandidate(id=mbid, label=artist.get("name") or mbid)
    candidate = _apply_full_artist(candidate, artist)
    _resolve_artist_place_chains(candidate)
    return candidate
