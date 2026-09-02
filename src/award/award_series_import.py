"""
award_series_import.py

Reverse-lookup awards importer: for every Track/Album/Artist row already
matched to MusicBrainz, asks MusicBrainz what award series *that entity*
belongs to (`series-rels` is a valid include on the recording, release-group,
and artist lookup endpoints alike -- confirmed live), rather than enumerating
MusicBrainz's entire global series catalog and trying to match nominees back
to the library. This inverts the cost: total API calls scale with the
library's own MB-matched entity count, not MusicBrainz's catalog size, and
there's no "which award shows to support" list to maintain -- whatever a
given entity is actually linked to on MusicBrainz just shows up.

MusicBrainz's `series` entity type is heavily overloaded beyond awards --
confirmed live, one MB-matched recording came back with 4 series relations
and only 1 was a genuine award (the other 3: a Billboard year-end chart, a
streaming-milestone club, and a Spotify playlist series, all typed as
"series" or even "award" despite not being competitive awards). _is_award
below is the local, per-relation classification filter that keeps the
false-positive rate low: allowed MB series type AND an award-ish keyword in
the series name. Best-effort, not exhaustive -- a genuine award show whose
name matches none of the keywords would still be filtered out.

Two entry points, not one:
  - import_awards_for_entity(): looks up award series for a single already-
    MB-matched entity. Called live, inline, from the same write sites that
    already set Track.MBID / Album.MBID+release_group_MBID / Artist.MBID
    (album_musicbrainz_mixin.py, track_edit_album.py,
    album_musicbrainz_review_import.py) -- awards data rides along with an
    MB match the user was already making, at zero marginal API cost beyond
    one extra include. There is deliberately no standalone "check every
    entity in the library" action in the running app.
  - sync_awards(): walks a batch of entities calling the above for each.
    Used only by scripts/backfill_awards_import.py, a one-time script for
    entities matched before this feature existed -- not wired to any UI.

Match-only, no staging table: this writes directly into the existing
Award/AwardAssociation tables. There is no "unmatched nominee" concept in
this direction -- every relation considered already belongs to a real local
entity by construction.
"""

from collections.abc import Callable
from dataclasses import dataclass
import re

import musicbrainzngs
from sqlalchemy import select

from src.db.db_tables.album import Album
from src.db.db_tables.artist import Artist
from src.db.db_tables.award import Award, AwardAssociation
from src.db.db_tables.track import Track
from src.foundation.logger_config import logger
from src.musicbrainz.musicbrainz_core import configure

# MB series types that can appear on the recording/release-group/artist
# lookup endpoints used here (Work-typed series link to Work entities, which
# don't have an endpoint queried in this module, so they can never appear --
# no separate exclusion needed).
_ALLOWED_SERIES_TYPES = {
    "Recording award",
    "Recording series",
    "Release group award",
    "Release group series",
    "Artist award",
    "Artist series",
}

# Substring, case-insensitive. Deliberately small and easy to extend --
# see module docstring for why a pure type filter isn't enough on its own.
_AWARD_NAME_KEYWORDS = ("award", "prize", "honor", "honour", "hall of fame")

_NUMBER_ATTR_RE = re.compile(r"^\s*(\d{4})\s*(winner)?\s*$", re.IGNORECASE)

# entity_type -> (MB lookup function name, top-level response key). Looked
# up via getattr() at call time (not bound directly at import time) so
# tests can patch musicbrainzngs.get_*_by_id and have it take effect.
_MB_LOOKUP = {
    "Track": ("get_recording_by_id", "recording"),
    "Album": ("get_release_group_by_id", "release-group"),
    "Artist": ("get_artist_by_id", "artist"),
}


@dataclass
class AwardSyncStats:
    entities_checked: int
    awards_created: int
    associations_created: int
    lookup_failures: int


@dataclass
class EntityAwardResult:
    awards_created: int
    associations_created: int
    lookup_failed: bool


def _is_award_series(series: dict) -> bool:
    series_type = series.get("type") or ""
    if series_type not in _ALLOWED_SERIES_TYPES:
        return False
    name = (series.get("name") or "").lower()
    return any(keyword in name for keyword in _AWARD_NAME_KEYWORDS)


