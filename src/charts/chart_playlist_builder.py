"""
chart_playlist_builder.py

Builds/updates two independent nested playlist trees -- one per Chart row
(Billboard Hot 100, Billboard 200) -- from matched ChartEntry data:

    <Chart name>              all matched tracks, every year
    +-- <decade>s             all matched tracks charted in that decade
        +-- <year>            all matched tracks charted in that year

Every level is a real playlist with its own materialized PlaylistTracks
rows (not just a folder / computed count) -- see
docs/specs/chart_playlists.md. Regeneration is idempotent: existing nodes
are found by a marker stored in Playlist.playlist_description, not by
name, so renaming a generated playlist doesn't cause duplicate creation on
the next run, and a user's own playlist nested under a chart root (no
marker) is never touched.
"""

from collections import defaultdict
from collections.abc import Callable, Iterable

from sqlalchemy.orm import selectinload

from src.core.logger_config import logger
from src.db.db_tables.track import Track
from src.playlist.playlist_track_sync import sync_playlist_tracks

_MARKER_PREFIX = "__chart_playlist__"

# A matched track only belongs in a chart *year* playlist if it actually
# came out around then. Catalog re-entries and hits-compilation matches
# (e.g. The Beatles' "Let It Be" charting again in 2010) link to a recent
# chart week but are decades-old recordings -- keep them out. The window
# is wider than chart_matching's _YEAR_SLACK because a track's recorded/
# release year can legitimately trail its first chart week by a year or
# two (year-end peaks, delayed album pressruns, sloppy release metadata).
_YEAR_TOLERANCE = 2


def _root_marker(chart_key: str) -> str:
    return f"{_MARKER_PREFIX}:root:{chart_key}"


def _decade_marker(chart_key: str, decade: int) -> str:
    return f"{_MARKER_PREFIX}:decade:{chart_key}:{decade}"


def _year_marker(chart_key: str, year: int) -> str:
    return f"{_MARKER_PREFIX}:year:{chart_key}:{year}"


class ChartPlaylistStats:
    """Summary of one generate_or_update() run, for the completion message."""

    def __init__(self):
        self.playlists_created = 0
        self.playlists_updated = 0
        self.tracks_added = 0
        self.tracks_removed = 0

    def __repr__(self):
        return (
            f"ChartPlaylistStats(created={self.playlists_created}, "
            f"updated={self.playlists_updated}, tracks_added={self.tracks_added}, "
            f"tracks_removed={self.tracks_removed})"
        )


