"""Import awards for MusicBrainz-matched tracks, albums and artists from their series relations."""

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
_ALLOWED_SERIES_TYPES = {"Recording award", "Recording series", "Release group award", "Release group series", "Artist award", "Artist series"}

# MB "series" is heavily overloaded (year-end charts, playlist series and streaming
# milestones are typed as award series too), so a series must pass both the type
# filter and one of these case-insensitive name keywords. Best-effort: an award
# whose name matches no keyword is skipped.
_AWARD_NAME_KEYWORDS = ("award", "prize", "honor", "honour", "hall of fame")

_NUMBER_ATTR_RE = re.compile(r"^\s*(\d{4})\s*(winner)?\s*$", re.IGNORECASE)

# Reverse lookup (ask MB which series each library entity is in), so API calls
# scale with the library, not with MB's whole series catalog.
# entity_type -> (MB lookup function name, top-level response key). Looked
# up via getattr() at call time (not bound directly at import time) so
# tests can patch musicbrainzngs.get_*_by_id and have it take effect.
_MB_LOOKUP = {"Track": ("get_recording_by_id", "recording"), "Album": ("get_release_group_by_id", "release-group"), "Artist": ("get_artist_by_id", "artist")}


@dataclass
class AwardSyncStats:
    """Totals for one sync_awards() run."""

    entities_checked: int
    awards_created: int
    associations_created: int
    lookup_failures: int


@dataclass
class EntityAwardResult:
    """Outcome of one import_awards_for_entity() call."""

    awards_created: int
    associations_created: int
    lookup_failed: bool


def _is_award_series(series: dict) -> bool:
    """True if an MB series passes the allowed-type and award-keyword filters."""
    series_type = series.get("type") or ""
    if series_type not in _ALLOWED_SERIES_TYPES:
        return False
    name = (series.get("name") or "").lower()
    return any(keyword in name for keyword in _AWARD_NAME_KEYWORDS)


def _parse_number_attribute(relation: dict) -> tuple[int, bool] | None:
    """(year, is_winner) from a relation's "number" attribute ("2023" or "2023 winner"), else None."""
    # An unrecognized format returns None: only plainly stated data is recorded, never guessed.
    for attr in relation.get("attributes") or []:
        if attr.get("attribute") != "number":
            continue
        m = _NUMBER_ATTR_RE.match(attr.get("value") or "")
        if not m:
            return None
        return int(m.group(1)), bool(m.group(2))
    return None


def _parse_series_name(name: str) -> tuple[str, str | None]:
    """Split "Show: Category nominees" into (show, category); no ":" gives (name, None)."""
    if ":" in name:
        show, category = name.split(":", 1)
        show, category = show.strip(), category.strip()
        category = re.sub(r"\s+nominees$", "", category, flags=re.IGNORECASE)
        return show, category
    return name.strip(), None


def _find_or_create_award(session, series: dict, year: int | None) -> tuple[Award, bool]:
    """(award, created) for an MB series and year, keyed on mb_series_id."""
    award = session.scalar(select(Award).where(Award.mb_series_id == series["id"], Award.award_year == year))
    if award is not None:
        return award, False

    award_name, award_category = _parse_series_name(series.get("name") or "")
    award = Award(award_name=award_name, award_category=award_category, award_year=year, mb_series_id=series["id"])
    session.add(award)
    session.flush()
    return award, True


def _find_or_create_association(session, award: Award, entity_type: str, entity_id: int, target_mbid: str, is_winner: bool) -> bool:
    """Create the award-entity link; return True only when a new row was added."""
    # An existing row is kept, but its winner/nominee status follows later MB corrections.
    # "recipient" (not "winner") to match the marker every other read site
    # checks for -- see Award.recipients and award_detail's recipient display.
    association_type = "recipient" if is_winner else "nominee"
    existing = session.scalar(select(AwardAssociation).where(AwardAssociation.award_id == award.award_id, AwardAssociation.entity_type == entity_type, AwardAssociation.entity_id == entity_id))
    if existing is not None:
        if existing.association_type != association_type:
            existing.association_type = association_type
        return False

    session.add(AwardAssociation(award_id=award.award_id, entity_type=entity_type, entity_id=entity_id, association_type=association_type, mb_target_mbid=target_mbid))
    return True


