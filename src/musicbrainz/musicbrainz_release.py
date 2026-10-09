"""MusicBrainz release (album) search with canonical ranking, and the full per-pressing detail fetch."""

# Searches the `release` endpoint, not `release-group`, so a pick carries real per-pressing data.

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
import re
import time
from typing import Any

import musicbrainzngs

from src.foundation.logger_config import logger
from src.musicbrainz.musicbrainz_artist import MBAlias, _resolve_artist_mbid
from src.musicbrainz.musicbrainz_core import (
    MBCandidate,
    MusicBrainzLookupError,
    _and_query,
    _canonical_rank_parts,
    _ext_score,
    _is_blank,
    _mb_call,
    _parse_partial_date,
    _resolve_place_area,
    _to_float,
    _to_int,
    configure,
    resolve_area_chain,
)
from src.musicbrainz.musicbrainz_credits import (
    MBTrackCredit,
    _fetch_work_by_id,
    _parse_artist_credits,
    _parse_recording_artist_credit,
    _parse_release_artist_credit,
    _parse_work_credits,
    _recording_work_mbids,
)
from src.musicbrainz.musicbrainz_label import MBFounderRelation, MBLabelInfo, _fetch_label_by_id, _parse_label

# Re-exported: album and publisher code imports these data classes from this module.
__all__ = ["MBFounderRelation", "MBLabelInfo", "MBReleaseDetail", "MBReleaseTrack", "MBTrackCredit", "fetch_release_detail", "fetch_release_group_aliases", "search_canonical_releases"]

# ISO 639-2/B language codes -> the names album_language suggests (base_album_edit.py).
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

_SIDE_TRACK_NUMBER_RE = re.compile(r"^([A-Za-z])(\d+)$")

# Years a candidate may differ from expected_year and still rank as "on-hint". A ranking
# preference only: a stale hint must never hide the correct release (#366).
_YEAR_HINT_TOLERANCE = 20

# Maximum direct lookups one search makes to backfill dates the search index omitted;
# each lookup costs one rate-limited second.
_MAX_BACKFILL_LOOKUPS = 25

# The release is fetched in two calls: the core set has a roughly fixed size; the
# recording-relation set balloons on heavily credited releases, so a failure there
# degrades to "no per-track credits" instead of failing the whole fetch.
_RELEASE_CORE_INCLUDES = ["artist-credits", "recordings", "media", "labels", "release-groups", "url-rels", "artist-rels"]
_RELEASE_RECORDING_REL_INCLUDES = ["recordings", "recording-level-rels", "artist-rels", "work-rels", "place-rels"]

# Pause before the single end-of-pass retry of failed follow-up lookups.
_RETRY_PAUSE_SECONDS = 2.0


@dataclass
class MBReleaseTrack:
    """One track of a release, with its credits and recording location."""

    disc_number: int
    disc_title: str | None
    track_number: int | None  # side-relative on vinyl ("B1" -> 1)
    side: str | None
    title: str
    recording_mbid: str
    credits: list[MBTrackCredit] = field(default_factory=list)
    location_place_mbid: str | None = None
    # Position across both vinyl sides ("B1" after 7 A-side tracks -> 8); local tracks
    # are numbered absolutely, so match on this, not track_number.
    absolute_position: int | None = None


@dataclass
class MBReleaseDetail:
    """Every per-pressing detail fetch_release_detail imports for one release."""

    release_group_mbid: str | None
    mbid: str | None = None
    status: str | None = None
    # Secondary type ("Live", "Compilation") when present, else primary type ("Album").
    release_type: str | None = None
    language: str | None = None
    catalog_number: str | None = None
    release_country: str | None = None  # e.g. "US", "GB", "XW" (Worldwide)
    discogs_master_url: str | None = None
    barcode: str | None = None
    release_year: int | None = None
    release_month: int | None = None
    release_day: int | None = None
    credits: list[MBTrackCredit] = field(default_factory=list)  # release-level credits
    tracks: list[MBReleaseTrack] = field(default_factory=list)
    # place MBID -> [place, containing areas...]; entries are
    # {"mbid", "name", "type", "latitude", "longitude"}.
    place_chains: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    labels: list[MBLabelInfo] = field(default_factory=list)
    media_format: str | None = None  # e.g. "CD", "CD/DVD-Video"
    # Follow-up lookups that failed twice and were skipped; shown as a non-fatal notice.
    partial_failures: list[str] = field(default_factory=list)