def _parse_number_attribute(relation: dict) -> tuple | None:
    """Returns (year, is_winner) parsed from the relation's "number"
    attribute (e.g. "2023" or "2023 winner"), or None if the relation
    carries no such attribute or it's in an unrecognized format -- awards
    sync only ever records what MusicBrainz states plainly, never guesses."""
    for attr in relation.get("attributes") or []:
        if attr.get("attribute") != "number":
            continue
        m = _NUMBER_ATTR_RE.match(attr.get("value") or "")
        if not m:
            return None
        return int(m.group(1)), bool(m.group(2))
    return None


def _parse_series_name(name: str) -> tuple:
    """Splits an MB series name like "Grammy Award: Record of the Year
    nominees" into (award_name, award_category) = ("Grammy Award", "Record
    of the Year"). Falls back to the whole name as award_name with no
    category when there's no ":" separator -- best-effort labeling, not a
    strict parser."""
    if ":" in name:
        show, category = name.split(":", 1)
        show, category = show.strip(), category.strip()
        category = re.sub(r"\s+nominees$", "", category, flags=re.IGNORECASE)
        return show, category
    return name.strip(), None


def _find_or_create_award(session, series: dict, year: int | None) -> tuple:
    """Returns (award, created)."""
    award = session.scalar(
        select(Award).where(Award.mb_series_id == series["id"], Award.award_year == year)
    )
    if award is not None:
        return award, False

    award_name, award_category = _parse_series_name(series.get("name") or "")
    award = Award(
        award_name=award_name,
        award_category=award_category,
        award_year=year,
        mb_series_id=series["id"],
    )
    session.add(award)
    session.flush()
    return award, True


def _find_or_create_association(
    session, award: Award, entity_type: str, entity_id: int, target_mbid: str, is_winner: bool
) -> bool:
    """Returns True if a new AwardAssociation was created. Updates
    association_type in place if MusicBrainz's winner/nominee status for
    this pairing changed since a prior sync (e.g. a late correction),
    without creating a duplicate row."""
    association_type = "winner" if is_winner else "nominee"
    existing = session.scalar(
        select(AwardAssociation).where(
            AwardAssociation.award_id == award.award_id,
            AwardAssociation.entity_type == entity_type,
            AwardAssociation.entity_id == entity_id,
        )
    )
    if existing is not None:
        if existing.association_type != association_type:
            existing.association_type = association_type
        return False

    session.add(
        AwardAssociation(
            award_id=award.award_id,
            entity_type=entity_type,
            entity_id=entity_id,
            association_type=association_type,
            mb_target_mbid=target_mbid,
        )
    )
    return True


def _candidate_entities(session, first_pass_only: bool = False):
    """Yields (entity_type, entity_id, mbid) for every Track/Artist row with
    an MBID and every Album row with a release_group_MBID.

    `first_pass_only` restricts Album and Artist to reviewed rows
    (first_pass=1), matching the scope already used for the
    release_group_MBID backfill. Deliberately NOT applied to Track: in the
    live library, first_pass=1 AND MBID-set tracks number zero (confirmed
    against the real DB) -- Track's first_pass flag tracks a different,
    unrelated review workflow than its MB-matched state, so filtering by it
    here would silently skip every track.
    """
    for track_id, mbid in session.execute(
        select(Track.track_id, Track.MBID).where(Track.MBID.isnot(None), Track.MBID != "")
    ):
        yield "Track", track_id, mbid

    album_query = select(Album.album_id, Album.release_group_MBID).where(
        Album.release_group_MBID.isnot(None), Album.release_group_MBID != ""
    )
    if first_pass_only:
        album_query = album_query.where(Album.first_pass == 1)
    for album_id, mbid in session.execute(album_query):
        yield "Album", album_id, mbid

    artist_query = select(Artist.artist_id, Artist.MBID).where(
        Artist.MBID.isnot(None), Artist.MBID != ""
    )
    if first_pass_only:
        artist_query = artist_query.where(Artist.first_pass == 1)
    for artist_id, mbid in session.execute(artist_query):
        yield "Artist", artist_id, mbid


# Sentinel for import_awards_for_entity's `relations` kwarg: distinguishes
# "not supplied, fetch it inline" from an explicit None ("the caller's own
# fetch failed").
_UNSET = object()


