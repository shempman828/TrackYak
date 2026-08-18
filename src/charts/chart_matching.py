"""
chart_matching.py

FTS5-shortlisted matcher: links ChartEntry rows to real Track/Album records
by title. Matching ~975,000 chart entries against a library of tens of
thousands of tracks/albums is only feasible with an index -- a naive
per-entry scan of the whole library is an O(entries x library) cross
product. Each run builds a scratch FTS5 table over the library's current
titles (rebuilt from scratch every run rather than kept permanently in
sync, since matching is a one-off batch pass, not an interactive search --
that also means no triggers to maintain on the heavily-written tracks/
albums tables). Per chart entry, an FTS5 MATCH pulls a small, lexically-
relevant candidate shortlist, which is then checked with a simple,
deterministic containment rule rather than a fuzzy similarity score.

Matching is intentionally strict and boolean-ish: title and artist both
have to line up (after normalizing case/punctuation and stripping a
leading "the"/"a"/"an") for either the chart entry or the candidate to be a
normalized substring of the other -- e.g. "Dark Side of the Moon" matches
"The Dark Side of the Moon", but two different songs that just happen to
share a few words don't. See src/charts/fts_query.py for the query-
building helper shared with the search tab's MATCH query: an AND of every
(stopword-filtered) word in the chart title. This is a *shortlist* query,
not the match decision itself -- the containment check above is -- so
AND was chosen over OR deliberately: OR means any single shared word (even
a common one) pulls a row into the ranked candidate set, and "ORDER BY
rank LIMIT n" still has to score every one of those rows before truncating
-- there's no cheap top-k shortcut in SQLite's FTS5 for that. At chart-
matching scale (hundreds of thousands of entries), that made OR
measurably slower than AND against a synthetic worst-case corpus. The
trade-off: a chart title that differs from its true library match by more
than a leading article -- e.g. a trailing "(Remastered 2011)" -- won't be
found via the shortlist, since AND requires every word (still normalized
via stopword-filtering, so "the"/"of"/etc. don't count against it).

Every exact normalized-title match is resolved from an in-memory dict
(`_exact_index`, built once per run) rather than a FTS5 query -- an O(1)
lookup is both faster and, for the common case, all that's needed, so
the FTS5 shortlist is only ever queried as a fallback for entries whose
title doesn't match anything exactly. The FTS5 lookup itself also goes
through a raw DBAPI cursor rather than session.execute(): the per-entry
ORM call was triggering a full autoflush (of the *previous* entry's
just-written match) before every single query, silently doubling the SQL
round trips across a run of hundreds of thousands of entries.

No Qt dependency here -- see chart_matching_worker.py for the background-
thread wrapper. Queries the session directly (not via controller.get) so it
controls eager-loading precisely: Track.primary_artist_names and
Album.album_artist_names are Python properties that walk artist_roles/
album_roles -> artist/credited_alias/role, which would otherwise trigger
several extra queries per track/album (N+1) while building the shortlist
index.
"""

import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Optional

from sqlalchemy import select, text
from sqlalchemy.orm import selectinload

from src.charts.fts_query import build_and_query
from src.db.db_tables.album import Album
from src.db.db_tables.associations import AlbumRoleAssociation, TrackArtistRole
from src.db.db_tables.chart import Chart, ChartEntry
from src.db.db_tables.track import Track

_SCRATCH_TABLE = "chart_match_scratch_fts"
_SHORTLIST_LIMIT = 20

_PUNCT_RE = re.compile(r"[^\w\s]")
_WS_RE = re.compile(r"\s+")
_LEADING_ARTICLE_RE = re.compile(r"^(the|a|an)\s+")

# The only two outcomes a match can have: normalized titles came out
# exactly equal, or one merely contains the other. Chosen to land in
# different match_confidence.py buckets (>0.8 vs >0.6) so
# chart_entry_table.py's row coloring still visibly distinguishes them.
_EXACT_SCORE = 1.0
_CONTAINS_SCORE = 0.75

_SHORTLIST_SQL = (
    f"SELECT title, artist_names, entity_id FROM {_SCRATCH_TABLE} "
    f"WHERE {_SCRATCH_TABLE} MATCH ? ORDER BY rank LIMIT ?"
)


def normalize_title(s: Optional[str]) -> str:
    """Lowercase, strip punctuation, collapse whitespace, and drop a
    leading "the"/"a"/"an" -- the only slop this matcher allows, so e.g.
    "Dark Side of the Moon" and "The Dark Side of the Moon" normalize to
    the same string."""
    if not s:
        return ""
    s = s.lower().strip()
    s = _PUNCT_RE.sub("", s)
    s = _WS_RE.sub(" ", s)
    s = _LEADING_ARTICLE_RE.sub("", s)
    return s


def _normalized_match(a: str, b: str) -> bool:
    """True if normalized a/b are equal or one contains the other. Empty
    strings never match -- otherwise this would be trivially true against
    every candidate."""
    if not a or not b:
        return False
    return a == b or a in b or b in a


@dataclass
class MatchStats:
    total_unmatched: int
    matched: int


