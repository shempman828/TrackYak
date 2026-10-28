"""Build and update the chart -> decade -> year playlist trees from matched ChartEntry data."""

# Every level holds real PlaylistTracks rows (docs/specs/chart_playlists.md).
# Nodes are found by a marker in Playlist.playlist_description, not by name, so a
# rename never duplicates a node and an unmarked user playlist is never touched.

from collections import defaultdict
from collections.abc import Callable, Iterable

from sqlalchemy.orm import selectinload

from src.db.db_tables.track import Track
from src.foundation.logger_config import logger
from src.playlist.playlist_track_sync import sync_playlist_tracks

# Public: charts_view.py checks for this prefix to tell whether a tree exists.
CHART_PLAYLIST_MARKER_PREFIX = "__chart_playlist__"

# A track only goes in a year playlist if it came out near that year, which keeps
# catalog re-entries (a 60s song charting again in 2010) out. Wider than
# chart_matching's _YEAR_SLACK: release metadata can trail the chart week by two years.
_YEAR_TOLERANCE = 2


def _root_marker(chart_key: str) -> str:
    """Return the marker for a chart's root playlist."""
    return f"{CHART_PLAYLIST_MARKER_PREFIX}:root:{chart_key}"


def _decade_marker(chart_key: str, decade: int) -> str:
    """Return the marker for a chart's decade playlist."""
    return f"{CHART_PLAYLIST_MARKER_PREFIX}:decade:{chart_key}:{decade}"


def _year_marker(chart_key: str, year: int) -> str:
    """Return the marker for a chart's year playlist."""
    return f"{CHART_PLAYLIST_MARKER_PREFIX}:year:{chart_key}:{year}"


class ChartPlaylistStats:
    """Summary of one generate_or_update() run, for the completion message."""

    def __init__(self):
        self.playlists_created = 0
        self.playlists_updated = 0
        self.playlists_removed = 0
        self.tracks_added = 0
        self.tracks_removed = 0

    def __repr__(self):
        return (
            f"ChartPlaylistStats(created={self.playlists_created}, "
            f"updated={self.playlists_updated}, removed={self.playlists_removed}, "
            f"tracks_added={self.tracks_added}, "
            f"tracks_removed={self.tracks_removed})"
        )


