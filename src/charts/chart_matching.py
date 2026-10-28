"""Qt-free strict matcher that links ChartEntry rows to library Tracks/Albums."""

# How it works: each run builds a scratch FTS5 table over the library's titles
# (a naive scan is O(entries x library) at ~975k entries). Exact normalized
# titles resolve from an in-memory dict; the FTS5 shortlist is only a fallback.
# A candidate must pass a word-aware title test, an artist test and a year test.
# A library fingerprint lets re-runs skip already-attempted entries while the
# library is unchanged.

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
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
_PROGRESS_EVERY = 500

# ASCII hyphen, the Unicode hyphen/dash range (U+2010-2015), the minus sign and
# the slash are word separators, so "Go-Go" / "Go Go" normalize alike.
_HYPHENISH_RE = re.compile(r"[\u2010-\u2015\u2212/-]")
_PUNCT_RE = re.compile(r"[^\w\s]")
_WS_RE = re.compile(r"\s+")
_LEADING_ARTICLE_RE = re.compile(r"^(the|a|an)\s+")
# A bare "and" is dropped (after "&" is stripped as punctuation) so
# "Blood, Sweat & Tears" and "Blood Sweat And Tears" normalize alike.
_CONNECTOR_RE = re.compile(r"\band\b")

# A four-digit year token, e.g. the "2011" in "... (2011 Remaster)".
_YEAR_TOKEN_RE = re.compile(r"^(19|20)\d{2}$")

# Splits a raw artist credit into contributors. "and" is deliberately NOT a
# separator: splitting on it breaks band names like "Blood Sweat And Tears".
_ARTIST_SPLIT_RE = re.compile(r"\s*(?:&|\+|/|,|\bfeaturing\b|\bfeat\b\.?|\bft\b\.?|\bwith\b|\bx\b|\bvs\b\.?)\s*", re.IGNORECASE)

# A leftover title tail starting with one of these reads as edition noise
# ("On the Border (2001 Remaster)"), not a different song ("Focus on Sanity").
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

# Markers meaning the candidate's release_year is a later reissue date, so the
# "not after the chart year" check must not fire.
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

# Chosen to land in different match_confidence.py buckets (>0.8 vs >0.6) so the
# entry table's row coloring tells them apart.
_EXACT_SCORE = 1.0
_CONTAINS_SCORE = 0.75

# A release one year after the chart week is still valid: a late-year debut
# often peaks, and gets its album, the next year ("TiK ToK" 2009 / "Animal" 2010).
_YEAR_SLACK = 1

_SHORTLIST_SQL = f"SELECT title, artist_names, entity_id, year FROM {_SCRATCH_TABLE} WHERE {_SCRATCH_TABLE} MATCH ? ORDER BY rank LIMIT ?"


def normalize_title(s: str | None) -> str:
    """Lowercase, fold accents, split hyphens, strip punctuation and "and", and drop a leading article."""
    if not s:
        return ""
    s = s.lower().strip()
    # Chart CSVs and library tags disagree on accents constantly (Beyonce/Beyoncé).
    s = "".join(ch for ch in unicodedata.normalize("NFKD", s) if not unicodedata.combining(ch))
    s = _HYPHENISH_RE.sub(" ", s)  # "go-go" -> "go go", not "gogo"
    s = _PUNCT_RE.sub("", s)
    s = _CONNECTOR_RE.sub(" ", s)
    s = _WS_RE.sub(" ", s).strip()
    return _LEADING_ARTICLE_RE.sub("", s)


def _seq_prefix_or_suffix(short: list, long_: list) -> list | None:
    """Return long_'s leftover tokens if short is a contiguous prefix or suffix of it, else None."""
    if not short or len(short) > len(long_):
        return None
    if short == long_[: len(short)]:
        return long_[len(short) :]
    if short == long_[len(long_) - len(short) :]:
        return long_[: len(long_) - len(short)]
    return None


def _titles_match(a: str, b: str) -> bool:
    """Return whether two normalized titles are equal or differ only by an edition-noise tail."""
    if not a or not b:
        return False
    at, bt = a.split(), b.split()
    if at == bt:
        return True
    short, long_ = (at, bt) if len(at) <= len(bt) else (bt, at)
    leftover = _seq_prefix_or_suffix(short, long_)
    if leftover is None:
        return False
    # The leftover token next to the shared part decides: "Focus" + "on Sanity" fails.
    edge = leftover[0] if long_[: len(short)] == short else leftover[-1]
    return edge in _TITLE_TAIL_MARKERS or bool(_YEAR_TOKEN_RE.match(edge))