def _media_format_str(medium_list: list[dict[str, Any]] | None) -> str | None:
    """Every distinct medium format, sorted and "/"-joined, or None when MB has none."""
    formats = sorted({m.get("format") for m in (medium_list or []) if m.get("format")})
    return "/".join(formats) if formats else None


def _parse_track_number_side(number: str | None, position: str | None) -> tuple[str | None, int | None]:
    """(side, track_number) from a vinyl-style number like "B2", else (None, position)."""
    if number:
        m = _SIDE_TRACK_NUMBER_RE.match(number.strip())
        if m:
            return m.group(1).upper(), int(m.group(2))
    return None, _to_int(position)


def _parse_recording_location(recording: dict[str, Any]) -> dict[str, Any] | None:
    """The recording's "recorded at" place as a flat dict, or None."""
    # The embedded place stub never carries its area; see _resolve_place_area (#248).
    for rel in recording.get("place-relation-list", []) or []:
        if rel.get("type") != "recorded at":
            continue
        place = rel.get("place") or {}
        if not place.get("id"):
            continue
        coords = place.get("coordinates") or {}
        return {
            "place_mbid": place["id"],
            "place_name": place.get("name") or "",
            "place_type": place.get("type"),
            "latitude": _to_float(coords.get("latitude")),
            "longitude": _to_float(coords.get("longitude")),
        }
    return None


def _backfill_release_details(r: dict[str, Any]) -> bool:
    """Fill a search hit's missing date (and medium-list) from a direct lookup; True if a lookup ran."""
    # The search index omits dates for some old releases, which would rank them last.
    # A missing medium-list alone only affects the label, so it does not cost a lookup.
    if r.get("date"):
        return False
    try:
        detail = musicbrainzngs.get_release_by_id(r["id"], includes=["media"])
    except Exception as e:
        logger.debug(f"Release backfill lookup failed for {r.get('id')}: {e}")
        return True
    release = detail.get("release", {})
    r["date"] = release.get("date")
    if not r.get("medium-list"):
        r["medium-list"] = release.get("medium-list")
    return True


def _release_type_str(release_group: dict[str, Any]) -> str | None:
    """Picker label type, e.g. "Album (Live, Compilation)"."""
    primary_type = release_group.get("primary-type") or release_group.get("type")
    secondary_types = release_group.get("secondary-type-list") or []
    if primary_type and secondary_types:
        return f"{primary_type} ({', '.join(secondary_types)})"
    if primary_type:
        return primary_type
    if secondary_types:
        return ", ".join(secondary_types)
    return None


def search_canonical_releases(album_name: str, artist_name: str | None = None, limit: int = 100, expected_year: int | None = None) -> list[MBCandidate]:
    """Search releases and rank the canonical pressing (official, earliest, preferred country) first."""
    if _is_blank(album_name):
        return []
    configure()
    fields: dict[str, Any] = {}
    if not _is_blank(artist_name):
        artist_mbid = _resolve_artist_mbid(artist_name)
        if artist_mbid:
            fields["arid"] = artist_mbid
        else:
            fields["artist"] = artist_name
    result = _mb_call(musicbrainzngs.search_releases, _and_query("release", album_name, fields), limit=limit)

    releases = [r for r in result.get("release-list", []) if r.get("id")]
    if not releases:
        return []

    # Only rank among releases within 10 points of MB's own top relevance score.
    top_score = max(_ext_score(r) for r in releases)
    candidates_pool = [r for r in releases if top_score - _ext_score(r) <= 10]

    lookups = 0
    for r in candidates_pool:
        if lookups >= _MAX_BACKFILL_LOOKUPS:
            break
        if _backfill_release_details(r):
            lookups += 1

    def _rank_key(r: dict[str, Any]):
        status_rank, date_key, country_rank = _canonical_rank_parts(r)
        # A missing expected_year or candidate year counts as on-hint.
        year = date_key[0] if date_key[0] != 9999 else None
        hint_rank = 0 if expected_year is None or year is None or abs(year - expected_year) <= _YEAR_HINT_TOLERANCE else 1
        media_count = len(r.get("medium-list", []) or [])
        return (status_rank, hint_rank, date_key, country_rank, media_count)

    candidates_pool.sort(key=_rank_key)

    candidates = []
    for r in candidates_pool:
        label_bits = [r.get("title", "?")]
        credit = r.get("artist-credit-phrase")
        if credit:
            label_bits.append(f"by {credit}")
        media_list = r.get("medium-list") or []
        type_str = _release_type_str(r.get("release-group") or {})

        track_total = sum(_to_int(m.get("track-count"), 0) for m in media_list)
        if not track_total:
            track_total = _to_int(r.get("medium-track-count"), 0)
        track_str = f"{track_total} track{'s' if track_total != 1 else ''}" if track_total else None

        detail_bits = [b for b in (type_str, track_str, r.get("status"), r.get("date"), r.get("country"), _media_format_str(media_list)) if b]
        if detail_bits:
            label_bits.append(f"[{' — '.join(detail_bits)}]")
        if r.get("disambiguation"):
            label_bits.append(f"[{r['disambiguation']}]")
        catalog = next((li.get("catalog-number") for li in (r.get("label-info-list") or []) if li.get("catalog-number")), None)
        if catalog:
            label_bits.append(f"({catalog})")

        candidates.append(MBCandidate(id=r["id"], label=" ".join(label_bits)))
    return candidates