def _rebuild_scratch_index(session, entity_type: str) -> dict:
    """(Re)builds the scratch FTS5 table over the library's current
    titles/artist names for `entity_type`, used as this run's fallback
    candidate shortlist source. Also returns an exact-title index --
    normalized title -> [(entity_id, normalized artist_names), ...] -- for
    the O(1) fast path in _find_match.
    """
    session.execute(text(f"DROP TABLE IF EXISTS {_SCRATCH_TABLE}"))
    session.execute(
        text(
            f"CREATE VIRTUAL TABLE {_SCRATCH_TABLE} USING fts5("
            "title, artist_names, entity_id UNINDEXED)"
        )
    )

    if entity_type == "Track":
        role_chain = Track.artist_roles
        tracks = session.scalars(
            select(Track).options(
                selectinload(role_chain).selectinload(TrackArtistRole.artist),
                selectinload(role_chain).selectinload(TrackArtistRole.credited_alias),
                selectinload(role_chain).selectinload(TrackArtistRole.role),
            )
        ).all()
        rows = [
            {
                "title": track.track_name,
                "artist_names": track.primary_artist_names,
                "entity_id": track.track_id,
            }
            for track in tracks
        ]
    elif entity_type == "Album":
        role_chain = Album.album_roles
        albums = session.scalars(
            select(Album).options(
                selectinload(role_chain).selectinload(AlbumRoleAssociation.artist),
                selectinload(role_chain).selectinload(AlbumRoleAssociation.credited_alias),
                selectinload(role_chain).selectinload(AlbumRoleAssociation.role),
            )
        ).all()
        rows = [
            {
                "title": album.album_name,
                "artist_names": album.album_artist_names,
                "entity_id": album.album_id,
            }
            for album in albums
        ]
    else:
        raise ValueError(f"Unsupported entity_type: {entity_type!r}")

    if rows:
        session.execute(
            text(
                f"INSERT INTO {_SCRATCH_TABLE} (title, artist_names, entity_id) "
                "VALUES (:title, :artist_names, :entity_id)"
            ),
            rows,
        )

    exact_index = defaultdict(list)
    for row in rows:
        exact_index[normalize_title(row["title"])].append(
            (row["entity_id"], normalize_title(row["artist_names"]))
        )
    return exact_index


def _find_match(cursor, exact_index: dict, entry: ChartEntry) -> Optional[tuple]:
    """Return (entity_id, score) for the best match, or None. Checks the
    O(1) exact-title index first; only falls back to an FTS5 shortlist
    query (via the raw cursor) if that finds nothing, since a match found
    there is definitionally as good as it gets -- no candidate found by a
    looser containment check could ever outscore an exact one.
    """
    entry_title = normalize_title(entry.raw_title)
    entry_artist = normalize_title(entry.raw_performer)
    if not entry_title or not entry_artist:
        return None

    for entity_id, cand_artist in exact_index.get(entry_title, ()):
        if _normalized_match(entry_artist, cand_artist):
            return (entity_id, _EXACT_SCORE)

    match_query = build_and_query(entry.raw_title)
    if not match_query:
        return None

    cursor.execute(_SHORTLIST_SQL, (match_query, _SHORTLIST_LIMIT))
    for cand_title, cand_artist_names, entity_id in cursor.fetchall():
        cand_title = normalize_title(cand_title)
        if cand_title == entry_title:
            continue  # already ruled out by the exact-index check above
        if not _normalized_match(entry_title, cand_title):
            continue
        if not _normalized_match(entry_artist, normalize_title(cand_artist_names)):
            continue
        return (entity_id, _CONTAINS_SCORE)

    return None


def match_chart(
    session,
    chart: Chart,
    progress_callback: Optional[Callable[[int, int, int], None]] = None,
    stage_callback: Optional[Callable[[str], None]] = None,
    is_cancelled: Optional[Callable[[], bool]] = None,
) -> MatchStats:
    """Match every currently-unmatched ChartEntry belonging to `chart`
    against the local library. Only scores entries where entity_id IS
    NULL, so re-running after a "Fetch Updates" only costs the newly-added
    weeks, not the whole chart -- and re-running after the library grows
    re-attempts everything still unmatched, without re-scoring entries that
    already matched.

    `stage_callback(message)` fires once per phase ("Building title
    index...", "Matching entries...") so a caller can show what's actually
    happening during the shortlist-index build, which otherwise looks
    identical to a hang.

    `progress_callback(scored, total, matched)` is invoked periodically (not
    every entry -- that would dominate runtime at chart scale) so a caller
    (e.g. ChartMatchingWorker) can drive a progress bar and show a live
    matched-so-far count. `is_cancelled()` is polled at the same cadence for
    cooperative cancellation; a cancelled run commits whatever was scored so
    far rather than discarding it.
    """
    if stage_callback:
        stage_callback("Building title index...")
    exact_index = _rebuild_scratch_index(session, chart.matched_entity_type)
    cursor = session.connection().connection.dbapi_connection.cursor()

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

        result = _find_match(cursor, exact_index, entry)
        if result is not None:
            entry.entity_type = chart.matched_entity_type
            entry.entity_id, entry.match_score = result
            matched += 1

    cursor.close()

    if progress_callback:
        progress_callback(len(unmatched), len(unmatched), matched)

    session.execute(text(f"DROP TABLE IF EXISTS {_SCRATCH_TABLE}"))
    chart.last_matched_at = datetime.now()
    session.commit()

    return MatchStats(total_unmatched=len(unmatched), matched=matched)
