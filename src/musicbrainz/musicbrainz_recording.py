"""MusicBrainz recording (track) search, ISRC enrichment and canonical-album discovery."""

from __future__ import annotations

from typing import Any

import musicbrainzngs

from src.foundation.logger_config import logger
from src.musicbrainz.musicbrainz_artist import _resolve_primary_artist_mbids
from src.musicbrainz.musicbrainz_core import (
    MBCandidate,
    MusicBrainzLookupError,
    _and_query,
    _canonical_rank_parts,
    _ext_score,
    _is_blank,
    _matching_track_recording_id,
    _mb_call,
    _normalize_title,
    _parse_partial_date,
    _query_term,
    _to_int,
    configure,
)
from src.musicbrainz.musicbrainz_credits import _parse_release_artist_credit

# Page size of every browse_releases call (the MusicBrainz maximum).
_BROWSE_PAGE_SIZE = 100


def search_recordings(track_name: str, artist_name: str | None = None, album_name: str | None = None, limit: int = 25) -> list[MBCandidate]:
    """Search recordings by title or alias, refined by artist and album; returns [] for a blank title."""
    if _is_blank(track_name):
        return []
    # Unrestricted title term so aliases match too; artist/release stay field filters.
    fields: dict[str, Any] = {}
    if artist_name:
        fields["artist"] = artist_name
    if album_name:
        fields["release"] = album_name
    result = _mb_call(musicbrainzngs.search_recordings, _and_query(None, track_name, fields), limit=limit)

    candidates = []
    for rec in result.get("recording-list", []):
        if not rec.get("id"):
            continue
        enrichment: dict[str, Any] = {"MBID": rec["id"]}

        label_bits = [rec.get("title", "?")]
        credit = rec.get("artist-credit-phrase")
        if credit:
            label_bits.append(f"by {credit}")
        length_ms = _to_int(rec.get("length"))
        if length_ms:
            total_s = length_ms // 1000
            label_bits.append(f"({total_s // 60}:{total_s % 60:02d})")
        releases = rec.get("release-list") or []
        if releases:
            label_bits.append(f"— {releases[0].get('title', '')}")
        if rec.get("disambiguation"):
            label_bits.append(f"[{rec['disambiguation']}]")

        candidates.append(MBCandidate(id=rec["id"], label=" ".join(label_bits), enrichment=enrichment))
    return candidates


def complete_recording_enrichment(candidate: MBCandidate) -> MBCandidate:
    """Best-effort ISRC follow-up lookup after a recording search pick."""
    try:
        result = _mb_call(musicbrainzngs.get_recording_by_id, candidate.id, includes=["isrcs"])
    except MusicBrainzLookupError as e:
        logger.warning(f"MusicBrainz recording ISRC lookup failed for {candidate.id}: {e}")
        return candidate

    isrcs = result.get("recording", {}).get("isrc-list") or []
    if isrcs and "isrc" not in candidate.enrichment:
        candidate.enrichment["isrc"] = isrcs[0]
    return candidate


def _browse_all_releases(includes: list[str], **browse_filter: str) -> list[dict[str, Any]]:
    """Every release matching one browse filter (e.g. artist= or recording=), across all pages."""
    releases: list[dict[str, Any]] = []
    offset = 0
    while True:
        result = _mb_call(musicbrainzngs.browse_releases, includes=includes, limit=_BROWSE_PAGE_SIZE, offset=offset, **browse_filter)
        page = result.get("release-list", [])
        releases.extend(page)
        offset += len(page)
        if not page or offset >= _to_int(result.get("release-count"), 0):
            return releases


