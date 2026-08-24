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

A chart entry that's unmatched because the library genuinely has nothing
for it (not uncommon -- charts include plenty of tracks/albums a given
library doesn't own) would otherwise get rescored on every single run
forever, since entity_id stays NULL either way. To avoid that, each run
also fingerprints the library scan it already has to do (see
_rebuild_scratch_index) and compares it to Chart.last_library_fingerprint:
an unmatched entry that was already attempted (ChartEntry.
last_match_attempt_at is set) is skipped on a re-run only while that
fingerprint hasn't changed. Any library edit that could plausibly flip an
outcome -- a rename, a new track/album, a removal -- changes the
fingerprint and forces every still-unmatched entry to be retried, so this
can never silently miss a match that a rename made possible; it only skips
the case where re-scoring is provably pointless.
"""

import hashlib
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


def _rebuild_scratch_index(session, entity_type: str) -> tuple:
    """(Re)builds the scratch FTS5 table over the library's current
    titles/artist names for `entity_type`, used as this run's fallback
    candidate shortlist source. Also returns an exact-title index --
    normalized title -> [(entity_id, normalized artist_names), ...] -- for
    the O(1) fast path in _find_match, and a fingerprint of every row's
    (entity_id, normalized title, normalized artist_names) -- see
    match_chart for how that's used to skip rescoring entries that were
    already attempted against an unchanged library. Order-independent (XOR
    of per-row hashes) since row order here has no meaning, and folds in the
    row count so two rows whose hashes happen to XOR-cancel can't fake "no
    rows changed".
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
    fingerprint_acc = 0
    for row in rows:
        norm_title = normalize_title(row["title"])
        norm_artist = normalize_title(row["artist_names"])
        exact_index[norm_title].append((row["entity_id"], norm_artist))
        row_hash = hashlib.md5(
            f"{row['entity_id']}|{norm_title}|{norm_artist}".encode()
        ).digest()
        fingerprint_acc ^= int.from_bytes(row_hash, "big")
    fingerprint = f"{len(rows)}:{fingerprint_acc:032x}"

    # Commit here rather than leaving this open until match_chart's final
    # commit: the DROP/CREATE/INSERT above open a real write transaction,
    # and the matching loop that follows is a long, read-only pass (up to
    # ~975k chart entries) -- holding the write lock for all of it starves
    # every other writer in the app (e.g. the batch-analysis coordinator)
    # until they hit busy_timeout and fail with "database is locked".
    session.commit()
    return exact_index, fingerprint


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
    against the local library. Only considers entries where entity_id IS
    NULL, so already-matched entries are never rescored.

    Within that unmatched set, an entry that was already attempted (has
    last_match_attempt_at set) is skipped *unless* the library's title/
    artist fingerprint has changed since chart.last_library_fingerprint was
    last recorded -- i.e. re-running after a "Fetch Updates" with no library
    changes only costs the newly-added weeks (never attempted), while a
    library edit (rename, add, remove -- anything touching a Track/Album
    title or artist credit) invalidates every still-unmatched entry for a
    full rescan, so a rename that turns a former non-match into a match is
    never silently missed. See _rebuild_scratch_index for the fingerprint.

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
    exact_index, fingerprint = _rebuild_scratch_index(session, chart.matched_entity_type)
    cursor = session.connection().connection.dbapi_connection.cursor()

    unmatched = session.scalars(
        select(ChartEntry).where(
            ChartEntry.chart_id == chart.chart_id,
            ChartEntry.entity_id.is_(None),
        )
    ).all()

    library_unchanged = (
        chart.last_library_fingerprint is not None
        and chart.last_library_fingerprint == fingerprint
    )
    to_score = [
        entry
        for entry in unmatched
        if not library_unchanged or entry.last_match_attempt_at is None
    ]

    if stage_callback:
        stage_callback("Matching entries...")

    now = datetime.now()
    matched = 0
    for scored, entry in enumerate(to_score, start=1):
        if scored % 500 == 0:
            if progress_callback:
                progress_callback(scored, len(to_score), matched)
            if is_cancelled and is_cancelled():
                break

        result = _find_match(cursor, exact_index, entry)
        entry.last_match_attempt_at = now
        if result is not None:
            entry.entity_type = chart.matched_entity_type
            entry.entity_id, entry.match_score = result
            matched += 1

    cursor.close()

    if progress_callback:
        progress_callback(len(to_score), len(to_score), matched)

    session.execute(text(f"DROP TABLE IF EXISTS {_SCRATCH_TABLE}"))
    chart.last_library_fingerprint = fingerprint
    chart.last_matched_at = now
    session.commit()

    return MatchStats(total_unmatched=len(unmatched), matched=matched)