def _candidate_entities(session, first_pass_only: bool = False):
    """Yield (entity_type, entity_id, mbid) for every MB-matched Track, Album and Artist."""
    # first_pass_only limits Album and Artist to reviewed rows. It is not applied to Track:
    # Track.first_pass belongs to an unrelated review workflow, so it would skip every track.
    for track_id, mbid in session.execute(select(Track.track_id, Track.MBID).where(Track.MBID.isnot(None), Track.MBID != "")):
        yield "Track", track_id, mbid

    album_query = select(Album.album_id, Album.release_group_MBID).where(Album.release_group_MBID.isnot(None), Album.release_group_MBID != "")
    if first_pass_only:
        album_query = album_query.where(Album.first_pass == 1)
    for album_id, mbid in session.execute(album_query):
        yield "Album", album_id, mbid

    artist_query = select(Artist.artist_id, Artist.MBID).where(Artist.MBID.isnot(None), Artist.MBID != "")
    if first_pass_only:
        artist_query = artist_query.where(Artist.first_pass == 1)
    for artist_id, mbid in session.execute(artist_query):
        yield "Artist", artist_id, mbid


# Sentinel for import_awards_for_entity's `relations` kwarg: distinguishes
# "not supplied, fetch it inline" from an explicit None ("the caller's own
# fetch failed").
_UNSET = object()


def fetch_award_series_relations(entity_type: str, mbid: str) -> list | None:
    """Fetch an entity's MB series relations; return None if the lookup failed."""
    # Split from import_awards_for_entity so Qt callers can run it on a worker thread:
    # musicbrainzngs retries up to 8x with a 30s timeout, which can freeze the UI for minutes.
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


def import_awards_for_entity(session, entity_type: str, entity_id: int, mbid: str, commit: bool = True, *, relations=_UNSET) -> EntityAwardResult:
    """Write Award/AwardAssociation rows for one MB-matched entity's award series relations."""
    # Best-effort: a lookup failure is logged and reported, never raised, so it cannot fail
    # the MB match that called it. relations=_UNSET fetches inline; None means the caller's fetch failed.
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
        created = _find_or_create_association(session, award, entity_type, entity_id, mbid, is_winner)
        if created:
            associations_created += 1

    if commit:
        session.commit()

    return EntityAwardResult(awards_created, associations_created, False)


def sync_awards(session, progress_callback: Callable[[int, int], None] | None = None, is_cancelled: Callable[[], bool] | None = None, first_pass_only: bool = False) -> AwardSyncStats:
    """Import awards for every MB-matched entity (used by scripts/backfill_awards_import.py)."""
    # Each entity commits as it goes, so a cancelled run keeps its progress.
    candidates = list(_candidate_entities(session, first_pass_only=first_pass_only))
    total = len(candidates)

    awards_created = 0
    associations_created = 0
    lookup_failures = 0
    checked = 0

    for index, (entity_type, entity_id, mbid) in enumerate(candidates, start=1):
        if index % 25 == 0:
            if progress_callback:
                progress_callback(checked, total)
            if is_cancelled and is_cancelled():
                break

        result = import_awards_for_entity(session, entity_type, entity_id, mbid)
        checked += 1
        awards_created += result.awards_created
        associations_created += result.associations_created
        lookup_failures += int(result.lookup_failed)

    if progress_callback:
        progress_callback(checked, total)

    return AwardSyncStats(entities_checked=checked, awards_created=awards_created, associations_created=associations_created, lookup_failures=lookup_failures)