@lru_cache(maxsize=65536)
def _artist_segments(raw: str | None) -> tuple:
    """Split a raw artist credit into its normalized, deduped contributors, in order."""
    if not raw:
        return ()
    seen = []
    for part in _ARTIST_SPLIT_RE.split(raw.strip()):
        seg = normalize_title(part)
        # One-character fragments are dropped so a stray initial can't be a shared segment.
        if len(seg) >= 2 and seg not in seen:
            seen.append(seg)
    return tuple(seen)


def _artists_match(a_raw: str | None, b_raw: str | None) -> bool:
    """Return whether two raw artist credits line up (equal, shared lead contributor, or multi-word prefix/suffix)."""
    a, b = normalize_title(a_raw), normalize_title(b_raw)
    if not a or not b:
        return False
    if a == b:
        return True
    a_segs, b_segs = _artist_segments(a_raw), _artist_segments(b_raw)
    shared = set(a_segs) & set(b_segs)
    # The shared contributor must lead one credit, so two acts that both end in
    # "& The Vandellas" don't match.
    if shared and ((a_segs and a_segs[0] in shared) or (b_segs and b_segs[0] in shared)):
        return True
    # Multi-word names only: "Ye" must not grab "Faye Webster", nor "R.E.M." "Jeremy Soule".
    at, bt = a.split(), b.split()
    short, long_ = (at, bt) if len(at) <= len(bt) else (bt, at)
    return len(short) >= 2 and _seq_prefix_or_suffix(short, long_) is not None


def _has_reissue_marker(raw_title: str | None) -> bool:
    """Return whether a title carries a reissue/remaster or year marker."""
    tokens = normalize_title(raw_title).split()
    return any(tok in _REISSUE_MARKERS or _YEAR_TOKEN_RE.match(tok) for tok in tokens)


@dataclass
class MatchStats:
    """Counts from one match_chart() run."""

    total_unmatched: int
    matched: int


def _library_rows(session, entity_type: str) -> list:
    """Return title/artist/id/year dicts for every library Track or Album."""
    # Query the session directly (not controller.get) to eager-load the role
    # chains behind primary_artist_names/album_artist_names and avoid N+1.
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
        return [{"title": t.track_name, "artist_names": t.primary_artist_names, "entity_id": t.track_id, "year": t.recorded_year or (t.album.release_year if t.album else None)} for t in tracks]
    if entity_type == "Album":
        role_chain = Album.album_roles
        albums = session.scalars(
            select(Album).options(
                selectinload(role_chain).selectinload(AlbumRoleAssociation.artist),
                selectinload(role_chain).selectinload(AlbumRoleAssociation.credited_alias),
                selectinload(role_chain).selectinload(AlbumRoleAssociation.role),
            )
        ).all()
        return [{"title": a.album_name, "artist_names": a.album_artist_names, "entity_id": a.album_id, "year": a.release_year} for a in albums]
    raise ValueError(f"Unsupported entity_type: {entity_type!r}")


def _rebuild_scratch_index(session, entity_type: str) -> tuple:
    """Rebuild the scratch FTS5 table and return (exact-title index, library fingerprint)."""
    session.execute(text(f"DROP TABLE IF EXISTS {_SCRATCH_TABLE}"))
    session.execute(text(f"CREATE VIRTUAL TABLE {_SCRATCH_TABLE} USING fts5(title, artist_names, entity_id UNINDEXED, year UNINDEXED)"))

    rows = _library_rows(session, entity_type)
    if rows:
        session.execute(text(f"INSERT INTO {_SCRATCH_TABLE} (title, artist_names, entity_id, year) VALUES (:title, :artist_names, :entity_id, :year)"), rows)

    exact_index = defaultdict(list)
    fingerprint_acc = 0
    for row in rows:
        norm_title = normalize_title(row["title"])
        norm_artist = normalize_title(row["artist_names"])
        # Keep the *raw* credit: _artists_match needs the "&"/"feat." separators.
        exact_index[norm_title].append((row["entity_id"], row["artist_names"], row["year"]))
        row_hash = hashlib.md5(f"{row['entity_id']}|{norm_title}|{norm_artist}|{row['year']}".encode(), usedforsecurity=False).digest()
        # XOR keeps the fingerprint order-independent; the row count guards against cancelling hashes.
        fingerprint_acc ^= int.from_bytes(row_hash, "big")
    fingerprint = f"{len(rows)}:{fingerprint_acc:032x}"

    # Commit now: holding the write lock through the long read-only matching
    # pass starves every other writer into "database is locked".
    session.commit()
    return exact_index, fingerprint