def search_canonical_album_for_recording(track_name: str, artist_name: str | None = None, recording_mbid: str | None = None, limit: int = 15) -> list[MBCandidate]:
    """One candidate per release-group that holds the recording, by its primary artist, earliest first."""
    # Not collapsed to one "earliest" release: a single can predate the album, so the user
    # judges. Each row's other pressings ride along on `alternates`. With no recording_mbid,
    # the artist's whole catalog is scanned -- recording search ordering is unstable for
    # famous tracks, so a full scan is the only way to never miss the earliest release.
    if _is_blank(track_name) and not recording_mbid:
        return []
    configure()
    artist_mbids = _resolve_primary_artist_mbids(artist_name) if artist_name else []
    artist_mbid_set = set(artist_mbids)

    def _is_primary_artist_release(r: dict[str, Any]) -> bool:
        # Exact MBID match excludes various-artists compilations that merely contain the track.
        if not artist_mbid_set:
            return True
        return any(c.artist_mbid in artist_mbid_set for c in _parse_release_artist_credit(r))

    releases_by_id: dict[str, dict[str, Any]] = {}
    release_recording: dict[str, str] = {}  # release MBID -> matched recording MBID

    def _browse_and_collect(rec_id: str) -> None:
        for r in _browse_all_releases(["release-groups", "artist-credits"], recording=rec_id):
            if r.get("id"):
                releases_by_id[r["id"]] = r
                release_recording[r["id"]] = rec_id

    if recording_mbid:
        _browse_and_collect(recording_mbid)
    elif artist_mbids:
        title_key = _normalize_title(track_name)
        for artist_mbid in artist_mbids:
            for r in _browse_all_releases(["release-groups", "artist-credits", "recordings"], artist=artist_mbid):
                if not r.get("id") or r["id"] in releases_by_id:
                    continue
                matched_recording_id = _matching_track_recording_id(r, title_key)
                if matched_recording_id is not None:
                    releases_by_id[r["id"]] = r
                    release_recording[r["id"]] = matched_recording_id
    else:
        # No artist identity: fall back to a plain-text recording search.
        result = _mb_call(musicbrainzngs.search_recordings, _query_term(track_name, {}), limit=25)
        recordings = [r for r in result.get("recording-list", []) if r.get("id")]
        if recordings:
            top_score = max(_ext_score(r) for r in recordings)
            for r in recordings:
                if top_score - _ext_score(r) <= 10:
                    _browse_and_collect(r["id"])

    releases_by_id = {rid: r for rid, r in releases_by_id.items() if _is_primary_artist_release(r)}
    if not releases_by_id:
        return []

    def _rank_key(r: dict[str, Any]):
        status_rank, date_key, country_rank = _canonical_rank_parts(r)
        return (status_rank, *date_key, country_rank)

    # Group by release-group; the best-ranked release is the row, the rest become alternates.
    groups: dict[str, list[dict[str, Any]]] = {}
    for r in releases_by_id.values():
        group_id = (r.get("release-group") or {}).get("id") or r["id"]
        groups.setdefault(group_id, []).append(r)
    for releases in groups.values():
        releases.sort(key=_rank_key)

    representative_groups = sorted(groups.values(), key=lambda releases: _rank_key(releases[0]))[:limit]

    # Tag rows "Recording N of M" only when 2+ distinct recordings appear (e.g. a later
    # re-recording under the same title), keyed off each group's top release.
    recording_earliest: dict[str, tuple] = {}
    for releases in representative_groups:
        r = releases[0]
        rec_id = release_recording.get(r["id"])
        if rec_id is None:
            continue
        key = _rank_key(r)
        if rec_id not in recording_earliest or key < recording_earliest[rec_id]:
            recording_earliest[rec_id] = key
    recording_labels: dict[str, str] = {}
    if len(recording_earliest) > 1:
        ordered = sorted(recording_earliest.items(), key=lambda kv: kv[1])
        recording_labels = {rec_id: f"Recording {i + 1} of {len(ordered)}" for i, (rec_id, _) in enumerate(ordered)}

    def _build_candidate(r: dict[str, Any]) -> MBCandidate:
        credits = _parse_release_artist_credit(r)
        release_group = r.get("release-group") or {}
        secondary_types = release_group.get("secondary-type-list") or []
        release_type = secondary_types[0] if secondary_types else release_group.get("primary-type")
        date_parts = _parse_partial_date(r.get("date"), "release")
        recording_id = release_recording.get(r["id"])
        recording_label = recording_labels.get(recording_id) if recording_id else None

        label_bits = [r.get("title", "?")]
        credit_phrase = r.get("artist-credit-phrase")
        if credit_phrase:
            label_bits.append(f"by {credit_phrase}")
        # Country is always shown: it is what tells same-group alternates apart.
        detail_bits = [b for b in (release_type, r.get("date"), r.get("status"), r.get("country")) if b]
        if detail_bits:
            label_bits.append(f"[{' — '.join(detail_bits)}]")
        if recording_label:
            label_bits.append(f"[{recording_label}]")

        return MBCandidate(
            id=r["id"],
            label=" ".join(label_bits),
            enrichment={
                "album_name": r.get("title"),
                "MBID": r["id"],
                "release_group_mbid": release_group.get("id"),
                "recording_mbid": recording_id,
                "release_year": date_parts.get("release_year"),
                "release_month": date_parts.get("release_month"),
                "release_day": date_parts.get("release_day"),
                "release_type": release_type,
                "status": r.get("status"),
                "country": r.get("country"),
                "artist_credits": [{"mbid": c.artist_mbid, "name": c.artist_name} for c in credits],
            },
        )

    candidates = []
    for releases in representative_groups:
        primary = _build_candidate(releases[0])
        primary.alternates = [_build_candidate(alt) for alt in releases[1:]]
        candidates.append(primary)
    return candidates