class ChartPlaylistBuilder:
    """Builds/updates the chart-derived playlist trees for every Chart row."""

    def __init__(self, controller):
        self.controller = controller

    def generate_or_update(self, progress_callback: Callable[[int, int], None] | None = None) -> ChartPlaylistStats:
        """Build or update the playlist tree of every Chart row."""
        stats = ChartPlaylistStats()
        charts = self.controller.get.get_all_entities("Chart")

        # Collect first so progress reporting has an accurate total.
        chart_year_tracks = [(chart, self._collect_year_tracks(chart)) for chart in charts]
        total_years = sum(len(yt) for _, yt in chart_year_tracks)
        done = 0

        live_markers: set[str] = set()
        for chart, year_tracks in chart_year_tracks:
            live_markers |= self._build_chart_tree(chart, year_tracks, stats)
            done += len(year_tracks)
            if progress_callback:
                progress_callback(done, total_years)

        self._prune_stale_playlists(live_markers, stats)
        return stats

    # ------------------------------------------------------------------
    # Data collection
    # ------------------------------------------------------------------

    def _collect_year_tracks(self, chart) -> dict[int, set[int]]:
        """Return {chart year: track_ids} for this chart's matched entries within _YEAR_TOLERANCE."""
        entries = self.controller.get.get_all_entities("ChartEntry", chart_id__eq=chart.chart_id, entity_id__notnull=True)

        track_entity_ids = {e.entity_id for e in entries if e.entity_type == "Track"}
        album_ids = {e.entity_id for e in entries if e.entity_type == "Album"}
        album_track_map = self._album_track_map(album_ids)

        # entity_id is a manual polymorphic link, not a ForeignKey, so a matched
        # track may since be deleted; _track_year_map omits it, so it is skipped
        # below instead of failing the whole insert on a FOREIGN KEY error.
        expanded_track_ids = {tid for tids in album_track_map.values() for tid in tids}
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
        """Return {track_id: recorded year, else album release year} for tracks that still exist."""
        if not track_ids:
            return {}
        tracks = self.controller.get.get_all_entities("Track", track_id__in=list(track_ids), load_options=[selectinload(Track.album)])
        return {t.track_id: (t.recorded_year or (t.album.release_year if t.album else None)) for t in tracks}

    def _album_track_map(self, album_ids: set[int]) -> dict[int, list[int]]:
        """Return {album_id: track_ids} for the matched albums in one query."""
        if not album_ids:
            return {}
        tracks = self.controller.get.get_all_entities("Track", album_id__in=list(album_ids))
        mapping: dict[int, list[int]] = defaultdict(list)
        for track in tracks:
            mapping[track.album_id].append(track.track_id)
        return mapping

    # ------------------------------------------------------------------
    # Tree build
    # ------------------------------------------------------------------

    def _build_chart_tree(self, chart, year_tracks: dict[int, set[int]], stats: ChartPlaylistStats) -> set[str]:
        """Build or update this chart's tree and return the markers of the nodes that still have content."""
        decade_tracks: dict[int, set[int]] = defaultdict(set)
        for year, track_ids in year_tracks.items():
            decade_tracks[year - (year % 10)].update(track_ids)

        live_markers = {_root_marker(chart.chart_key)}
        live_markers.update(_decade_marker(chart.chart_key, decade) for decade in decade_tracks)
        live_markers.update(_year_marker(chart.chart_key, year) for year in year_tracks)

        root = self._find_or_create(marker=_root_marker(chart.chart_key), name=chart.chart_name, parent_id=None, stats=stats)
        if root is None:
            logger.error(f"Could not create/find root playlist for chart {chart.chart_key}")
            return live_markers

        decade_playlist_ids: dict[int, int] = {}
        for decade in sorted(decade_tracks):
            decade_playlist = self._find_or_create(marker=_decade_marker(chart.chart_key, decade), name=f"{decade}s", parent_id=root.playlist_id, stats=stats)
            if decade_playlist is None:
                continue
            decade_playlist_ids[decade] = decade_playlist.playlist_id

        for year in sorted(year_tracks):
            decade = year - (year % 10)
            decade_playlist_id = decade_playlist_ids.get(decade)
            if decade_playlist_id is None:
                continue
            year_playlist = self._find_or_create(marker=_year_marker(chart.chart_key, year), name=str(year), parent_id=decade_playlist_id, stats=stats)
            if year_playlist is None:
                continue
            self._sync(year_playlist.playlist_id, year_tracks[year], stats)

        for decade, playlist_id in decade_playlist_ids.items():
            self._sync(playlist_id, decade_tracks[decade], stats)

        root_tracks: set[int] = set()
        for track_ids in decade_tracks.values():
            root_tracks.update(track_ids)
        self._sync(root.playlist_id, root_tracks, stats)

        return live_markers

    # ------------------------------------------------------------------
    # Stale-node pruning
    # ------------------------------------------------------------------

    def _prune_stale_playlists(self, live_markers: set[str], stats: ChartPlaylistStats) -> None:
        """Empty, and delete when childless, generated playlists whose year or decade no longer has tracks."""
        # A user playlist nested under a stale node counts as a child and keeps that branch.
        generated = self.controller.get.get_all_entities("Playlist", playlist_description__startswith=CHART_PLAYLIST_MARKER_PREFIX)
        stale = [p for p in generated if p.playlist_description not in live_markers]
        if not stale:
            return

        # 1. Empty every stale node -- this is what actually removes a track
        #    from, say, the 1964 playlist once its match is cleared.
        stale_ids = []
        for playlist in stale:
            playlist_id = playlist.playlist_id
            stale_ids.append((playlist_id, playlist.playlist_description))
            self._sync(playlist_id, set(), stats)

        # 2. Delete the emptied nodes, leaves first (year -> decade -> root)
        #    so a stale decade/root becomes childless once its generated
        #    children are gone.
        depth = {"year": 0, "decade": 1, "root": 2}

        def _kind(marker: str) -> str:
            parts = marker.split(":")
            return parts[1] if len(parts) > 1 else ""

        for playlist_id, _marker in sorted(stale_ids, key=lambda pair: depth.get(_kind(pair[1]), 9)):
            has_child = self.controller.get.get_entity_object("Playlist", parent_id__eq=playlist_id)
            if has_child is not None:
                continue
            if self.controller.delete.delete_entity("Playlist", entity_id=playlist_id):
                stats.playlists_removed += 1

    # ------------------------------------------------------------------
    # Find-or-create / sync helpers
    # ------------------------------------------------------------------

    def _find_or_create(self, marker: str, name: str, parent_id, stats: ChartPlaylistStats):
        """Return the playlist that carries marker, creating it if absent."""
        existing = self.controller.get.get_entity_object("Playlist", playlist_description__eq=marker)
        if existing:
            return existing

        created = self.controller.add.add_entity("Playlist", playlist_name=name, parent_id=parent_id, playlist_description=marker)
        if created is not None:
            stats.playlists_created += 1
        return created

    def _sync(self, playlist_id: int, track_ids: set[int], stats: ChartPlaylistStats) -> None:
        """Make playlist_id hold exactly track_ids and add the changes to stats."""
        result = sync_playlist_tracks(self.controller, playlist_id, track_ids)
        if result is None:
            return
        if result.added or result.removed:
            stats.playlists_updated += 1
        stats.tracks_added += result.added
        stats.tracks_removed += result.removed