def _year_ok(cand_year: int | None, chart_year: int | None, is_reissue: bool) -> bool:
    """Return whether a candidate did not come out materially after its chart week."""
    if cand_year is None or chart_year is None or is_reissue:
        return True
    return cand_year <= chart_year + _YEAR_SLACK


def _pick_by_year(candidates: list, chart_year: int | None) -> int | None:
    """Return the (entity_id, year) candidate's id whose year is nearest chart_year, else the first."""
    if not candidates:
        return None
    if chart_year is not None:
        dated = [(eid, y) for eid, y in candidates if y is not None]
        if dated:
            return min(dated, key=lambda pair: abs(pair[1] - chart_year))[0]
    return candidates[0][0]


def _find_match(cursor, exact_index: dict, entry: ChartEntry) -> tuple | None:
    """Return (entity_id, score) for entry's best match, or None."""
    entry_title = normalize_title(entry.raw_title)
    if not entry_title or not normalize_title(entry.raw_performer):
        return None

    chart_year = entry.chart_week.year if entry.chart_week else None

    # Exact title + artist is trusted regardless of year: a track owned only via a
    # later compilation carries that compilation's year. Year only breaks ties.
    exact_candidates = [(entity_id, cand_year) for entity_id, cand_artist_raw, cand_year in exact_index.get(entry_title, ()) if _artists_match(entry.raw_performer, cand_artist_raw)]
    best = _pick_by_year(exact_candidates, chart_year)
    if best is not None:
        return (best, _EXACT_SCORE)

    match_query = build_and_query(entry.raw_title)
    if not match_query:
        return None

    # Raw DBAPI cursor, not session.execute(): the ORM call autoflushed before
    # every query, doubling round trips across hundreds of thousands of entries.
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
    return (best, _CONTAINS_SCORE) if best is not None else None


def match_chart(
    session, chart: Chart, progress_callback: Callable[[int, int, int], None] | None = None, stage_callback: Callable[[str], None] | None = None, is_cancelled: Callable[[], bool] | None = None
) -> MatchStats:
    """Match chart's unmatched entries against the library and commit the results."""
    if stage_callback:
        stage_callback("Building title index...")
    exact_index, fingerprint = _rebuild_scratch_index(session, chart.matched_entity_type)

    cursor = None
    try:
        cursor = session.connection().connection.dbapi_connection.cursor()
        unmatched = session.scalars(select(ChartEntry).where(ChartEntry.chart_id == chart.chart_id, ChartEntry.entity_id.is_(None))).all()

        # An already-attempted entry is only re-scored when the library changed since the last run.
        library_unchanged = chart.last_library_fingerprint is not None and chart.last_library_fingerprint == fingerprint
        to_score = [entry for entry in unmatched if not library_unchanged or entry.last_match_attempt_at is None]

        if stage_callback:
            stage_callback("Matching entries...")

        now = datetime.now(UTC)
        matched = 0
        cancelled = False
        for scored, entry in enumerate(to_score, start=1):
            if scored % _PROGRESS_EVERY == 0:
                if progress_callback:
                    progress_callback(scored, len(to_score), matched)
                if is_cancelled and is_cancelled():
                    cancelled = True
                    break

            result = _find_match(cursor, exact_index, entry)
            entry.last_match_attempt_at = now
            if result is not None:
                entry.entity_type = chart.matched_entity_type
                entry.entity_id, entry.match_score = result
                matched += 1

        if progress_callback and not cancelled:
            progress_callback(len(to_score), len(to_score), matched)

        # A cancelled run keeps the old fingerprint: the entries it never reached
        # were attempted against an older library and must be retried next run.
        if not cancelled:
            chart.last_library_fingerprint = fingerprint
            chart.last_matched_at = now
        session.commit()  # a cancelled run still keeps what it scored
    except Exception:
        session.rollback()
        raise
    finally:
        if cursor is not None:
            cursor.close()
        session.execute(text(f"DROP TABLE IF EXISTS {_SCRATCH_TABLE}"))
        session.commit()

    return MatchStats(total_unmatched=len(unmatched), matched=matched)
