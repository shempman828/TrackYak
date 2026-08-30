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
leading "the"/"a"/"an"). The line-up test is word-aware, not a raw
substring: the two token lists must be equal, or the shorter must be a
contiguous prefix/suffix of the longer -- and for a title, the leftover
tail has to look like edition noise (a "(2011 Remaster)", "(Album
Version)", "Featuring ..." etc.). So "Dark Side of the Moon" still matches
"The Dark Side of the Moon" and "On the Border" still matches "On the
Border (2001 Remaster)", but "King" no longer grabs "Kingston", "Stand"
no longer grabs "Standing Stones", and "Focus" no longer grabs "Focus on
Sanity". Artist comparison also splits on collab separators
("&"/"feat."/"featuring"/"with"/"x"/...) so a shared credit is enough and
"R.E.M." can't match "Jeremy Soule" on a "rem" substring.

A third axis, applied only when the data is present: a candidate whose
known release year is *after* the chart week's year is rejected outright
(a song can't chart before it exists), unless the candidate title carries
a remaster/reissue/anniversary/"(YYYY)" marker -- in which case its
album's release_year is a reissue date, not the recording's, so the year
check is skipped. When several candidates survive title+artist, the one
whose year is nearest to (but not after) the chart year wins.
See src/charts/fts_query.py for the query-
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

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import re
import unicodedata

from sqlalchemy import select, text
from sqlalchemy.orm import selectinload

from src.charts.fts_query import build_and_query
from src.db.db_tables.album import Album
from src.db.db_tables.associations import AlbumRoleAssociation, TrackArtistRole
from src.db.db_tables.chart import Chart, ChartEntry
from src.db.db_tables.track import Track

_SCRATCH_TABLE = "chart_match_scratch_fts"
_SHORTLIST_LIMIT = 20

# ASCII hyphen, the Unicode hyphen/dash range (U+2010-2015), the minus
# sign, and the slash -- all treated as word separators, not letters, so
# "Go-Go" / "Go Go" and "Run-D.M.C." (however its hyphen is encoded)
# normalize alike.
_HYPHENISH_RE = re.compile(r"[‐-―−/-]")
_PUNCT_RE = re.compile(r"[^\w\s]")
_WS_RE = re.compile(r"\s+")
_LEADING_ARTICLE_RE = re.compile(r"^(the|a|an)\s+")
# A bare "and" connector -- dropped so "Blood, Sweat & Tears" (the "&" is
# stripped as punctuation) and "Blood Sweat And Tears" collapse to the
# same string, likewise "Tom Petty & The Heartbreakers" / "... And The
# ...". Applied after punctuation stripping, so it only ever sees the
# literal word.
_CONNECTOR_RE = re.compile(r"\band\b")

# A four-digit year token, e.g. the "2011" in "... (2011 Remaster)".
_YEAR_TOKEN_RE = re.compile(r"^(19|20)\d{2}$")

# Splits an artist credit into individual contributors. Runs on the raw
# string so "&", "+", "/", "," and the "feat."/"featuring"/"ft"/"with"/
# "x"/"vs" words all break a credit into its parts -- a shared part is
# enough for two credits to line up. "and" is deliberately NOT a separator
# here (normalize_title drops it as a connector instead): splitting on it
# fuses band names like "Blood Sweat And Tears" inconsistently with their
# "&" spelling.
_ARTIST_SPLIT_RE = re.compile(
    r"\s*(?:&|\+|/|,|\bfeaturing\b|\bfeat\b\.?|\bft\b\.?|\bwith\b|\bx\b|\bvs\b\.?)\s*",
    re.IGNORECASE,
)

# Tokens that make a title's leftover tail read as edition noise rather
# than a different song -- lets "On the Border" match "On the Border
# (2001 Remaster)" and "Get Up" match "Get Up (Featuring Chamillionaire)"
# while still rejecting "Focus" -> "Focus on Sanity".
_TITLE_TAIL_MARKERS = frozenset(
    {
        "remaster",
        "remastered",
        "remasters",
        "remix",
        "remixed",
        "mix",
        "edit",
        "edited",
        "version",
        "reissue",
        "deluxe",
        "expanded",
        "anniversary",
        "edition",
        "mono",
        "stereo",
        "live",
        "acoustic",
        "unplugged",
        "demo",
        "session",
        "sessions",
        "instrumental",
        "single",
        "radio",
        "album",
        "bonus",
        "rerecorded",
        "rerecording",
        "taylors",
        "mixes",
        "extended",
        "original",
        "digital",
        "explicit",
        "clean",
        "featuring",
        "feat",
        "ft",
        "with",
    }
)

# The subset of the above that specifically signals a *reissue* -- i.e. a
# case where the candidate album's release_year is a later reissue date,
# not the year the recording actually came out, so the "not after the
# chart year" check must not fire.
_REISSUE_MARKERS = frozenset(
    {
        "remaster",
        "remastered",
        "remasters",
        "reissue",
        "deluxe",
        "expanded",
        "anniversary",
        "edition",
        "mono",
        "stereo",
        "rerecorded",
        "rerecording",
        "taylors",
        "version",
        "mix",
        "mixes",
        "remix",
        "remixed",
        "edit",
    }
)

# The only two outcomes a match can have: normalized titles came out
# exactly equal, or one merely contains the other. Chosen to land in
# different match_confidence.py buckets (>0.8 vs >0.6) so
# chart_entry_table.py's row coloring still visibly distinguishes them.
_EXACT_SCORE = 1.0
_CONTAINS_SCORE = 0.75

_SHORTLIST_SQL = (
    f"SELECT title, artist_names, entity_id, year FROM {_SCRATCH_TABLE} "
    f"WHERE {_SCRATCH_TABLE} MATCH ? ORDER BY rank LIMIT ?"
)


def normalize_title(s: str | None) -> str:
    """Lowercase, fold diacritics, split on hyphen/slash, strip punctuation,
    drop a bare "and" connector, collapse whitespace, and drop a leading
    "the"/"a"/"an". The slop this matcher allows: "Dark Side of the Moon"
    and "The Dark Side of the Moon", "Beyonce" and "Beyoncé", "Wake Me Up
    Before You Go-Go" and "... Go Go", "Blood, Sweat & Tears" and "Blood
    Sweat And Tears" all normalize to the same string."""
    if not s:
        return ""
    s = s.lower().strip()
    # Fold accents: "é" -> "e", "ö" -> "o" -- chart CSVs and library tags
    # disagree on these constantly (Beyonce/Beyoncé, Motley/Mötley).
    s = "".join(ch for ch in unicodedata.normalize("NFKD", s) if not unicodedata.combining(ch))
    s = _HYPHENISH_RE.sub(" ", s)  # "go-go" -> "go go", not "gogo"
    s = _PUNCT_RE.sub("", s)
    s = _CONNECTOR_RE.sub(" ", s)
    s = _WS_RE.sub(" ", s).strip()
    s = _LEADING_ARTICLE_RE.sub("", s)
    return s


def _seq_prefix_or_suffix(short: list, long_: list) -> list | None:
    """If `short` is a contiguous prefix or suffix of `long_`, return the
    leftover tokens (the part of `long_` that `short` didn't cover);
    otherwise return None. `short` must be non-empty and no longer than
    `long_`. An exact-length equal match returns [] (falsy but not None)."""
    if not short or len(short) > len(long_):
        return None
    if short == long_[: len(short)]:
        return long_[len(short) :]
    if short == long_[len(long_) - len(short) :]:
        return long_[: len(long_) - len(short)]
    return None


def _titles_match(a: str, b: str) -> bool:
    """True if normalized titles `a`/`b` line up: equal token lists, or the
    shorter is a contiguous prefix/suffix of the longer *and* the leftover
    tail reads as edition noise (a "(2011 Remaster)", "(Album Version)",
    "Featuring ..." and the like). Empty strings never match."""
    if not a or not b:
        return False
    at, bt = a.split(), b.split()
    if at == bt:
        return True
    short, long_ = (at, bt) if len(at) <= len(bt) else (bt, at)
    leftover = _seq_prefix_or_suffix(short, long_)
    if not leftover:
        return bool(leftover == [])
    edge = leftover[0] if long_[: len(short)] == short else leftover[-1]
    return edge in _TITLE_TAIL_MARKERS or bool(_YEAR_TOKEN_RE.match(edge))


def _artist_segments(raw: str | None) -> list:
    """Split a raw artist credit on collab separators into its normalized
    contributors, in order, deduped, e.g. "Ciara Featuring Chamillionaire"
    -> ["ciara", "chamillionaire"]. One-character fragments are dropped so
    a stray initial can't become a shared "segment"."""
    if not raw:
        return []
    seen = []
    for part in _ARTIST_SPLIT_RE.split(raw.strip()):
        seg = normalize_title(part)
        if len(seg) >= 2 and seg not in seen:
            seen.append(seg)
    return seen


def _artists_match(a_raw: str | None, b_raw: str | None) -> bool:
    """True if two raw artist credits line up: identical once normalized,
    or they share a contributor *and* that contributor leads at least one
    of the two credits (so "Drake & Future" lines up with "Drake" and a
    "X Featuring Y" credit lines up with a "Y Featuring X" one, but two
    different acts that both end in "& The Vandellas" don't), or -- for
    multi-word names only -- one is a contiguous prefix/suffix of the
    other. A single-token name that isn't a shared lead never matches, so
    "Ye" can't grab "Faye Webster" and "R.E.M." can't grab "Jeremy
    Soule"."""
    a, b = normalize_title(a_raw), normalize_title(b_raw)
    if not a or not b:
        return False
    if a == b:
        return True
    a_segs, b_segs = _artist_segments(a_raw), _artist_segments(b_raw)
    shared = set(a_segs) & set(b_segs)
    if shared and ((a_segs and a_segs[0] in shared) or (b_segs and b_segs[0] in shared)):
        return True
    at, bt = a.split(), b.split()
    short, long_ = (at, bt) if len(at) <= len(bt) else (bt, at)
    return len(short) >= 2 and _seq_prefix_or_suffix(short, long_) is not None


def _has_reissue_marker(raw_title: str | None) -> bool:
    """True if the title carries a token that marks it as a reissue/
    remaster (so a candidate album's release_year is a later reissue date,
    not the recording's year, and the "not after the chart year" check
    should be skipped)."""
    tokens = normalize_title(raw_title).split()
    return any(tok in _REISSUE_MARKERS or _YEAR_TOKEN_RE.match(tok) for tok in tokens)


@dataclass
class MatchStats:
    total_unmatched: int
    matched: int


def _rebuild_scratch_index(session, entity_type: str) -> tuple:
    """(Re)builds the scratch FTS5 table over the library's current
    titles/artist names for `entity_type`, used as this run's fallback
    candidate shortlist source. Also returns an exact-title index --
    normalized title -> [(entity_id, raw artist_names, year), ...] -- for
    the O(1) fast path in _find_match, and a
    fingerprint of every row's (entity_id, normalized title, normalized
    artist_names, year) -- see match_chart for how that's used to skip
    rescoring entries that were already attempted against an unchanged
    library. Order-independent (XOR of per-row hashes) since row order here
    has no meaning, and folds in the row count so two rows whose hashes
    happen to XOR-cancel can't fake "no rows changed".
    """
    session.execute(text(f"DROP TABLE IF EXISTS {_SCRATCH_TABLE}"))
    session.execute(
        text(
            f"CREATE VIRTUAL TABLE {_SCRATCH_TABLE} USING fts5("
            "title, artist_names, entity_id UNINDEXED, year UNINDEXED)"
        )
    )

    if entity_type == "Track":
        role_chain = Track.artist_roles
        tracks = session.scalars(
            select(Track).options(
                selectinload(role_chain).selectinload(TrackArtistRole.artist),
                selectinload(role_chain).selectinload(TrackArtistRole.credited_alias),
                selectinload(role_chain).selectinload(TrackArtistRole.role),
                selectinload(Track.album),
            )
        ).all()
        rows = [
            {
                "title": track.track_name,
                "artist_names": track.primary_artist_names,
                "entity_id": track.track_id,
                "year": track.recorded_year or (track.album.release_year if track.album else None),
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
                "year": album.release_year,
            }
            for album in albums
        ]
    else:
        raise ValueError(f"Unsupported entity_type: {entity_type!r}")

    if rows:
        session.execute(
            text(
                f"INSERT INTO {_SCRATCH_TABLE} (title, artist_names, entity_id, year) "
                "VALUES (:title, :artist_names, :entity_id, :year)"
            ),
            rows,
        )

    exact_index = defaultdict(list)
    fingerprint_acc = 0
    for row in rows:
        norm_title = normalize_title(row["title"])
        norm_artist = normalize_title(row["artist_names"])
        # Store the *raw* credit, not norm_artist: _artists_match needs the
        # "&"/"feat." separators intact to split a credit into its
        # contributors, and normalize_title has already stripped them.
        exact_index[norm_title].append((row["entity_id"], row["artist_names"], row["year"]))
        row_hash = hashlib.md5(
            f"{row['entity_id']}|{norm_title}|{norm_artist}|{row['year']}".encode()
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


# A release tagged one year *after* the first chart week is still a valid
# match: a single that debuts late in the year commonly peaks -- and has
# its album/"release year" recorded -- the following year (e.g. Kesha's
# "TiK ToK" first charts in 2009, album "Animal" is 2010). More slop than
# that and it's a different recording.
_YEAR_SLACK = 1


def _year_ok(cand_year: int | None, chart_year: int | None, is_reissue: bool) -> bool:
    """A candidate can't have come out materially *after* the week it
    supposedly charted. Skipped when either year is unknown, or when the
    candidate is a flagged reissue/remaster (its album's release_year is
    then a later reissue date, not the recording's)."""
    if cand_year is None or chart_year is None or is_reissue:
        return True
    return cand_year <= chart_year + _YEAR_SLACK


def _pick_by_year(candidates: list, chart_year: int | None) -> int | None:
    """From (entity_id, year) candidates that already passed title/artist/
    year checks, return the best entity_id: the one whose year sits closest
    to `chart_year`, else (nothing dated, or no chart year) just the
    first."""
    if not candidates:
        return None
    if chart_year is not None:
        dated = [(eid, y) for eid, y in candidates if y is not None]
        if dated:
            return min(dated, key=lambda pair: abs(pair[1] - chart_year))[0]
    return candidates[0][0]


def _find_match(cursor, exact_index: dict, entry: ChartEntry) -> tuple | None:
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

    chart_year = entry.chart_week.year if entry.chart_week else None

    # Exact normalized title + artist is trusted regardless of year: a
    # track you only own via a later hits compilation carries that
    # compilation's release_year, so a "not after the chart year" reject
    # here would drop correct matches wholesale. Year is only a
    # tie-breaker (_pick_by_year) when several tracks share the title.
    exact_candidates = [
        (entity_id, cand_year)
        for entity_id, cand_artist_raw, cand_year in exact_index.get(entry_title, ())
        if _artists_match(entry.raw_performer, cand_artist_raw)
    ]
    best = _pick_by_year(exact_candidates, chart_year)
    if best is not None:
        return (best, _EXACT_SCORE)

    match_query = build_and_query(entry.raw_title)
    if not match_query:
        return None

    cursor.execute(_SHORTLIST_SQL, (match_query, _SHORTLIST_LIMIT))
    contains_candidates = []
    for cand_title_raw, cand_artist_names, entity_id, cand_year in cursor.fetchall():
        cand_title = normalize_title(cand_title_raw)
        if cand_title == entry_title:
            continue  # already ruled out by the exact-index check above
        if not _titles_match(entry_title, cand_title):
            continue
        if not _artists_match(entry.raw_performer, cand_artist_names):
            continue
        if not _year_ok(cand_year, chart_year, _has_reissue_marker(cand_title_raw)):
            continue
        contains_candidates.append((entity_id, cand_year))

    best = _pick_by_year(contains_candidates, chart_year)
    if best is not None:
        return (best, _CONTAINS_SCORE)

    return None


def match_chart(
    session,
    chart: Chart,
    progress_callback: Callable[[int, int, int], None] | None = None,
    stage_callback: Callable[[str], None] | None = None,
    is_cancelled: Callable[[], bool] | None = None,
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
            ChartEntry.chart_id == chart.chart_id, ChartEntry.entity_id.is_(None)
        )
    ).all()

    library_unchanged = (
        chart.last_library_fingerprint is not None and chart.last_library_fingerprint == fingerprint
    )
    to_score = [
        entry for entry in unmatched if not library_unchanged or entry.last_match_attempt_at is None
    ]

    if stage_callback:
        stage_callback("Matching entries...")

    now = datetime.now(UTC)
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
