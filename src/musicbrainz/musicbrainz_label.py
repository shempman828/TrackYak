"""Record-label lookup and parsing for MusicBrainz releases."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import musicbrainzngs

from src.musicbrainz.musicbrainz_core import _mb_call, _parse_partial_date


@dataclass
class MBFounderRelation:
    """One 'founder' artist-relation on a label."""

    mbid: str
    name: str


@dataclass
class MBLabelInfo:
    """One label attached to a release, enriched from a full label lookup."""

    mbid: str
    name: str
    catalog_number: str | None = None
    disambiguation: str | None = None
    annotation: str | None = None
    begin_year: int | None = None
    begin_month: int | None = None
    begin_day: int | None = None
    end_year: int | None = None
    end_month: int | None = None
    end_day: int | None = None
    # Headquarters area chain, immediate area first; entries are
    # {"mbid", "name", "type", "latitude", "longitude"}.
    area_chain: list[dict[str, Any]] = field(default_factory=list)
    founders: list[MBFounderRelation] = field(default_factory=list)


def _fetch_label_by_id(label_mbid: str) -> dict[str, Any]:
    """Full label lookup with annotation and artist-rels; raises MusicBrainzLookupError."""
    # The release's embedded label stub has no annotation or founder relations.
    return _mb_call(musicbrainzngs.get_label_by_id, label_mbid, includes=["annotation", "artist-rels"]).get("label", {})


def _parse_label(label_mbid: str, catalog_number: str | None, label: dict[str, Any]) -> tuple[MBLabelInfo, str | None]:
    """Parse a full label response into (MBLabelInfo, headquarters_area_mbid) without network calls."""
    life_span = label.get("life-span") or {}
    area = label.get("area") or {}
    annotation = (label.get("annotation") or {}).get("text") or None

    founders = []
    for rel in label.get("artist-relation-list", []) or []:
        if rel.get("type") != "founder":
            continue
        artist = rel.get("artist") or {}
        if not artist.get("id") or not artist.get("name"):
            continue
        founders.append(MBFounderRelation(mbid=artist["id"], name=artist["name"]))

    begin_parts = _parse_partial_date(life_span.get("begin"), "begin")
    end_parts = _parse_partial_date(life_span.get("end"), "end")

    return MBLabelInfo(
        mbid=label_mbid,
        name=label.get("name") or "",
        catalog_number=catalog_number,
        disambiguation=label.get("disambiguation") or None,
        annotation=annotation,
        begin_year=begin_parts.get("begin_year"),
        begin_month=begin_parts.get("begin_month"),
        begin_day=begin_parts.get("begin_day"),
        end_year=end_parts.get("end_year"),
        end_month=end_parts.get("end_month"),
        end_day=end_parts.get("end_day"),
        founders=founders,
    ), area.get("id")