def fetch_award_series_relations(entity_type: str, mbid: str) -> list | None:
    """Network-only half of import_awards_for_entity: fetch the raw
    series-relation-list for one already-MB-matched entity.

    Returns the relation list (possibly empty) on success, or None if the
    lookup failed (network, rate-limit, malformed response) -- same
    best-effort contract as import_awards_for_entity, just split out so a Qt
    caller can run this on a worker thread and hand the result to
    import_awards_for_entity(relations=...) instead of blocking the UI
    thread on it. musicbrainzngs retries a stuck request up to 8x at a 30s
    socket timeout each, so on the UI thread this can freeze the app for
    minutes with no progress or cancel.
    """
    configure()
    lookup_fn_name, response_key = _MB_LOOKUP[entity_type]
    try:
        result = getattr(musicbrainzngs, lookup_fn_name)(mbid, includes=["series-rels"])
    except Exception as e:
        # Deliberate broad boundary catch: musicbrainzngs has no single
        # exception hierarchy covering every failure mode (network,
        # rate-limit, malformed-response).
        logger.warning(f"Awards import: lookup failed for {entity_type} {mbid}: {e}")
        return None
    return (result.get(response_key) or {}).get("series-relation-list") or []


def import_awards_for_entity(
    session, entity_type: str, entity_id: int, mbid: str, commit: bool = True, *, relations=_UNSET
) -> EntityAwardResult:
    """Look up award series for one already-MB-matched entity and write
    straight into Award/AwardAssociation for every relation that passes
    _is_award_series. This is the primary entry point -- called inline from
    the live MB-match write sites (see module docstring) right after they
    set an entity's MBID, so awards data rides along with a match the user
    was already making.

    Best-effort: a lookup failure (network, rate-limit, malformed response)
    is caught and logged, never raised -- this must not fail the surrounding
    match action (an album/artist save) just because awards enrichment
    couldn't complete. `commit=False` lets a caller that's already inside a
    larger transaction (e.g. the backfill script's own per-entity loop)
    control commit timing itself.

    `relations` lets a caller that already fetched the entity's
    series-relation-list -- e.g. on a Qt worker thread, to keep the UI
    responsive -- hand it straight in; the default sentinel means fetch it
    inline here via fetch_award_series_relations(). Passing relations=None
    explicitly means the caller's own fetch failed, and is treated the same
    as an inline lookup failure.
    """
    if relations is _UNSET:
        relations = fetch_award_series_relations(entity_type, mbid)
    if relations is None:
        return EntityAwardResult(0, 0, True)

    awards_created = 0
    associations_created = 0
    for relation in relations:
        series = relation.get("series") or {}
        if not _is_award_series(series):
            continue
        parsed = _parse_number_attribute(relation)
        if parsed is None:
            continue
        year, is_winner = parsed

        award, award_was_created = _find_or_create_award(session, series, year)
        if award_was_created:
            awards_created += 1
        created = _find_or_create_association(
            session, award, entity_type, entity_id, mbid, is_winner
        )
        if created:
            associations_created += 1

    if commit:
        session.commit()

    return EntityAwardResult(awards_created, associations_created, False)


def sync_awards(
    session,
    progress_callback: Callable[[int, int], None] | None = None,
    is_cancelled: Callable[[], bool] | None = None,
    first_pass_only: bool = False,
) -> AwardSyncStats:
    """Batch entry point: walk every MB-matched Track/Album/Artist row,
    calling import_awards_for_entity() for each. Used only by
    scripts/backfill_awards_import.py for entities matched before this
    feature existed -- not wired to any live UI action. See
    _candidate_entities for what `first_pass_only` does and why it excludes
    Track.

    `progress_callback(checked, total)` fires periodically (not every
    entity) so the script can print progress. `is_cancelled()` is polled at
    the same cadence; each entity's associations are committed as they're
    found (not held until the whole run finishes), so an interrupted run
    keeps whatever progress it already made.
    """
    candidates = list(_candidate_entities(session, first_pass_only=first_pass_only))
    total = len(candidates)

    awards_created = 0
    associations_created = 0
    lookup_failures = 0

    for checked, (entity_type, entity_id, mbid) in enumerate(candidates, start=1):
        if checked % 25 == 0:
            if progress_callback:
                progress_callback(checked, total)
            if is_cancelled and is_cancelled():
                break

        result = import_awards_for_entity(session, entity_type, entity_id, mbid)
        awards_created += result.awards_created
        associations_created += result.associations_created
        lookup_failures += int(result.lookup_failed)

    if progress_callback:
        progress_callback(total, total)

    return AwardSyncStats(
        entities_checked=len(candidates),
        awards_created=awards_created,
        associations_created=associations_created,
        lookup_failures=lookup_failures,
    )
