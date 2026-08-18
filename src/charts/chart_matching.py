"""
chart_matching.py

Blocking-indexed matcher: links ChartEntry rows to real Track/Album records
by normalized-title bucketing. Matching ~975,000 chart entries against a
library of tens of thousands of tracks/albums is only feasible this way --
a naive per-entry scan of the whole library is an O(entries x library)
cross product (billions of SequenceMatcher calls). Every existing matcher in
this codebase (e.g. album_musicbrainz_review_dialog.py) only ever compares
within one album's tracklist (tens of candidates), so none of them face this
scale; this module exists because that technique doesn't transfer as-is.

No Qt dependency here -- see chart_matching_worker.py for the background-
thread wrapper. Queries the session directly (not via controller.get) so it
controls eager-loading precisely: Track.primary_artist_names and
Album.album_artist_names are Python properties that walk artist_roles/
album_roles -> artist/credited_alias/role, which would otherwise trigger
several extra queries per track/album (N+1) while building the index.
"""

import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher
from typing import Callable, Optional

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from src.db.db_tables.album import Album
from src.db.db_tables.associations import AlbumRoleAssociation, TrackArtistRole
from src.db.db_tables.chart import Chart, ChartEntry
from src.db.db_tables.track import Track

# Title-dominant, artist as a secondary signal -- there's no "position"-style
# tiebreaker available here (unlike the MusicBrainz track matcher), so both
# signals are required rather than one being a minor adjustment to the other.
_TITLE_WEIGHT = 0.6
_ARTIST_WEIGHT = 0.4

# Looser than album_musicbrainz_review_dialog's 0.65 (which has a position
# signal to fall back on): chart titles/performers carry more cosmetic noise
# ("feat.", "(Remastered 2009)") than same-release MusicBrainz/local pairs.
_AUTO_MATCH_SCORE_FLOOR = 0.72

_PUNCT_RE = re.compile(r"[^\w\s]")
_WS_RE = re.compile(r"\s+")


def normalize_title(s: Optional[str]) -> str:
    """Lowercase, strip punctuation, collapse whitespace. Deliberately
    coarse -- this only needs to bucket near-duplicates together for the
    blocking index below, not produce a canonical title."""
    if not s:
        return ""
    s = s.lower().strip()
    s = _PUNCT_RE.sub("", s)
    s = _WS_RE.sub(" ", s)
    return s


@dataclass
class _Candidate:
    entity_id: int
    title: str
    artist_names: str


@dataclass
class MatchStats:
    total_unmatched: int
    matched: int


def build_title_index(session, entity_type: str) -> dict:
    """One-time O(library size) pass building normalized-title -> candidates,
    so the per-entry matching loop in match_chart() never scans the whole
    library. Eager-loads everything primary_artist_names/album_artist_names
    touches so building the index doesn't N+1 per track/album."""
    index = defaultdict(list)

    if entity_type == "Track":
        role_chain = Track.artist_roles
        tracks = session.scalars(
            select(Track).options(
                selectinload(role_chain).selectinload(TrackArtistRole.artist),
                selectinload(role_chain).selectinload(TrackArtistRole.credited_alias),
                selectinload(role_chain).selectinload(TrackArtistRole.role),
            )
        ).all()
        for track in tracks:
            index[normalize_title(track.track_name)].append(
                _Candidate(track.track_id, track.track_name, track.primary_artist_names)
            )
    elif entity_type == "Album":
        role_chain = Album.album_roles
        albums = session.scalars(
            select(Album).options(
                selectinload(role_chain).selectinload(AlbumRoleAssociation.artist),
                selectinload(role_chain).selectinload(AlbumRoleAssociation.credited_alias),
                selectinload(role_chain).selectinload(AlbumRoleAssociation.role),
            )
        ).all()
        for album in albums:
            index[normalize_title(album.album_name)].append(
                _Candidate(album.album_id, album.album_name, album.album_artist_names)
            )
    else:
        raise ValueError(f"Unsupported entity_type: {entity_type!r}")

    return index