class ChartPlaylistBuilder:
    """Builds/updates the chart-derived playlist trees for every Chart row."""

    def __init__(self, controller):
        self.controller = controller

    def generate_or_update(
        self, progress_callback: Callable[[int, int], None] | None = None
    ) -> ChartPlaylistStats:
        """Build/update every chart's playlist tree. Always processes every
        Chart row -- there is no chart-type selection (see spec)."""
        stats = ChartPlaylistStats()
        charts = self.controller.get.get_all_entities("Chart")

        # Collect first so progress reporting has an accurate total.
        chart_year_tracks = [
            (chart, self._collect_year_tracks(chart)) for chart in charts
        ]
        total_years = sum(len(yt) for _, yt in chart_year_tracks)
        done = 0

        for chart, year_tracks in chart_year_tracks:
            self._build_chart_tree(chart, year_tracks, stats)
            done += len(year_tracks)
            if progress_callback:
                progress_callback(done, total_years)

        return stats

    # ------------------------------------------------------------------
    # Data collection
    # ------------------------------------------------------------------

    def _collect_year_tracks(self, chart) -> dict[int, set[int]]:
        """Matched entries for this chart, grouped by chart_week's year,
        resolved to concrete track_id sets. A track is only placed in a
        year when its own release/recorded year is within _YEAR_TOLERANCE
        of that year -- see the constant's note."""
        entries = self.controller.get.get_all_entities(
            "ChartEntry", chart_id__eq=chart.chart_id, entity_id__notnull=True
        )

        track_entity_ids = {e.entity_id for e in entries if e.entity_type == "Track"}
        album_ids = {e.entity_id for e in entries if e.entity_type == "Album"}
        album_track_map = self._album_track_map(album_ids)

        # One release-year lookup covering both directly-matched tracks and
        # the tracks expanded from album-matched entries.
        # ChartEntry.entity_id/entity_type is a manual polymorphic link,
        # not a real ForeignKey (see chart.py), so a matched track can
        # since have been deleted -- _track_year_map only holds tracks that
        # still exist, so a stale id resolves to None here and is skipped,
        # which also keeps it from poisoning a whole insert with a FOREIGN
        # KEY failure.
        expanded_track_ids = {
            tid for tids in album_track_map.values() for tid in tids
        }
        track_years = self._track_year_map(track_entity_ids | expanded_track_ids)

        year_tracks: dict[int, set[int]] = defaultdict(set)
        for entry in entries:
            chart_year = entry.chart_week.year
            if entry.entity_type == "Track":
                candidate_ids: Iterable[int] = (entry.entity_id,)
            elif entry.entity_type == "Album":
                candidate_ids = album_track_map.get(entry.entity_id, ())
            else:
                continue
            for track_id in candidate_ids:
                track_year = track_years.get(track_id)
                if track_year is None:
                    continue  # stale id, or no known year -- can't place it
                if abs(track_year - chart_year) <= _YEAR_TOLERANCE:
                    year_tracks[chart_year].add(track_id)
        return year_tracks

    def _track_year_map(self, track_ids: set[int]) -> dict[int, int | None]:
        """{track_id: best-known year} for the tracks that still exist. Year
        is the track's recorded_year, else its album's release_year, else
        None -- mirrors how chart_matching derives a candidate's year."""
        if not track_ids:
            return {}
        tracks = self.controller.get.get_all_entities(
            "Track",
            track_id__in=list(track_ids),
            load_options=[selectinload(Track.album)],
        )
        return {
            t.track_id: (
                t.recorded_year
                or (t.album.release_year if t.album else None)
            )
            for t in tracks
        }

    def _album_track_map(self, album_ids: set[int]) -> dict[int, list[int]]:
        """One query for every matched album's tracks, instead of one query
        per ChartEntry row (an album charts on the same weekly row many
        times over its run)."""
        if not album_ids:
            return {}
        tracks = self.controller.get.get_all_entities(
            "Track", album_id__in=list(album_ids)
        )
        mapping: dict[int, list[int]] = defaultdict(list)
        for track in tracks:
            mapping[track.album_id].append(track.track_id)
        return mapping

    # ------------------------------------------------------------------
    # Tree build
    # ------------------------------------------------------------------

    def _build_chart_tree(
        self, chart, year_tracks: dict[int, set[int]], stats: ChartPlaylistStats
    ) -> None:
        root = self._find_or_create(
            marker=_root_marker(chart.chart_key),
            name=chart.chart_name,
            parent_id=None,
            stats=stats,
        )
        if root is None:
            logger.error(f"Could not create/find root playlist for chart {chart.chart_key}")
            return

        decade_tracks: dict[int, set[int]] = defaultdict(set)
        for year, track_ids in year_tracks.items():
            decade_tracks[year - (year % 10)].update(track_ids)

        decade_playlist_ids: dict[int, int] = {}
        for decade in sorted(decade_tracks):
            decade_playlist = self._find_or_create(
                marker=_decade_marker(chart.chart_key, decade),
                name=f"{decade}s",
                parent_id=root.playlist_id,
                stats=stats,
            )
            if decade_playlist is None:
                continue
            decade_playlist_ids[decade] = decade_playlist.playlist_id

        for year in sorted(year_tracks):
            decade = year - (year % 10)
            decade_playlist_id = decade_playlist_ids.get(decade)
            if decade_playlist_id is None:
                continue
            year_playlist = self._find_or_create(
                marker=_year_marker(chart.chart_key, year),
                name=str(year),
                parent_id=decade_playlist_id,
                stats=stats,
            )
            if year_playlist is None:
                continue
            self._sync(year_playlist.playlist_id, year_tracks[year], stats)

        for decade, playlist_id in decade_playlist_ids.items():
            self._sync(playlist_id, decade_tracks[decade], stats)

        root_tracks: set[int] = set()
        for track_ids in decade_tracks.values():
            root_tracks.update(track_ids)
        self._sync(root.playlist_id, root_tracks, stats)

    # ------------------------------------------------------------------
    # Find-or-create / sync helpers
    # ------------------------------------------------------------------

    def _find_or_create(self, marker: str, name: str, parent_id, stats: ChartPlaylistStats):
        existing = self.controller.get.get_entity_object(
            "Playlist", playlist_description__eq=marker
        )
        if existing:
            return existing

        created = self.controller.add.add_entity(
            "Playlist",
            playlist_name=name,
            parent_id=parent_id,
            playlist_description=marker,
        )
        if created is not None:
            stats.playlists_created += 1
        return created

    def _sync(self, playlist_id: int, track_ids: set[int], stats: ChartPlaylistStats) -> None:
        result = sync_playlist_tracks(self.controller, playlist_id, track_ids)
        if result is None:
            return
        if result.added or result.removed:
            stats.playlists_updated += 1
        stats.tracks_added += result.added
        stats.tracks_removed += result.removed
