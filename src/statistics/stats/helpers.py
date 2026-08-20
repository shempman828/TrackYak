"""
stats/helpers.py

Generic, reusable building blocks shared across the statistics suite. Most
of the ~60 library statistics are the same shape (a rating leaderboard, an
outlier-controlled average, a value distribution, a top/bottom-N list)
applied to a different entity or join -- these helpers are written once and
reused rather than re-implemented per stat.
"""

from dataclasses import dataclass
from typing import Optional, Sequence

import numpy as np
from sqlalchemy import func

from src.db.db_tables import TrackArtistRole

# Valid rating range -- values outside this are excluded from all rating
# calculations across the whole statistics suite. This is the canonical
# definition; statistics_utility.py re-exports it for backward
# compatibility with any existing import of that name.
RATING_MIN = 0.5
RATING_MAX = 10.0


# ---------------------------------------------------------------------- #
#  Deduplicated artist -> track credits                                   #
# ---------------------------------------------------------------------- #


def distinct_artist_track_subquery(session):
    """(artist_id, track_id) pairs, deduplicated across an artist's
    multiple role-credits on the same track.

    TrackArtistRole's primary key is (track_id, artist_id, role_id), so an
    artist credited in two roles on one track (e.g. Primary Artist and
    Composer) produces two rows. Any per-artist rating aggregate that joins
    straight through TrackArtistRole without pinning to one role_id would
    silently count that track's rating twice. Stats that aggregate a
    specific role (composer leaderboards, per-role leaderboards) don't need
    this -- the composite PK already prevents duplicates once role_id is
    fixed -- but anything aggregating "this artist's tracks" across all
    roles (generation/type/religion/gender ratings, artist-by-country) does.
    """
    return (
        session.query(TrackArtistRole.artist_id, TrackArtistRole.track_id)
        .distinct()
        .subquery()
    )


# ---------------------------------------------------------------------- #
#  Threshold leaderboards (power-of-10 rating leaderboards)               #
# ---------------------------------------------------------------------- #


def threshold_leaderboard(
    session,
    base_query,
    entity_id_col,
    entity_name_col,
    rating_col,
    thresholds=(10, 100, 1000),
    limit=5,
    ascending=False,
):
    """Build a rating leaderboard at each of several minimum-sample-size
    tiers ("at least N rated tracks/albums").

    `base_query` must already be joined through to `rating_col` and
    filtered to the valid rating range -- this helper only owns the
    group-by/having/order/limit-per-tier logic, since the join chain
    differs per entity (genre, publisher, place, composer, ...).

    Returns {threshold: [(name, avg_rating, n), ...]}.
    """
    order_expr = func.avg(rating_col)
    order = order_expr.asc() if ascending else order_expr.desc()

    grouped = base_query.with_entities(
        entity_id_col,
        entity_name_col,
        order_expr.label("avg_rating"),
        func.count(rating_col).label("n"),
    ).group_by(entity_id_col, entity_name_col)

    results = {}
    for threshold in thresholds:
        rows = (
            grouped.having(func.count(rating_col) >= threshold)
            .order_by(order)
            .limit(limit)
            .all()
        )
        results[threshold] = [
            (name, round(avg_rating, 2), n) for _id, name, avg_rating, n in rows
        ]
    return results


# ---------------------------------------------------------------------- #
#  Outlier-controlled average                                             #
# ---------------------------------------------------------------------- #


def outlier_controlled_average(
    ratings: Sequence[float],
    method: str = "iqr",
    trim_fraction: float = 0.1,
    min_n: int = 10,
) -> Optional[float]:
    """Average a list of ratings with outlier control.

    method="iqr" (default): drop values outside [Q1 - 1.5*IQR, Q3 + 1.5*IQR]
    method="trimmed": drop the top/bottom `trim_fraction` of sorted values

    Returns None if fewer than `min_n` ratings are available.
    """
    values = [r for r in ratings if r is not None]
    if len(values) < min_n:
        return None

    arr = np.array(values, dtype=float)

    if method == "trimmed":
        arr = np.sort(arr)
        cut = int(len(arr) * trim_fraction)
        if cut > 0:
            arr = arr[cut:-cut]
        return float(np.mean(arr)) if len(arr) else None

    q1, q3 = np.percentile(arr, [25, 75])
    iqr = q3 - q1
    lower, upper = q1 - 1.5 * iqr, q3 + 1.5 * iqr
    filtered = arr[(arr >= lower) & (arr <= upper)]
    if len(filtered) == 0:
        filtered = arr
    return float(np.mean(filtered))


# ---------------------------------------------------------------------- #
#  Value distributions (histograms)                                       #
# ---------------------------------------------------------------------- #


@dataclass
class DistributionStats:
    minimum: float
    maximum: float
    mean: float
    median: float
    stdev: float
    buckets: list  # list[(bucket_start, bucket_end, count)]
    n: int


def distribution_stats(
    values: Sequence[float], bucket_count: int = 20
) -> Optional[DistributionStats]:
    """Summarize a numeric column: min/max/mean/median/stdev plus a
    histogram bucket list. Returns None if `values` is empty.
    """
    cleaned = [v for v in values if v is not None]
    if not cleaned:
        return None

    arr = np.array(cleaned, dtype=float)
    counts, edges = np.histogram(arr, bins=bucket_count)
    buckets = [
        (float(edges[i]), float(edges[i + 1]), int(counts[i]))
        for i in range(len(counts))
    ]

    return DistributionStats(
        minimum=float(arr.min()),
        maximum=float(arr.max()),
        mean=float(arr.mean()),
        median=float(np.median(arr)),
        stdev=float(arr.std()),
        buckets=buckets,
        n=len(cleaned),
    )


# ---------------------------------------------------------------------- #
#  Top/bottom-N tracks by an arbitrary metric column                      #
# ---------------------------------------------------------------------- #


def track_metric_top_bottom(session, metric_column, n=10, min_value=None, extra_filters=None):
    """Top/bottom N tracks ranked by any single `Track.<column>`.

    Returns {"top": [(track_name, primary_artist_names, value), ...],
             "bottom": [...]}.

    Queries full Track rows (bounded to `n`, cheap) rather than a raw
    column projection so this can reuse Track.primary_artist_names
    instead of re-deriving artist credit lookup logic.
    """
    query = session.query(metric_column.class_).filter(metric_column.isnot(None))
    if min_value is not None:
        query = query.filter(metric_column >= min_value)
    if extra_filters:
        query = query.filter(*extra_filters)

    def _rows(ordered_query):
        return [
            (
                track.track_name,
                track.primary_artist_names,
                getattr(track, metric_column.key),
            )
            for track in ordered_query.limit(n).all()
        ]

    top = _rows(query.order_by(metric_column.desc()))
    bottom = _rows(query.order_by(metric_column.asc()))
    return {"top": top, "bottom": bottom}


# ---------------------------------------------------------------------- #
#  Sparse-data guard                                                      #
# ---------------------------------------------------------------------- #


def sparse_data_guard(count_query, min_count=20, min_fraction=None, total=None) -> bool:
    """Return whether there's enough data to render a chart rather than an
    empty-state message. `count_query` is a SQLAlchemy Query already
    filtered to the rows that would back the stat (e.g. albums with a
    non-null estimated_sales).
    """
    count = count_query.count()
    if count < min_count:
        return False
    if min_fraction is not None and total:
        if count / total < min_fraction:
            return False
    return True
