"""
musicbrainz_release.py

Release (album) search and detail fetch.

Searches the `release` endpoint directly -- not `release-group` -- so a
single pick carries real per-pressing data (status, language/script,
catalog number, barcode, credits, recording locations) instead of the
release-group's abstract aggregate. A release-group can have 100+ member
releases (regional pressings, reissues, box sets), so search results are
ranked to put the *canonical* release first: official status, earliest
date, a fixed country preference, fewest media. The picker dialog still
shows the ranked list and lets the user override the top pick.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import musicbrainzngs

from src.core.logger_config import logger
from src.musicbrainz.musicbrainz_artist import MBAlias, search_artists
from src.musicbrainz.musicbrainz_core import (
    MBCandidate,
    MusicBrainzLookupError,
    _escape_lucene,
    _ext_score,
    _parse_partial_date,
    _query_term,
    _resolve_place_area,
    _to_float,
    configure,
    resolve_area_chain,
)

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
_PERFORMER_RELATION_TYPES = {"performer", "vocal", "instrument", "performing orchestra"}
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
    "sound",
    "recording",
}
_CREDIT_RELATION_TYPES = _PERFORMER_RELATION_TYPES | _PRODUCTION_RELATION_TYPES

# Display name for a production relation type when its attribute-list is
# empty (or supplies only a qualifier). Defaults to rel_type.title(), but a
# handful of MB relation types have an internal `type` slug that differs
# from the noun MB itself uses for the credit -- confirmed against MB's
# relationship-type docs (reverse link phrase is the credited noun): "sound"
# credits as "sound engineer" (#326), "mix" credits as "mixer", and
# "recording" credits as "recording engineer" -- title-casing the type name
# directly is only safe for the types where the slug and the credited noun
# happen to match (producer, engineer, mastering, arranger, orchestrator,
# conductor, programming, remixer).
_PRODUCTION_RELATION_DISPLAY_NAMES = {
    "sound": "Sound Engineer",
    "mix": "Mixer",
    "recording": "Recording Engineer",
}

# Boolean-style qualifier words MB allows on performer/instrument/vocal
# relations alongside (not instead of) the actual instrument/vocal name --
# MB's own link-phrase template for these relations is literally
# "{additional} {guest} {solo} {instrument}", so attribute-list can contain
# a qualifier *before* the real value, and a single relation can carry more
# than one instrument/vocal value (e.g. one person credited for both piano
# and organ). Confirmed against the live API: Eagles' "Hotel California"
# (release 76df3287-6cda-33eb-8e9e-f5b0f0625372) credits Bill Armstrong's
# trumpet as attribute-list ["additional", "trumpet"] -- naively taking
# attribute-list[0] reports his role as "Additional" and drops "trumpet"
# entirely.
_PERFORMER_ATTRIBUTE_QUALIFIERS = {"additional", "guest", "solo"}

_SIDE_TRACK_NUMBER_RE = re.compile(r"^([A-Za-z])(\d+)$")

# Work-level artist-relation types this feature imports as a writing credit
# (composer/lyricist/writer/...). Confirmed against the live API
# (get_work_by_id responses for e.g. Queen's "Bohemian Rhapsody" and several
# "My Way" works): unlike some recording/release relation types (see
# _PRODUCTION_RELATION_DISPLAY_NAMES), every one of these title-cases
# cleanly to its credited noun with no attribute-list involved, e.g.
# "composer" -> "Composer", "instrument arranger" -> "Instrument Arranger".
# Deliberately excludes other relation types also seen on works that are not
# writing credits: "previous attribution" (a superseded historical credit,
# not the current one) and "dedication" (the work's dedicatee, not a
# writer).
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
class MBFounderRelation:
    """One 'founder' artist-relation on a label."""

    mbid: str
    name: str


@dataclass
class MBLabelInfo:
    """One label (record company/publisher) attached to a release via its
    label-info-list, enriched with a follow-up get_label_by_id lookup (see
    _fetch_label_by_id/_parse_label) for life-span, annotation, headquarters
    area, and founder relations -- none of which the release response's
    embedded label stub carries."""

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
    # Full containing chain for the label's headquarters area (immediate
    # area first, then its parents up to the outermost country) -- see
    # resolve_area_chain. Each entry is
    # {"mbid", "name", "type", "latitude", "longitude"}.
    area_chain: list[dict[str, Any]] = field(default_factory=list)
    founders: list[MBFounderRelation] = field(default_factory=list)


@dataclass
class MBReleaseDetail:
    release_group_mbid: str | None
    mbid: str | None = None
    status: str | None = None
    # The release-group's secondary type (e.g. "Live", "Compilation",
    # "Soundtrack") if it has one, else its primary type (e.g. "Album",
    # "Single", "EP") -- a release-group always has exactly one primary
    # type but zero or more secondary types, and the secondary type is
    # what users actually mean by "release type" for e.g. a live album
    # whose primary type is still plain "Album".
    release_type: str | None = None
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
    # Every unique label attached to this release via label-info-list.
    labels: list[MBLabelInfo] = field(default_factory=list)


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
    """One credit's Role name for an artist-relation. For performer
    relations (instrument/vocal), the attribute alone is the full role
    (e.g. "Piano", "Lead Vocals") -- the generic type adds nothing. For
    production relations (producer/engineer/mix/...), the attribute is a
    *modifier* of the type, not a replacement for it -- MB credits like
    "assistant engineer" or "mastering engineer" are the `engineer` type
    with an "assistant"/"mastering" attribute, so both must be combined or
    the type name is silently dropped."""
    rel_type = rel.get("type")
    attributes = rel.get("attribute-list") or []
    if rel_type in _PRODUCTION_RELATION_TYPES:
        base_name = _PRODUCTION_RELATION_DISPLAY_NAMES.get(rel_type, rel_type.title() if rel_type else None)
        if attributes:
            return f"{attributes[0].title()} {base_name}"
        return base_name
    # Performer/instrument/vocal: split out qualifier words ("additional"/
    # "guest"/"solo") from the actual instrument/vocal value(s) rather than
    # assuming attribute-list[0] is the value -- see
    # _PERFORMER_ATTRIBUTE_QUALIFIERS.
    qualifiers = [a for a in attributes if a.lower() in _PERFORMER_ATTRIBUTE_QUALIFIERS]
    values = [a for a in attributes if a.lower() not in _PERFORMER_ATTRIBUTE_QUALIFIERS]
    if values:
        name = " & ".join(v.title() for v in values)
        if qualifiers:
            name = f"{' '.join(q.title() for q in qualifiers)} {name}"
        return name
    if qualifiers:
        return " ".join(q.title() for q in qualifiers)
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


def _parse_release_artist_credit(release: dict[str, Any]) -> list[MBTrackCredit]:
    """Parse the release's `artist-credit` -- the actual "Album Artist"
    byline (e.g. "Uncle Tupelo"), structurally distinct from
    `artist-relation-list` (producer/engineer/performer-type credits
    handled by _parse_artist_credits). musicbrainzngs interleaves each
    named artist with a bare joinphrase string (e.g. " & ", " feat. ");
    only the dict entries are real credits."""
    credits = []
    for entry in release.get("artist-credit", []) or []:
        if not isinstance(entry, dict):
            continue
        artist = entry.get("artist") or {}
        if not artist.get("id"):
            continue
        credits.append(
            MBTrackCredit(
                artist_mbid=artist["id"],
                artist_name=entry.get("name") or artist.get("name") or "",
                role_name="Album Artist",
                canonical_name=artist.get("name") or "",
            )
        )
    return credits


def _parse_recording_artist_credit(recording: dict[str, Any]) -> list[MBTrackCredit]:
    """Parse a recording's own `artist-credit` -- the track's "Primary
    Artist" byline, which is usually the same as the release's Album
    Artist but can diverge (e.g. a compilation track, or a feature credited
    at the recording level but not the release level). Same dict shape as
    _parse_release_artist_credit, just read off the embedded recording
    instead of the release."""
    credits = []
    for entry in recording.get("artist-credit", []) or []:
        if not isinstance(entry, dict):
            continue
        artist = entry.get("artist") or {}
        if not artist.get("id"):
            continue
        credits.append(
            MBTrackCredit(
                artist_mbid=artist["id"],
                artist_name=entry.get("name") or artist.get("name") or "",
                role_name="Primary Artist",
                canonical_name=artist.get("name") or "",
            )
        )
    return credits


def _recording_work_mbids(recording: dict[str, Any]) -> list[str]:
    """The MBID(s) of the work(s) a recording is a performance of, via its
    work-relation-list. The embedded work stub here carries only
    id/title/type/language (confirmed against the live API) -- not the
    work's own artist-relation-list, where composer/lyricist/writer/...
    credits actually live -- so each unique work needs its own
    get_work_by_id follow-up (_fetch_work_by_id) to resolve those, same
    reasoning as recording locations (_parse_recording_location) and labels
    (_fetch_label_by_id) needing their own direct lookups."""
    mbids = []
    for rel in recording.get("work-relation-list", []) or []:
        if rel.get("type") != "performance":
            continue
        work = rel.get("work") or {}
        if work.get("id"):
            mbids.append(work["id"])
    return mbids


def _fetch_work_by_id(work_mbid: str) -> dict[str, Any]:
    """Raw get_work_by_id call for a work referenced by a recording's
    work-relation-list -- the release response's embedded work stub is
    missing the artist-relation-list this feature needs for writing
    credits, so a direct per-work follow-up lookup is needed (same
    reasoning as _fetch_label_by_id for release labels). Raises
    MusicBrainzLookupError on failure; callers treat this as best-effort,
    same as every other follow-up lookup in this module."""
    configure()
    try:
        result = musicbrainzngs.get_work_by_id(work_mbid, includes=["artist-rels"])
    except Exception as e:
        # Intentional broad boundary catch: musicbrainzngs has no single
        # exception hierarchy covering every failure mode it can raise
        # (network errors, XML parse errors, HTTP errors, auth/rate-limit
        # errors) — wrap all of them into this module's MusicBrainzLookupError
        # so every caller elsewhere in the codebase only ever has to catch
        # one type.
        raise MusicBrainzLookupError(str(e)) from e
    return result.get("work", {})


def _parse_work_credits(work: dict[str, Any]) -> list[MBTrackCredit]:
    """Parse composer/lyricist/writer/... credits from a work's own
    artist-relation-list (see _fetch_work_by_id) -- distinct from
    _parse_artist_credits, which reads the same-shaped relation list off a
    recording or release instead."""
    credits = []
    for rel in work.get("artist-relation-list", []) or []:
        rel_type = rel.get("type")
        if rel_type not in _WORK_RELATION_TYPES:
            continue
        artist = rel.get("artist") or {}
        if not artist.get("id"):
            continue
        credits.append(
            MBTrackCredit(
                artist_mbid=artist["id"],
                artist_name=rel.get("target-credit") or artist.get("name") or "",
                role_name=rel_type.title(),
                canonical_name=artist.get("name") or "",
            )
        )
    return credits


# MusicBrainz titles inconsistently use curly vs. straight quotes for the
# same contraction across different entries of the same era/song (e.g.
# "Ain't" vs "Ain’t") -- normalizing both sides of an exact-title match
# avoids silently missing genuine matches (including, in one real case
# found while building this feature, the actual earliest/canonical release)
# purely because of which quote character a cataloger happened to type.
_QUOTE_NORMALIZE = str.maketrans({
    "‘": "'", "’": "'", "‚": "'", "‛": "'",
    "“": '"', "”": '"', "„": '"', "‟": '"',
})


def _normalize_title(title: str) -> str:
    return title.strip().translate(_QUOTE_NORMALIZE).lower()


def _matching_track_recording_id(release: dict[str, Any], title_key: str) -> str | None:
    """The recording MBID of the track on `release` with the exact given
    title (case/quote-insensitive, not substring -- "In the Mood" must not
    match "In the Mood for Love", a different song entirely), or None if no
    track on this release matches."""
    for medium in release.get("medium-list", []) or []:
        for track in medium.get("track-list", []) or []:
            recording = track.get("recording", {})
            title = recording.get("title") or track.get("title") or ""
            if _normalize_title(title) == title_key:
                return recording.get("id")
    return None


def _parse_recording_location(recording: dict[str, Any]) -> dict[str, Any] | None:
    """Extract the "recorded at" place relation on a recording, if any, as a
    flat dict -- id/name/type/coordinates only. MusicBrainz embeds a reduced
    place stub on this relation that never carries the place's containing
    area (confirmed against the live API for bug #248), so the area has to
    be looked up separately via _resolve_place_area."""
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


def _fetch_label_by_id(label_mbid: str) -> dict[str, Any]:
    """Raw get_label_by_id call for a release's label-info-list entry --
    the release response's embedded label is only a stub (name, sort-name,
    life-span, area, disambiguation), missing the annotation and founder
    relations this feature also imports, so a direct per-label follow-up
    lookup is needed (same reasoning as _resolve_place_area for recording
    locations). Raises MusicBrainzLookupError on failure; callers treat
    this as best-effort, same as every other follow-up lookup in this
    module."""
    configure()
    try:
        result = musicbrainzngs.get_label_by_id(
            label_mbid, includes=["annotation", "artist-rels"]
        )
    except Exception as e:
        # Intentional broad boundary catch: musicbrainzngs has no single
        # exception hierarchy covering every failure mode it can raise
        # (network errors, XML parse errors, HTTP errors, auth/rate-limit
        # errors) — wrap all of them into this module's MusicBrainzLookupError
        # so every caller elsewhere in the codebase only ever has to catch
        # one type.
        raise MusicBrainzLookupError(str(e)) from e
    return result.get("label", {})


def _parse_label(
    label_mbid: str, catalog_number: str | None, label: dict[str, Any]
) -> tuple[MBLabelInfo, str | None]:
    """Pure parsing of a full get_label_by_id response into
    (MBLabelInfo, headquarters_area_mbid) -- no network calls (the area
    mbid is resolved into its full parent chain separately, by the caller,
    via resolve_area_chain, same split as _apply_full_artist vs.
    _resolve_artist_place_chains)."""
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


def _resolve_primary_artist_mbids(artist_name: str) -> list[str]:
    """Resolve every MusicBrainz artist MBID plausibly representing the same
    performer as `artist_name`, not just the single best name match.

    MusicBrainz often models a performer's original-era ensemble credit as a
    distinct artist entity from a later "solo" umbrella credit used for
    reissues/compilations -- e.g. "Glenn Miller" (person) vs. "Glenn Miller
    and His Orchestra" (the actual credit on the original-era releases,
    explicitly disambiguated on MusicBrainz as "use only for releases Glenn
    Miller performed with"). Resolving only the solo entity would silently
    exclude every original-era release credited to the ensemble -- exactly
    the releases a "canonical first release" search most needs to find.

    Common names collide, though -- a plain "Glenn Miller" artist search
    also returns several entirely unrelated people who happen to share that
    exact name (an accordion player, a mastering engineer, a Chilliwack
    band member...), all tied at nearly the same relevance score. Including
    every same-named entity would flood the recording search with noise
    (and multiply API calls) for zero benefit, so only two kinds of hit
    qualify: MusicBrainz's own top-ranked match for the name (the "main"
    entity a plain-text search is presumably after), and any other hit
    whose name is a strict superset containing the query -- e.g. "Glenn
    Miller and His Orchestra", "Glenn Miller Orchestra" -- which catches the
    ensemble-credit pattern without matching an unrelated same-named person
    (their name is identical to the query, not a superset of it).
    """
    try:
        result = musicbrainzngs.search_artists(_escape_lucene(artist_name), limit=10)
    except Exception as e:
        raise MusicBrainzLookupError(str(e)) from e

    artists = result.get("artist-list", [])
    if not artists:
        return []

    query = artist_name.lower()
    mbids = [artists[0]["id"]]
    for a in artists[1:]:
        name = (a.get("name") or "").lower()
        if name != query and query in name:
            mbids.append(a["id"])
    return mbids


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


def _backfill_release_details(r: dict[str, Any]) -> None:
    """MusicBrainz's search index sometimes omits `date` and `medium-list`
    for older, sparsely-indexed releases even though a direct lookup of the
    same release MBID returns them correctly (e.g. a 1953 original 12"
    pressing indexed without a release-event date). Ranking on a missing
    date would otherwise treat it as if released in the far future, burying
    the original pressing behind well-indexed reissues -- so fetch the real
    values with a direct lookup whenever the search result is missing them."""
    if r.get("date") and r.get("medium-list"):
        return
    try:
        detail = musicbrainzngs.get_release_by_id(r["id"], includes=["media"])
    except Exception:  # ruff: ignore[blind-except]
        # Best-effort enrichment -- fall back to whatever the search result
        # already had (possibly still missing) rather than failing the
        # whole search over one release's lookup.
        return
    release = detail.get("release", {})
    if not r.get("date"):
        r["date"] = release.get("date")
    if not r.get("medium-list"):
        r["medium-list"] = release.get("medium-list")


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

    top_score = max(_ext_score(r) for r in releases)
    # Keep everything within 10 points of MB's own top relevance score --
    # the canonical-ranking heuristic below only needs to choose among
    # releases MB itself considers close matches, not the whole result set.
    candidates_pool = [r for r in releases if top_score - _ext_score(r) <= 10]

    for r in candidates_pool:
        _backfill_release_details(r)

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
        media_formats = sorted({
            m.get("format") for m in (r.get("medium-list") or []) if m.get("format")
        })
        format_str = "/".join(media_formats) if media_formats else None
        detail_bits = [b for b in (status, date, country, format_str) if b]
        if detail_bits:
            label_bits.append(f"[{' — '.join(detail_bits)}]")
        if r.get("disambiguation"):
            label_bits.append(f"[{r['disambiguation']}]")
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

    release_group = release.get("release-group") or {}
    secondary_types = release_group.get("secondary-type-list") or []
    release_type = (
        secondary_types[0] if secondary_types else release_group.get("primary-type")
    )

    detail = MBReleaseDetail(
        release_group_mbid=release_group.get("id"),
        mbid=release.get("id") or release_mbid,
        status=release.get("status"),
        release_type=release_type,
        language=_MB_LANGUAGE_NAMES.get(language_code, language_code),
        catalog_number=catalog_number,
        discogs_master_url=discogs_master_url,
        barcode=release.get("barcode"),
        release_year=date_parts.get("release_year"),
        release_month=date_parts.get("release_month"),
        release_day=date_parts.get("release_day"),
        credits=_parse_release_artist_credit(release) + _parse_artist_credits(release),
    )

    # First pass: parse every track, collecting each unique recording-location
    # place, before resolving any of their areas -- this is what lets
    # progress_callback report a real total up front instead of an
    # ever-growing one.
    raw_locations: dict[str, dict[str, Any]] = {}  # place_mbid -> raw location dict
    # (mb_track, [work_mbid, ...]) for every track whose recording is a
    # performance of at least one work -- resolved into writing credits
    # below, once per unique work, same dedup approach as labels/locations.
    track_work_mbids: list[tuple[MBReleaseTrack, list[str]]] = []

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
                credits=_parse_recording_artist_credit(recording)
                + _parse_artist_credits(recording),
                absolute_position=absolute_position,
            )
            location = _parse_recording_location(recording)
            if location:
                mb_track.location_place_mbid = location["place_mbid"]
                raw_locations[location["place_mbid"]] = location
            work_mbids = _recording_work_mbids(recording)
            if work_mbids:
                track_work_mbids.append((mb_track, work_mbids))
            detail.tracks.append(mb_track)

    # Each unique work referenced above needs its own direct lookup (see
    # _fetch_work_by_id) -- the release response's embedded work stub is
    # missing the artist-relation-list where composer/lyricist/writer/...
    # credits live. Done once per unique work mbid, same dedup approach as
    # labels below (a work can be the target of more than one recording on
    # the same release, e.g. a reprise or alternate take).
    raw_work_credits: dict[str, list[MBTrackCredit]] = {}
    for _mb_track, work_mbids in track_work_mbids:
        for work_mbid in work_mbids:
            if work_mbid in raw_work_credits:
                continue
            try:
                full_work = _fetch_work_by_id(work_mbid)
            except MusicBrainzLookupError as e:
                logger.warning(f"Could not resolve work {work_mbid}: {e}")
                continue
            raw_work_credits[work_mbid] = _parse_work_credits(full_work)
    for mb_track, work_mbids in track_work_mbids:
        for work_mbid in work_mbids:
            mb_track.credits.extend(raw_work_credits.get(work_mbid, []))

    # Each unique label attached to this release needs its own direct
    # lookup (see _fetch_label_by_id) -- the release response's embedded
    # label is only a stub, missing annotation/founder relations. Done
    # once per unique label mbid, same dedup approach as recording
    # locations below (a release can list the same label against several
    # tracks/media, e.g. one catalog number per format).
    raw_labels: dict[
        str, tuple[MBLabelInfo, str | None]
    ] = {}  # label_mbid -> (info, area_mbid)
    for li in release.get("label-info-list", []) or []:
        label_stub = li.get("label") or {}
        label_mbid = label_stub.get("id")
        if not label_mbid or label_mbid in raw_labels:
            continue
        try:
            full_label = _fetch_label_by_id(label_mbid)
        except MusicBrainzLookupError as e:
            logger.warning(f"Could not resolve label {label_mbid}: {e}")
            continue
        raw_labels[label_mbid] = _parse_label(
            label_mbid, li.get("catalog-number"), full_label
        )

    # Each unique place needs its own direct lookup to find its containing
    # area (see _resolve_place_area) before that area's parent chain can be
    # walked -- done once per unique place, same as area chains are only
    # ever walked once per unique area below.
    pending_areas: dict[str, None] = {}  # ordered set of area MBIDs to resolve
    for place_mbid, location in raw_locations.items():
        area_mbid, area_name = _resolve_place_area(place_mbid)
        location["area_mbid"] = area_mbid
        location["area_name"] = area_name
        if area_mbid:
            pending_areas.setdefault(area_mbid, None)
    for _label_info, area_mbid in raw_labels.values():
        if area_mbid:
            pending_areas.setdefault(area_mbid, None)

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

    for label_info, area_mbid in raw_labels.values():
        label_info.area_chain = area_cache.get(area_mbid, []) if area_mbid else []
        detail.labels.append(label_info)

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