def _bucket_key(normalized: str) -> tuple:
    """Secondary index key for the adjacent-bucket fallback: first
    character + a coarse length bucket. Cheap and bounded -- used only when
    an entry's exact-normalized-title bucket is empty (the common case,
    since most historical chart entries won't be in a personal library),
    and even then only checks other titles sharing this key, never a sweep
    over the whole index."""
    if not normalized:
        return ("", 0)
    return (normalized[0], len(normalized) // 4)


def _build_secondary_index(index: dict) -> dict:
    secondary = defaultdict(list)
    for key in index:
        secondary[_bucket_key(key)].append(key)
    return secondary


def _adjacent_bucket_candidates(index: dict, secondary_index: dict, key: str) -> list:
    candidates = []
    for adjacent_key in secondary_index.get(_bucket_key(key), []):
        if adjacent_key != key:
            candidates.extend(index[adjacent_key])
    return candidates


def _title_similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()


def _combined_score(chart_title: str, chart_artist: str, cand: _Candidate) -> float:
    title_sim = _title_similarity(chart_title, cand.title or "")
    artist_sim = _title_similarity(chart_artist or "", cand.artist_names or "")
    return _TITLE_WEIGHT * title_sim + _ARTIST_WEIGHT * artist_sim


def match_chart(
    session,
    chart: Chart,
    progress_callback: Optional[Callable[[int, int, int], None]] = None,
    stage_callback: Optional[Callable[[str], None]] = None,
    is_cancelled: Optional[Callable[[], bool]] = None,
) -> MatchStats:
    """Match every currently-unmatched ChartEntry belonging to `chart`
    against the local library, using blocking by normalized title. Only
    scores entries where entity_id IS NULL, so re-running after a "Fetch
    Updates" only costs the newly-added weeks, not the whole chart -- and
    re-running after the library grows re-attempts everything still
    unmatched, without re-scoring entries that already matched.

    `stage_callback(message)` fires once per phase ("Building title index...",
    "Matching entries...") so a caller can show what's actually happening
    during the index build, which otherwise looks identical to a hang --
    it's O(library size) and reports no per-item progress of its own.

    `progress_callback(scored, total, matched)` is invoked periodically (not
    every entry -- that would dominate runtime at chart scale) so a caller
    (e.g. ChartMatchingWorker) can drive a progress bar and show a live
    matched-so-far count. `is_cancelled()` is polled at the same cadence for
    cooperative cancellation; a cancelled run commits whatever was scored so
    far rather than discarding it.
    """
    if stage_callback:
        stage_callback("Building title index...")
    index = build_title_index(session, chart.matched_entity_type)
    secondary_index = _build_secondary_index(index)

    unmatched = session.scalars(
        select(ChartEntry).where(
            ChartEntry.chart_id == chart.chart_id,
            ChartEntry.entity_id.is_(None),
        )
    ).all()

    if stage_callback:
        stage_callback("Matching entries...")

    matched = 0
    for scored, entry in enumerate(unmatched, start=1):
        if scored % 500 == 0:
            if progress_callback:
                progress_callback(scored, len(unmatched), matched)
            if is_cancelled and is_cancelled():
                break

        key = normalize_title(entry.raw_title)
        candidates = index.get(key, [])
        if not candidates:
            candidates = _adjacent_bucket_candidates(index, secondary_index, key)

        best_cand = None
        best_score = 0.0
        for cand in candidates:
            score = _combined_score(entry.raw_title, entry.raw_performer, cand)
            if score > best_score:
                best_score, best_cand = score, cand

        if best_cand is not None and best_score >= _AUTO_MATCH_SCORE_FLOOR:
            entry.entity_type = chart.matched_entity_type
            entry.entity_id = best_cand.entity_id
            entry.match_score = best_score
            matched += 1

    if progress_callback:
        progress_callback(len(unmatched), len(unmatched), matched)

    chart.last_matched_at = datetime.now()
    session.commit()

    return MatchStats(total_unmatched=len(unmatched), matched=matched)
