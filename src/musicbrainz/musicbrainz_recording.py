"""
musicbrainz_recording.py

Recording (track) search and enrichment, including "canonical album for a
recording" discovery -- reuses release-domain ranking/credit-parsing
helpers from musicbrainz_release since a recording's canonical album is
itself just a specially-filtered release search.
"""

from __future__ import annotations

from typing import Any

import musicbrainzngs

from src.core.logger_config import logger
from src.musicbrainz.musicbrainz_core import (
    MBCandidate,
    MusicBrainzLookupError,
    _and_query,
    _ext_score,
    _parse_partial_date,
    _query_term,
    configure,
)
from src.musicbrainz.musicbrainz_release import (
    _COUNTRY_PREFERENCE,
    _matching_track_recording_id,
    _normalize_title,
    _parse_release_artist_credit,
    _resolve_primary_artist_mbids,
)


def search_recordings(
    track_name: str, artist_name: str | None = None, album_name: str | None = None, limit: int = 25
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
        result = musicbrainzngs.search_recordings(_and_query(None, track_name, fields), limit=limit)
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
    except Exception as e:
        logger.warning(f"MusicBrainz recording ISRC lookup failed for {candidate.id}: {e}")
        return candidate

    isrcs = result.get("recording", {}).get("isrc-list") or []
    if isrcs and "isrc" not in candidate.enrichment:
        candidate.enrichment["isrc"] = isrcs[0]
    return candidate


def search_canonical_album_for_recording(
    track_name: str,
    artist_name: str | None = None,
    recording_mbid: str | None = None,
    limit: int = 15,
) -> list[MBCandidate]:
    """Find candidate "canonical first album" releases for a recording, by
    its primary artist.

    Deliberately does NOT collapse to a single globally-earliest release --
    a single can be released a day (or decades) before the release most
    people actually think of as "the album," and date alone can't tell
    those apart. Instead this returns one top-level candidate per
    release-group, across every type (single/EP/album/compilation), sorted
    by date -- so a human can look at the spread and judge which one is
    canonical, rather than the ranking deciding for them. That top-level
    candidate is its group's earliest/most-official/preferred-country
    pressing, but every other release in the same group (other countries,
    other pressings) rides along on `MBCandidate.alternates` rather than
    being discarded -- a date/status tie can still land on the "wrong"
    country pick, and a picker UI can offer the alternates as a same-row
    variant dropdown so the user can correct that without the group being
    buried entirely.

    If `recording_mbid` is already known (e.g. the track's own MBID from a
    prior Identification-tab lookup), it's used directly and no artist
    catalog scan is performed.

    Otherwise, once the primary artist is resolved, this scans that
    artist's *entire* release catalog (paginated, ~100 releases per call)
    rather than searching for matching recordings first. An earlier version
    searched MusicBrainz's recording index and browsed releases per
    matching recording instead -- for a heavily-reissued track that could
    mean dozens of small, sequential, rate-limited calls, AND it was
    unreliable: an extremely famous recording can tie dozens of genuinely
    distinct historical recordings at the same relevance score with
    MusicBrainz returning them in no chronological (or even stable) order,
    so no cap or early-stop on that path could avoid a real chance of
    missing the actual earliest one. Scanning the full discography instead
    is both faster in practice (far fewer, larger calls) and strictly more
    complete (deterministic -- every release is checked, so nothing can be
    missed by bad luck in result ordering).

    A matching title doesn't guarantee the same performance, though -- some
    songs have a genuinely different, later re-recording released under the
    same title and artist credit. When the final results span more than one
    distinct recording MBID, each candidate's label is tagged "Recording N
    of M" so that isn't silently conflated; `enrichment["recording_mbid"]`
    carries the specific recording either way, letting a caller stamp it
    onto a track once picked instead of only recording which album it's on.
    """
    configure()
    artist_mbids = _resolve_primary_artist_mbids(artist_name) if artist_name else []
    artist_mbid_set = set(artist_mbids)

    def _is_primary_artist_release(r: dict[str, Any]) -> bool:
        # Excludes compilations/various-artists releases that merely
        # contain the recording -- an exact MBID match against any of the
        # resolved primary-artist identities, not a name comparison (see
        # _resolve_primary_artist_mbids for why a single resolved MBID
        # isn't enough here).
        if not artist_mbid_set:
            return True
        return any(c.artist_mbid in artist_mbid_set for c in _parse_release_artist_credit(r))

    releases_by_id: dict[str, dict[str, Any]] = {}
    # release MBID -> the recording MBID that matched on it. Distinct
    # recording identities (not just distinct pressings/editions of the
    # same performance) get called out in the final labels below -- e.g. a
    # song re-recorded years later is a real, common case a title-only
    # match can't otherwise distinguish for the user.
    release_recording: dict[str, str] = {}

    def _browse_and_collect(rec_id: str) -> None:
        """Browse one known recording's releases into releases_by_id."""
        try:
            result = musicbrainzngs.browse_releases(
                recording=rec_id, includes=["release-groups", "artist-credits"], limit=100
            )
        except Exception as e:
            raise MusicBrainzLookupError(str(e)) from e
        for r in result.get("release-list", []):
            releases_by_id[r["id"]] = r
            release_recording[r["id"]] = rec_id

    if recording_mbid:
        _browse_and_collect(recording_mbid)
    elif artist_mbids:
        title_key = _normalize_title(track_name)
        for artist_mbid in artist_mbids:
            offset = 0
            while True:
                try:
                    result = musicbrainzngs.browse_releases(
                        artist=artist_mbid,
                        includes=["release-groups", "artist-credits", "recordings"],
                        limit=100,
                        offset=offset,
                    )
                except Exception as e:
                    raise MusicBrainzLookupError(str(e)) from e
                releases = result.get("release-list", [])
                for r in releases:
                    if r["id"] in releases_by_id:
                        continue
                    matched_recording_id = _matching_track_recording_id(r, title_key)
                    if matched_recording_id is not None:
                        releases_by_id[r["id"]] = r
                        release_recording[r["id"]] = matched_recording_id
                offset += len(releases)
                if not releases or offset >= result.get("release-count", 0):
                    break
    else:
        # No artist identity resolved at all (e.g. the track has no known
        # artist) -- there's no discography to browse, so fall back to a
        # plain-text recording search instead.
        try:
            result = musicbrainzngs.search_recordings(_query_term(track_name, {}), limit=25)
        except Exception as e:
            raise MusicBrainzLookupError(str(e)) from e
        recordings = result.get("recording-list", [])
        if recordings:
            top_score = max(_ext_score(r) for r in recordings)
            for r in recordings:
                if top_score - _ext_score(r) <= 10:
                    _browse_and_collect(r["id"])

    releases_by_id = {rid: r for rid, r in releases_by_id.items() if _is_primary_artist_release(r)}
    if not releases_by_id:
        return []

    def _rank_key(r: dict[str, Any]):
        status_rank = 0 if (r.get("status") or "").lower() == "official" else 1
        date_parts = _parse_partial_date(r.get("date"), "d")
        country = r.get("country") or ""
        country_rank = (
            _COUNTRY_PREFERENCE.index(country)
            if country in _COUNTRY_PREFERENCE
            else len(_COUNTRY_PREFERENCE)
        )
        return (
            status_rank,
            date_parts.get("d_year", 9999),
            date_parts.get("d_month", 99),
            date_parts.get("d_day", 99),
            country_rank,
        )

    # Group by release-group, but keep every release in the group (sorted
    # best-first by status/date/country) instead of collapsing to a single
    # representative -- otherwise a handful of countries' worth of single
    # pressings buries the one true album release-group under near-
    # duplicate rows, but picking only ONE of them by ranking heuristic
    # alone can guess wrong (e.g. an earlier-dated release in an unwanted
    # country beats the intended worldwide/home-country pressing on a date
    # tie). The best-ranked release per group becomes that group's picker
    # row; the rest ride along as `alternates` so a picker UI can offer
    # them as a same-row variant dropdown instead of silently discarding
    # them.
    groups: dict[str, list[dict[str, Any]]] = {}
    for r in releases_by_id.values():
        group_id = (r.get("release-group") or {}).get("id") or r["id"]
        groups.setdefault(group_id, []).append(r)
    for releases in groups.values():
        releases.sort(key=_rank_key)

    representative_groups = sorted(groups.values(), key=lambda releases: _rank_key(releases[0]))[
        :limit
    ]

    # Label distinct recordings among the final candidates -- e.g. "Ain't
    # Nobody Here but Us Chickens" by Louis Jordan has a 1946 original and a
    # genuinely different mid-1950s re-recording, both released under
    # matching titles/artist credits. Without this, two such candidates
    # would look like just two dated editions of the same performance, and
    # a user could pick one thinking it's "the classic version" without
    # realizing a same-titled but musically different recording exists.
    # Only labeled when 2+ distinct recordings actually appear in this
    # specific result set -- the common case (one recording, many
    # pressings) stays unannotated. Keyed off each group's top-ranked
    # release only -- alternates within a group are, in practice, always
    # other pressings of that same recording.
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
        recording_labels = {
            rec_id: f"Recording {i + 1} of {len(ordered)}" for i, (rec_id, _) in enumerate(ordered)
        }

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
        # Country is always shown here (not just for non-preferred ones) --
        # this label is what distinguishes same-group alternates from one
        # another in a variant dropdown, so the one detail the user picks
        # by can't be the one detail that's sometimes hidden.
        detail_bits = [
            b for b in (release_type, r.get("date"), r.get("status"), r.get("country")) if b
        ]
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