def _get_release(release_mbid: str, includes: list[str]) -> dict[str, Any]:
    """One release lookup with the given includes; raises MusicBrainzLookupError."""
    return _mb_call(musicbrainzngs.get_release_by_id, release_mbid, includes=includes).get("release", {})


def _retry_deferred(deferred: list[tuple[str, Callable[[], None]]], status_fn: Callable[[str], None], pause: float) -> list[str]:
    """Retry each parked (description, redo) once after `pause`; return descriptions that failed again."""
    if not deferred:
        return []
    status_fn(f"Retrying {len(deferred)} lookup(s) that failed")
    if pause > 0:
        time.sleep(pause)
    still_failed: list[str] = []
    for describe, redo in deferred:
        try:
            redo()
        except MusicBrainzLookupError as e:
            logger.warning(f"{describe} (retry): {e}")
            still_failed.append(describe)
    return still_failed


def fetch_release_detail(
    release_mbid: str,
    progress_callback: Callable[[int, int], None] | None = None,
    status_callback: Callable[[str], None] | None = None,
    known_label_mbids: frozenset[str] = frozenset(),
    known_place_mbids: frozenset[str] = frozenset(),
    retry_pause: float = _RETRY_PAUSE_SECONDS,
) -> MBReleaseDetail:
    """Fetch every per-pressing detail of one release; follow-up failures go to partial_failures."""
    # known_label_mbids / known_place_mbids: MBIDs the local DB already has rows for
    # (album_musicbrainz_known_entities.py) -- their label/area lookups are skipped, since
    # the write layer matches on MBID and keeps its existing data.

    def _status(message: str) -> None:
        if status_callback:
            status_callback(message)

    progress_state = {"done": 0, "total": 0}

    def _tick() -> None:
        progress_state["done"] += 1
        if progress_callback:
            progress_callback(progress_state["done"], progress_state["total"])

    # --- Mandatory core call: scalars, tracklist skeleton, labels, credits.
    _status("Fetching release data")
    release = _get_release(release_mbid, _RELEASE_CORE_INCLUDES)

    catalog_number = next((li.get("catalog-number") for li in (release.get("label-info-list") or []) if li.get("catalog-number")), None)

    discogs_master_url = None
    for rel in release.get("url-relation-list", []) or []:
        if rel.get("type") == "discogs" and "/master/" in (rel.get("target") or ""):
            discogs_master_url = rel["target"]
            break

    language_code = (release.get("text-representation") or {}).get("language")
    date_parts = _parse_partial_date(release.get("date"), "release")

    release_group = release.get("release-group") or {}
    secondary_types = release_group.get("secondary-type-list") or []
    release_type = secondary_types[0] if secondary_types else release_group.get("primary-type")

    detail = MBReleaseDetail(
        release_group_mbid=release_group.get("id"),
        mbid=release.get("id") or release_mbid,
        status=release.get("status"),
        release_type=release_type,
        language=_MB_LANGUAGE_NAMES.get(language_code, language_code),
        catalog_number=catalog_number,
        release_country=release.get("country"),
        discogs_master_url=discogs_master_url,
        barcode=release.get("barcode"),
        release_year=date_parts.get("release_year"),
        release_month=date_parts.get("release_month"),
        release_day=date_parts.get("release_day"),
        credits=_parse_release_artist_credit(release) + _parse_artist_credits(release),
        media_format=_media_format_str(release.get("medium-list")),
    )

    # Tracklist skeleton; the relation call below merges in per-recording relations by recording MBID.
    tracks_by_recording: dict[str, list[MBReleaseTrack]] = {}
    for medium in release.get("medium-list", []) or []:
        disc_number = _to_int(medium.get("position"), 0)
        disc_title = medium.get("title")
        for track in medium.get("track-list", []) or []:
            recording = track.get("recording") or {}
            side, track_number = _parse_track_number_side(track.get("number"), track.get("position"))
            mb_track = MBReleaseTrack(
                disc_number=disc_number,
                disc_title=disc_title,
                track_number=track_number,
                side=side,
                title=recording.get("title") or track.get("title") or "",
                recording_mbid=recording.get("id") or "",
                credits=_parse_recording_artist_credit(recording),
                absolute_position=_to_int(track.get("position")),
            )
            detail.tracks.append(mb_track)
            rid = recording.get("id")
            if rid:
                tracks_by_recording.setdefault(rid, []).append(mb_track)

    # --- Optional relation call with one inline retry (it gates every work lookup);
    # a second failure downgrades to a scalar-only import.
    raw_locations: dict[str, dict[str, Any]] = {}  # place_mbid -> raw location dict
    track_work_mbids: list[tuple[MBReleaseTrack, list[str]]] = []
    _status("Fetching track relationships")
    rel_release: dict[str, Any] | None
    try:
        rel_release = _get_release(release_mbid, _RELEASE_RECORDING_REL_INCLUDES)
    except MusicBrainzLookupError as first_err:
        logger.warning(f"Track-relationship fetch failed for release {release_mbid}: {first_err}")
        _status("Retrying track relationships")
        if retry_pause > 0:
            time.sleep(retry_pause)
        try:
            rel_release = _get_release(release_mbid, _RELEASE_RECORDING_REL_INCLUDES)
        except MusicBrainzLookupError as retry_err:
            logger.warning(f"Track-relationship retry failed for release {release_mbid}: {retry_err}")
            rel_release = None
            detail.partial_failures.append("track relationships (performers, writers, recording locations)")

    if rel_release is not None:
        for medium in rel_release.get("medium-list", []) or []:
            for track in medium.get("track-list", []) or []:
                rec = track.get("recording") or {}
                targets = tracks_by_recording.get(rec.get("id") or "", [])
                if not targets:
                    continue
                extra_credits = _parse_artist_credits(rec)
                location = _parse_recording_location(rec)
                work_mbids = _recording_work_mbids(rec)
                for mb_track in targets:
                    if extra_credits:
                        mb_track.credits.extend(extra_credits)
                    if location:
                        mb_track.location_place_mbid = location["place_mbid"]
                        raw_locations[location["place_mbid"]] = location
                    if work_mbids:
                        track_work_mbids.append((mb_track, work_mbids))

    # Unique works, in first-seen order.
    raw_work_credits: dict[str, list[MBTrackCredit]] = {}
    unique_work_mbids: list[str] = list(dict.fromkeys(wm for _mb_track, work_mbids in track_work_mbids for wm in work_mbids))

    # Unique labels needing a full lookup; a label already on file stays a bare stub.
    raw_labels: dict[str, tuple[MBLabelInfo, str | None]] = {}  # label_mbid -> (info, area_mbid)
    labels_to_fetch: list[tuple[str, str | None]] = []  # (label_mbid, catalog_number)
    seen_label_mbids: set[str] = set()
    for li in release.get("label-info-list", []) or []:
        label_stub = li.get("label") or {}
        label_mbid = label_stub.get("id")
        if not label_mbid or label_mbid in seen_label_mbids:
            continue
        seen_label_mbids.add(label_mbid)
        if label_mbid in known_label_mbids:
            raw_labels[label_mbid] = (MBLabelInfo(mbid=label_mbid, name=label_stub.get("name") or "", catalog_number=li.get("catalog-number")), None)
            continue
        labels_to_fetch.append((label_mbid, li.get("catalog-number")))

    places_to_resolve = [pm for pm in raw_locations if pm not in known_place_mbids]
    for place_mbid in known_place_mbids & raw_locations.keys():
        # Already on file with its ancestry; resolve_place_chain matches on MBID.
        raw_locations[place_mbid]["area_mbid"] = None
        raw_locations[place_mbid]["area_name"] = None

    progress_state["total"] = len(unique_work_mbids) + len(labels_to_fetch) + len(places_to_resolve)
    if progress_callback:
        progress_callback(0, progress_state["total"])

    # --- Pass 1: per-entity follow-ups; failures are parked and retried once at the end.
    deferred: list[tuple[str, Callable[[], None]]] = []

    for i, work_mbid in enumerate(unique_work_mbids, start=1):
        _status(f"Resolving writing credits ({i} of {len(unique_work_mbids)})")

        def _do_work(wm: str = work_mbid) -> None:
            raw_work_credits[wm] = _parse_work_credits(_fetch_work_by_id(wm))

        try:
            _do_work()
        except MusicBrainzLookupError as e:
            logger.warning(f"Could not resolve work {work_mbid}: {e}")
            deferred.append((f"writing credits for work {work_mbid}", _do_work))
        _tick()

    for i, (label_mbid, catalog) in enumerate(labels_to_fetch, start=1):
        _status(f"Resolving record label ({i} of {len(labels_to_fetch)})")

        def _do_label(lm: str = label_mbid, cat: str | None = catalog) -> None:
            raw_labels[lm] = _parse_label(lm, cat, _fetch_label_by_id(lm))

        try:
            _do_label()
        except MusicBrainzLookupError as e:
            logger.warning(f"Could not resolve label {label_mbid}: {e}")
            deferred.append((f"record label {label_mbid}", _do_label))
        _tick()

    # _resolve_place_area is already best-effort, so it is a progress step, not a retry unit.
    for i, place_mbid in enumerate(places_to_resolve, start=1):
        _status(f"Resolving recording location ({i} of {len(places_to_resolve)})")
        area_mbid, area_name = _resolve_place_area(place_mbid)
        raw_locations[place_mbid]["area_mbid"] = area_mbid
        raw_locations[place_mbid]["area_name"] = area_name
        _tick()

    detail.partial_failures.extend(_retry_deferred(deferred, _status, retry_pause))

    for mb_track, work_mbids in track_work_mbids:
        for work_mbid in work_mbids:
            mb_track.credits.extend(raw_work_credits.get(work_mbid, []))

    # --- Pass 2: area hierarchy walks, same collect-then-retry-once contract.
    pending_areas: dict[str, None] = {}  # ordered set of area MBIDs to resolve
    for location in raw_locations.values():
        if location.get("area_mbid"):
            pending_areas.setdefault(location["area_mbid"], None)
    for _label_info, area_mbid in raw_labels.values():
        if area_mbid:
            pending_areas.setdefault(area_mbid, None)

    area_cache: dict[str, list[dict[str, Any]]] = {}
    area_deferred: list[tuple[str, Callable[[], None]]] = []
    area_list = list(pending_areas)
    for idx, area_mbid in enumerate(area_list, start=1):
        _status(f"Resolving location hierarchy ({idx} of {len(area_list)})")
        if area_mbid in known_place_mbids:
            # Already on file locally with its own ancestry.
            area_cache[area_mbid] = [{"mbid": area_mbid, "name": None, "type": None, "latitude": None, "longitude": None}]
            continue

        def _do_area(am: str = area_mbid) -> None:
            resolve_area_chain(am, area_cache)

        try:
            _do_area()
        except MusicBrainzLookupError as e:
            logger.warning(f"Could not resolve area chain {area_mbid}: {e}")
            area_deferred.append((f"location hierarchy for area {area_mbid}", _do_area))
    detail.partial_failures.extend(_retry_deferred(area_deferred, _status, retry_pause))

    for place_mbid, location in raw_locations.items():
        place_node = {"mbid": place_mbid, "name": location["place_name"], "type": location.get("place_type"), "latitude": location.get("latitude"), "longitude": location.get("longitude")}
        area_chain = area_cache.get(location.get("area_mbid"), []) if location.get("area_mbid") else []
        detail.place_chains[place_mbid] = [place_node, *area_chain]

    for label_info, area_mbid in raw_labels.values():
        label_info.area_chain = area_cache.get(area_mbid, []) if area_mbid else []
        detail.labels.append(label_info)

    return detail


def fetch_release_group_aliases(release_group_mbid: str) -> list[MBAlias]:
    """Alternate album titles from the release-group; raises MusicBrainzLookupError."""
    result = _mb_call(musicbrainzngs.get_release_group_by_id, release_group_mbid, includes=["aliases"])

    aliases = []
    for al in result.get("release-group", {}).get("alias-list", []) or []:
        name = al.get("alias")
        if not name:
            continue
        aliases.append(MBAlias(name=name, type=al.get("type") or ""))
    return aliases
