"""Credit parsing for MusicBrainz releases, recordings and works."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import musicbrainzngs

from src.musicbrainz.musicbrainz_core import _mb_call

# Artist-relation types imported as credits. Performer types take their role from
# attribute-list (e.g. "piano"); production types are credited under the type itself.
_PERFORMER_RELATION_TYPES = {"performer", "vocal", "instrument", "performing orchestra"}
_PRODUCTION_RELATION_TYPES = {"producer", "engineer", "mix", "mastering", "arranger", "orchestrator", "conductor", "programming", "remixer", "sound", "recording"}
_CREDIT_RELATION_TYPES = _PERFORMER_RELATION_TYPES | _PRODUCTION_RELATION_TYPES

# Production types whose MB slug differs from the credited noun (#326); the rest title-case cleanly.
_PRODUCTION_RELATION_DISPLAY_NAMES = {"sound": "Sound Engineer", "mix": "Mixer", "recording": "Recording Engineer"}

# Qualifier words MB puts in attribute-list next to the real instrument/vocal value
# (e.g. ["additional", "trumpet"]); dropped so the role is just "Trumpet".
_PERFORMER_ATTRIBUTE_QUALIFIERS = {"additional", "guest", "solo"}

# Work-level relation types imported as writing credits. Excludes "previous attribution"
# (superseded credit) and "dedication" (dedicatee, not a writer).
_WORK_RELATION_TYPES = {
    "composer",
    "lyricist",
    "writer",
    "librettist",
    "arranger",
    "orchestrator",
    "translator",
    "instrument arranger",
    "vocal arranger",
    "instrumentator",
    "revised by",
    "reconstructed by",
}


@dataclass
class MBTrackCredit:
    """One artist credited in one role on a release, recording or work."""

    artist_mbid: str | None
    artist_name: str  # as credited, e.g. "H. Arlen"
    role_name: str
    canonical_name: str = ""  # the artist's registered MB name, e.g. "Harold Arlen"


def _relation_role_names(rel: dict[str, Any]) -> list[str]:
    """Role name(s) for one artist-relation: one for production types, one per value for performer types."""
    rel_type = rel.get("type")
    attributes = rel.get("attribute-list") or []
    if rel_type in _PRODUCTION_RELATION_TYPES:
        # The attribute modifies the type ("assistant" + "engineer"), it does not replace it.
        base_name = _PRODUCTION_RELATION_DISPLAY_NAMES.get(rel_type, rel_type.title() if rel_type else None)
        if attributes:
            return [f"{attributes[0].title()} {base_name}"]
        return [base_name] if base_name else []
    values = [a for a in attributes if a.lower() not in _PERFORMER_ATTRIBUTE_QUALIFIERS]
    if values:
        return [v.title() for v in values]
    return [rel_type.title()] if rel_type else []


def _parse_artist_credits(entity: dict[str, Any]) -> list[MBTrackCredit]:
    """Credits from a recording's or release's artist-relation-list."""
    credits = []
    for rel in entity.get("artist-relation-list", []) or []:
        if rel.get("type") not in _CREDIT_RELATION_TYPES:
            continue
        artist = rel.get("artist") or {}
        if not artist.get("id"):
            continue
        for role_name in _relation_role_names(rel):
            credits.append(MBTrackCredit(artist_mbid=artist["id"], artist_name=rel.get("target-credit") or artist.get("name") or "", role_name=role_name, canonical_name=artist.get("name") or ""))
    return credits


def _parse_artist_credit_byline(entity: dict[str, Any], role_name: str) -> list[MBTrackCredit]:
    """Credits from an entity's `artist-credit` byline, all under `role_name`."""
    credits = []
    # musicbrainzngs interleaves the artist dicts with bare joinphrase strings (" & ").
    for entry in entity.get("artist-credit", []) or []:
        if not isinstance(entry, dict):
            continue
        artist = entry.get("artist") or {}
        if not artist.get("id"):
            continue
        credits.append(MBTrackCredit(artist_mbid=artist["id"], artist_name=entry.get("name") or artist.get("name") or "", role_name=role_name, canonical_name=artist.get("name") or ""))
    return credits


def _parse_release_artist_credit(release: dict[str, Any]) -> list[MBTrackCredit]:
    """The release's "Album Artist" byline credits."""
    return _parse_artist_credit_byline(release, "Album Artist")


def _parse_recording_artist_credit(recording: dict[str, Any]) -> list[MBTrackCredit]:
    """The recording's "Primary Artist" byline credits."""
    return _parse_artist_credit_byline(recording, "Primary Artist")


def _recording_work_mbids(recording: dict[str, Any]) -> list[str]:
    """MBIDs of the works a recording is a performance of."""
    mbids = []
    for rel in recording.get("work-relation-list", []) or []:
        if rel.get("type") != "performance":
            continue
        work = rel.get("work") or {}
        if work.get("id"):
            mbids.append(work["id"])
    return mbids


def _fetch_work_by_id(work_mbid: str) -> dict[str, Any]:
    """Full work lookup with artist-rels; raises MusicBrainzLookupError."""
    # The release response's embedded work stub has no artist-relation-list.
    return _mb_call(musicbrainzngs.get_work_by_id, work_mbid, includes=["artist-rels"]).get("work", {})


def _parse_work_credits(work: dict[str, Any]) -> list[MBTrackCredit]:
    """Writing credits (composer, lyricist, ...) from a work's artist-relation-list."""
    credits = []
    for rel in work.get("artist-relation-list", []) or []:
        rel_type = rel.get("type")
        if rel_type not in _WORK_RELATION_TYPES:
            continue
        artist = rel.get("artist") or {}
        if not artist.get("id"):
            continue
        credits.append(MBTrackCredit(artist_mbid=artist["id"], artist_name=rel.get("target-credit") or artist.get("name") or "", role_name=rel_type.title(), canonical_name=artist.get("name") or ""))
    return credits
