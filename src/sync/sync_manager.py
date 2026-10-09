"""SyncManager: copy playlists/moods to a local folder or an MTP device, with one result-dict shape for both."""

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import shutil

from sqlalchemy import case, func, select, union
from sqlalchemy.orm import selectinload

from src.db.db_helpers import GetFromDB
from src.db.db_tables import Album, AlbumRoleAssociation, MoodTrackAssociation, PlaylistTracks, Track, TrackArtistRole
from src.foundation.logger_config import logger
from src.sync.mtp_manager import MtpDevice, MtpManager
from src.sync.sync_naming import predicted_device_filename, safe_filename, safe_playlist_name
from src.sync.sync_prune import SyncPruneMixin
from src.sync.transcode import LOSSLESS_EXTENSIONS, TranscodeCache, TranscodeError, ffmpeg_available, is_lossless_path

# Post-copy verification retries this many times (so up to _MAX_RETRIES + 1 copy attempts).
_MAX_RETRIES = 2

# MD5 duplicate confirmation is disk-bound; a small pool overlaps it.
_DUPLICATE_CHECK_WORKERS = 8

# One single-threaded ffmpeg per track; capped so a background sync doesn't take every core.
_TRANSCODE_WORKERS = max(2, min(4, os.cpu_count() or 2))

# Track.file_extension values that count as lossless, with and without a leading dot.
_LOSSLESS_EXTS_LOWER = {e.lower() for e in LOSSLESS_EXTENSIONS} | {e.lower().lstrip(".") for e in LOSSLESS_EXTENSIONS}


@dataclass
class _Transport:
    """Destination-specific operations that _sync_item drives (local folder or MTP device)."""

    verb: str  # "copied" / "sent", for the result message
    progress_label: str
    music_subpath: str  # M3U path from the playlists folder back to the music folder
    diff: Callable[[list[dict], list[dict]], tuple[list[dict], list[dict]]]
    copy_one: Callable[[dict], bool]
    list_existing: Callable[[], dict[str, int]] | None  # None: the destination can't be listed
    write_m3u: Callable[[str, str], bool]  # (safe playlist name, content) -> ok


class SyncManager(SyncPruneMixin):
    """All sync logic: DB lookups, transcode, diff, copy-with-verify, M3U writing, and prune."""

    def __init__(self, db_session):
        self.session = db_session
        self.get_db = GetFromDB(db_session)
        self.mtp = MtpManager()
        self.transcode_cache = TranscodeCache()
        # Device filename -> track identity handled so far this run; a track shared by several
        # selected playlists/moods is processed once and only added to the later M3Us.
        self._run_synced_names: dict[str, object] = {}

    def begin_sync_run(self) -> None:
        """Clear the per-run cross-playlist dedup state."""
        self._run_synced_names = {}

    @staticmethod
    def _track_identity(track: dict):
        """A key that tells two different tracks apart (track_id, else the source path)."""
        track_id = track.get("track_id")
        return track_id if track_id is not None else track.get("file_path")

    def _split_run_duplicates(self, tracks: list[dict], transcode_active: bool, failures: list[dict] | None = None) -> tuple[list[dict], list[dict]]:
        """Partition `tracks` into (already synced this run, fresh); name collisions go to `failures`."""
        already_synced: list[dict] = []
        fresh: list[dict] = []
        claimed: dict[str, object] = {}
        for track in tracks:
            if self._unusable_source_reason(track):
                fresh.append(track)  # the diff pass records the real reason
                continue
            name = predicted_device_filename(track, transcode_active)
            identity = self._track_identity(track)
            owner = self._run_synced_names.get(name, claimed.get(name))
            if owner is None or owner == identity:
                if name in self._run_synced_names:
                    track["device_filename"] = name
                    track["copied_successfully"] = True
                    already_synced.append(track)
                else:
                    claimed[name] = identity
                    fresh.append(track)
                continue
            # Two different tracks map to one 'Artist - Title.ext': the second would overwrite the first.
            logger.warning(f"Device filename collision: {name}")
            self._record_failure(failures, track, f"another track already uses the file name '{name}'")
        return already_synced, fresh

    def _remember_synced(self, tracks: list[dict]) -> None:
        """Record the device filenames confirmed on the device this run."""
        for track in tracks:
            name = track.get("device_filename")
            if name:
                self._run_synced_names[name] = self._track_identity(track)

    # ------------------------------------------------------------------
    # Database helpers
    # ------------------------------------------------------------------

    def _selection_aggregates(self, membership_model, id_column) -> dict:
        """{entity_id: (track_count, size_bytes, lossless_bytes, lossless_seconds)} in one grouped query."""
        # Lossless buckets only cover tracks with a known duration (needed for the MP3 size estimate).
        lossless = func.lower(func.coalesce(Track.file_extension, "")).in_(_LOSSLESS_EXTS_LOWER) & Track.duration.isnot(None)
        rows = self.session.execute(
            select(
                id_column,
                func.count(),
                func.coalesce(func.sum(Track.file_size), 0),
                func.coalesce(func.sum(case((lossless, Track.file_size), else_=0)), 0),
                func.coalesce(func.sum(case((lossless, Track.duration), else_=0.0)), 0.0),
            )
            .join(Track, Track.track_id == membership_model.track_id)
            .group_by(id_column)
        ).all()
        return {r[0]: (int(r[1]), r[2], r[3], float(r[4])) for r in rows}

    def selection_totals(self, playlist_ids: list[int], mood_ids: list[int]) -> tuple[int, int, int, float]:
        """(track_count, size, lossless_bytes, lossless_seconds) over the de-duplicated union of a selection."""
        if not playlist_ids and not mood_ids:
            return (0, 0, 0, 0.0)
        track_id_queries = []
        if playlist_ids:
            track_id_queries.append(select(PlaylistTracks.track_id).where(PlaylistTracks.playlist_id.in_(playlist_ids)))
        if mood_ids:
            track_id_queries.append(select(MoodTrackAssociation.track_id).where(MoodTrackAssociation.mood_id.in_(mood_ids)))
        distinct_track_ids = union(*track_id_queries).subquery()
        lossless = func.lower(func.coalesce(Track.file_extension, "")).in_(_LOSSLESS_EXTS_LOWER) & Track.duration.isnot(None)
        row = self.session.execute(
            select(
                func.count(),
                func.coalesce(func.sum(Track.file_size), 0),
                func.coalesce(func.sum(case((lossless, Track.file_size), else_=0)), 0),
                func.coalesce(func.sum(case((lossless, Track.duration), else_=0.0)), 0.0),
            ).where(Track.track_id.in_(select(distinct_track_ids.c.track_id)))
        ).one()
        return (int(row[0]), row[1], row[2], float(row[3]))

    def get_playlists(self) -> list[dict]:
        """Every playlist as a selection-tree dict with its track/size aggregates."""
        aggregates = self._selection_aggregates(PlaylistTracks, PlaylistTracks.playlist_id)
        result = []
        for pl in self.get_db.get_all_entities("Playlist"):
            track_count, size, lossless_size, lossless_duration = aggregates.get(pl.playlist_id, (0, 0, 0, 0.0))
            result.append(
                {
                    "kind": "playlist",
                    "playlist_id": pl.playlist_id,
                    "name": pl.playlist_name,
                    "description": pl.playlist_description,
                    "track_count": track_count,
                    "size": size,
                    "lossless_size": lossless_size,
                    "lossless_duration": lossless_duration,
                    "is_smart": pl.is_smart,
                    "parent_id": pl.parent_id,
                }
            )
        return result

    def get_moods(self) -> list[dict]:
        """Every mood as a selection-tree dict with its track/size aggregates."""
        aggregates = self._selection_aggregates(MoodTrackAssociation, MoodTrackAssociation.mood_id)
        result = []
        for mood in self.get_db.get_all_entities("Mood"):
            track_count, size, lossless_size, lossless_duration = aggregates.get(mood.mood_id, (0, 0, 0, 0.0))
            result.append(
                {
                    "kind": "mood",
                    "mood_id": mood.mood_id,
                    "name": mood.mood_name,
                    "description": mood.mood_description,
                    "track_count": track_count,
                    "size": size,
                    "lossless_size": lossless_size,
                    "lossless_duration": lossless_duration,
                    "parent_id": mood.parent_id,
                }
            )
        return result

    @staticmethod
    def _filename_artist(track) -> str:
        """The artist for the on-device filename: album artist, else joined primary artists, else 'Various Artists'."""
        album = getattr(track, "album", None)
        if album is not None:
            album_names = [assoc.credited_name.strip() for assoc in album.album_roles if assoc.role and assoc.role.role_name == "Album Artist" and assoc.credited_name and assoc.credited_name.strip()]
            if album_names:
                return " & ".join(album_names)
        primary = track.primary_artists
        if primary:
            return " & ".join(a.artist_name for a in primary)
        return "Various Artists"

    def _track_to_dict(self, track) -> dict:
        """The plain dict the sync passes work on."""
        return {"track_id": track.track_id, "file_path": track.track_file_path, "title": track.track_name, "artist": self._filename_artist(track), "duration": track.duration}

    @staticmethod
    def _track_load_options(track_rel):
        """Eager-load options for everything _track_to_dict touches, so a listing is a few queries, not N."""
        album_roles = selectinload(track_rel).selectinload(Track.album).selectinload(Album.album_roles)
        return [
            selectinload(track_rel).selectinload(Track.artist_roles).selectinload(TrackArtistRole.artist),
            selectinload(track_rel).selectinload(Track.artist_roles).selectinload(TrackArtistRole.role),
            album_roles.selectinload(AlbumRoleAssociation.artist),
            album_roles.selectinload(AlbumRoleAssociation.role),
            album_roles.selectinload(AlbumRoleAssociation.credited_alias),  # read by credited_name
        ]

    def get_playlist_tracks(self, playlist_id: int) -> list[dict]:
        """A playlist's tracks in playlist order."""
        rows = self.get_db.get_all_entities("PlaylistTracks", playlist_id=playlist_id, load_options=self._track_load_options(PlaylistTracks.track))
        rows = sorted(rows, key=lambda pt: pt.position if pt.position is not None else 0)
        # A dangling association (track row gone) is skipped instead of aborting the run.
        return [self._track_to_dict(pt.track) for pt in rows if pt.track is not None]

    def get_mood_tracks(self, mood_id: int) -> list[dict]:
        """A mood's tracks."""
        rows = self.get_db.get_all_entities("MoodTrackAssociation", mood_id=mood_id, load_options=self._track_load_options(MoodTrackAssociation.track))
        return [self._track_to_dict(assoc.track) for assoc in rows if assoc.track is not None]

    def get_item_tracks(self, item_data: dict) -> list[dict]:
        """Dispatch to the right track lookup based on item_data['kind']."""
        if item_data.get("kind") == "mood":
            return self.get_mood_tracks(item_data["mood_id"])
        return self.get_playlist_tracks(item_data["playlist_id"])

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _unusable_source_reason(track: dict) -> str | None:
        """Why this track's source file can't be read, or None if it's fine."""
        path = track.get("file_path")
        if not path:
            return "no source file on record"
        if not Path(path).exists():
            return "source file not found"
        return None

    @staticmethod
    def _record_failure(failures: list[dict] | None, track: dict, reason: str) -> None:
        """Append a {title, artist, reason} entry for the Activity page."""
        if failures is None:
            return
        failures.append({"title": track.get("title", "?"), "artist": track.get("artist", "?"), "reason": reason})

    @staticmethod
    def _empty_result(playlist_name: str, message: str) -> dict:
        """A zeroed result dict for the early exits."""
        return {
            "playlist_name": playlist_name,
            "success": False,
            "message": message,
            "tracks_copied": 0,
            "tracks_skipped": 0,
            "tracks_failed": 0,
            "tracks_transcoded": 0,
            "total_tracks": 0,
            "failures": [],
        }

    @staticmethod
    def _effective_source(track: dict) -> str:
        """The file actually copied: the transcoded MP3 if one was made, else the library original."""
        return track.get("sync_source_path") or track["file_path"]

    @classmethod
    def _expected_size(cls, track: dict) -> int | None:
        """Size the copied file must have on the destination, or None if the source can't be read."""
        try:
            return Path(cls._effective_source(track)).stat().st_size
        except OSError:
            return None

    def _transcode_candidates(self, tracks: list[dict]) -> list[dict]:
        """Tracks with a readable lossless source."""
        # A missing source is left for the diff pass to record with the real reason.
        return [t for t in tracks if t.get("file_path") and is_lossless_path(t["file_path"]) and not self._unusable_source_reason(t)]

    def _prepare_transcodes(
        self, tracks: list[dict], failures: list[dict] | None, progress_callback=None, progress_total: int = 0, should_cancel: Callable[[], bool] | None = None, bitrate: str = "320k"
    ) -> None:
        """Encode every lossless track to a cached MP3 in parallel and point track['sync_source_path'] at it."""
        # Failed/cancelled encodes are flagged '_transcode_skipped' so the diff pass ignores them.
        pending = self._transcode_candidates(tracks)
        if not pending:
            return

        def _encode(track: dict) -> tuple[dict, str, str | None]:
            """Run one encode off-thread; returns (track, 'ok'|'cancelled'|'failed', cache path or error)."""
            if should_cancel and should_cancel():
                return track, "cancelled", None
            try:
                path = str(self.transcode_cache.get_or_create(track["file_path"], bitrate))
                return track, "ok", path
            except (TranscodeError, OSError) as e:
                return track, "failed", str(e)

        done = 0
        # Bookkeeping stays on this thread; only the ffmpeg calls overlap.
        with ThreadPoolExecutor(max_workers=min(_TRANSCODE_WORKERS, len(pending))) as pool:
            futures = [pool.submit(_encode, t) for t in pending]
            for future in as_completed(futures):
                track, outcome, detail = future.result()
                done += 1
                if progress_callback:
                    progress_callback(done, progress_total, f"Converting to MP3: {track['title']}")
                if outcome == "ok":
                    track["sync_source_path"] = detail
                elif outcome == "cancelled":
                    track["_transcode_skipped"] = True
                    self._record_failure(failures, track, "cancelled before it was copied")
                else:
                    track["_transcode_skipped"] = True
                    logger.error(f"Transcode failed for {track['file_path']}: {detail}")
                    self._record_failure(failures, track, f"could not convert to MP3: {detail}")

    def _file_md5(self, file_path: str, chunk_size: int = 65536) -> str:
        """MD5 hex digest of a local file ('' if unreadable)."""
        md5 = hashlib.md5()
        try:
            with Path(file_path).open("rb") as f:
                while chunk := f.read(chunk_size):
                    md5.update(chunk)
            return md5.hexdigest()
        except OSError as e:
            logger.warning(f"MD5 failed for {file_path}: {e}")
            return ""

    def _is_local_duplicate(self, source_path: str, dest_path: str) -> bool:
        """True if dest_path already holds an identical copy of source_path (size, then MD5)."""
        if not Path(dest_path).exists():
            return False
        try:
            if Path(source_path).stat().st_size != Path(dest_path).stat().st_size:
                return False
            return self._file_md5(source_path) == self._file_md5(dest_path)
        except OSError:
            return False

    def _list_local_pool(self, music_dir: str) -> dict[str, int]:
        """{filename: size} for every file in music_dir, from one directory scan."""
        existing: dict[str, int] = {}
        try:
            with os.scandir(music_dir) as it:
                for entry in it:
                    if entry.is_file():
                        try:
                            existing[entry.name] = entry.stat().st_size
                        except OSError:
                            continue
        except OSError:
            pass
        return existing

    def _accept_for_diff(self, track: dict, failures: list[dict] | None) -> tuple[str, int | None] | None:
        """Set track['device_filename'] and return (name, source size); None if the track can't be synced."""
        if track.get("_transcode_skipped"):
            return None  # already in `failures`
        reason = self._unusable_source_reason(track)
        if reason:
            logger.warning(f"{reason}: {track.get('file_path')}")
            self._record_failure(failures, track, reason)
            return None
        source = self._effective_source(track)
        device_filename = safe_filename(track["artist"], track["title"], Path(source).suffix)
        track["device_filename"] = device_filename
        return device_filename, self._expected_size(track)

    def _diff_local_pool(self, tracks: list[dict], music_dir, failures: list[dict] | None = None) -> tuple[list[dict], list[dict]]:
        """Partition tracks into (to_copy, to_skip) from one folder scan; name+size matches confirmed by MD5."""
        existing = self._list_local_pool(str(music_dir))
        to_copy: list[dict] = []
        md5_candidates: list[tuple[dict, str]] = []

        for track in tracks:
            accepted = self._accept_for_diff(track, failures)
            if accepted is None:
                continue
            device_filename, source_size = accepted
            if source_size is not None and existing.get(device_filename) == source_size:
                md5_candidates.append((track, str(Path(music_dir) / device_filename)))
            else:
                to_copy.append(track)

        to_skip: list[dict] = []
        if md5_candidates:
            with ThreadPoolExecutor(max_workers=_DUPLICATE_CHECK_WORKERS) as pool:
                futures = {pool.submit(self._is_local_duplicate, self._effective_source(t), dest): t for t, dest in md5_candidates}
                for future in as_completed(futures):
                    track = futures[future]
                    (to_skip if future.result() else to_copy).append(track)

        return to_copy, to_skip

    def _diff_mtp_pool(self, tracks: list[dict], device: MtpDevice, music_dir_uri: str, failures: list[dict] | None = None) -> tuple[list[dict], list[dict]]:
        """Partition tracks into (to_copy, to_skip) from one remote listing, by name + size."""
        existing = self.mtp.list_remote_dir(device, music_dir_uri)
        to_copy: list[dict] = []
        to_skip: list[dict] = []

        for track in tracks:
            accepted = self._accept_for_diff(track, failures)
            if accepted is None:
                continue
            device_filename, source_size = accepted
            if source_size is not None and existing.get(device_filename) == source_size:
                to_skip.append(track)
            else:
                to_copy.append(track)

        return to_copy, to_skip

    def _copy_with_retry(
        self,
        tracks: list[dict],
        copy_one: Callable[[dict], bool],
        list_existing: Callable[[], dict[str, int]] | None,
        expected_size: Callable[[dict], int | None],
        progress_callback=None,
        progress_total: int = 0,
        progress_label: str = "Copying",
        should_cancel: Callable[[], bool] | None = None,
    ) -> tuple[list[dict], list[dict]]:
        """Copy `tracks`, verify each by re-listing the destination, and retry failures; returns (succeeded, failed)."""
        # A transport's "success" isn't trusted (MTP can drop or truncate silently) unless the destination
        # can't be listed at all (list_existing None, e.g. aft), where the copy result is all there is.
        # On cancel, the loop stops before the next track; the in-flight copy still finishes.
        remaining = tracks
        succeeded: list[dict] = []
        cancelled = False

        for attempt in range(_MAX_RETRIES + 1):
            if not remaining or cancelled:
                break
            label = progress_label if attempt == 0 else f"Retrying ({attempt + 1}/{_MAX_RETRIES + 1})"
            for i, track in enumerate(remaining):
                if should_cancel and should_cancel():
                    cancelled = True
                    break
                if progress_callback:
                    progress_callback(i, progress_total, f"{label}: {track['title']}")
                track["_last_copy_ok"] = copy_one(track)

            existing = list_existing() if list_existing is not None else None
            still_failed = []
            for track in remaining:
                if existing is None:
                    verified = bool(track.get("_last_copy_ok"))
                else:
                    size = existing.get(track["device_filename"])
                    expected = expected_size(track)
                    verified = size is not None and expected is not None and size == expected
                if verified:
                    track["copied_successfully"] = True
                    succeeded.append(track)
                else:
                    still_failed.append(track)
            remaining = still_failed
            if remaining and not cancelled and attempt < _MAX_RETRIES:
                logger.warning(f"{len(remaining)} track(s) failed verification, retrying (attempt {attempt + 2}/{_MAX_RETRIES + 1})")

        for track in remaining:
            track["copied_successfully"] = False
            if cancelled:
                logger.info(f"Sync cancelled before copying: {track.get('device_filename')}")
                track["failure_reason"] = "cancelled before it was copied"
            elif track.get("_last_copy_ok"):
                logger.error(f"Failed to sync after retries: {track.get('device_filename')}")
                track["failure_reason"] = f"copied but never verified on the destination after {_MAX_RETRIES + 1} attempts (truncated or rejected by the device)"
            else:
                logger.error(f"Failed to sync after retries: {track.get('device_filename')}")
                track["failure_reason"] = f"copy failed after {_MAX_RETRIES + 1} attempts (transport error)"

        return succeeded, remaining

    @staticmethod
    def _build_m3u_content(tracks: list[dict], music_subpath: str) -> str:
        """M3U text for the successfully synced tracks, in the source order of `tracks`."""
        lines = ["#EXTM3U"]
        for track in tracks:
            if not track.get("copied_successfully", False):
                continue
            duration = track.get("duration") or 0
            lines.append(f"#EXTINF:{int(duration)},{track['artist']} - {track['title']}")
            lines.append(f"{music_subpath}/{track['device_filename']}")
        return "\n".join(lines) + "\n"

    def _sync_item(
        self, playlist_data: dict, transport: _Transport, progress_callback=None, should_cancel: Callable[[], bool] | None = None, transcode_to_mp3: bool = False, transcode_bitrate: str = "320k"
    ) -> dict:
        """Sync one playlist/mood through `transport` and return its result dict."""
        playlist_name = playlist_data["name"]
        tracks = self.get_item_tracks(playlist_data)
        if not tracks:
            return self._empty_result(playlist_name, "Playlist is empty")

        total_tracks = len(tracks)
        failures: list[dict] = []
        ffmpeg_missing = transcode_to_mp3 and not ffmpeg_available()
        transcode_active = transcode_to_mp3 and not ffmpeg_missing
        already_synced, fresh = self._split_run_duplicates(tracks, transcode_active, failures)

        # Progress units: one per encode, then one per track, so the bar only moves forward.
        n_convert = len(self._transcode_candidates(fresh)) if transcode_active else 0
        units = n_convert + total_tracks
        if transcode_active:
            self._prepare_transcodes(fresh, failures, progress_callback, units, should_cancel, transcode_bitrate)
        to_copy, to_skip = transport.diff(fresh, failures)
        for track in to_skip:
            track["copied_successfully"] = True

        offset = units - len(to_copy)
        copy_progress = (lambda i, _total, msg: progress_callback(offset + i, units, msg)) if progress_callback else None
        succeeded, failed = self._copy_with_retry(
            to_copy, transport.copy_one, transport.list_existing, self._expected_size, copy_progress, progress_total=units, progress_label=transport.progress_label, should_cancel=should_cancel
        )
        if progress_callback:
            progress_callback(units, units, f"Finished: {playlist_name}")

        for track in failed:
            self._record_failure(failures, track, track.get("failure_reason", "unknown error"))
        self._remember_synced(to_skip + succeeded)

        tracks_copied = len(succeeded)
        tracks_skipped = len(to_skip) + len(already_synced)
        tracks_failed = len(failures)
        tracks_transcoded = sum(1 for t in succeeded if t.get("sync_source_path"))

        m3u_written = transport.write_m3u(safe_playlist_name(playlist_data), self._build_m3u_content(tracks, transport.music_subpath))

        message = f"{tracks_copied} {transport.verb}, {tracks_skipped} skipped"
        if tracks_transcoded:
            message += f", {tracks_transcoded} to MP3"
        if tracks_failed:
            message += f", {tracks_failed} failed"
        if not m3u_written:
            message += ", playlist file not written"
        if ffmpeg_missing:
            message += "  (ffmpeg not found — copied originals)"

        return {
            "playlist_name": playlist_name,
            "success": tracks_copied > 0 or tracks_skipped > 0,
            "message": message,
            "tracks_copied": tracks_copied,
            "tracks_skipped": tracks_skipped,
            "tracks_failed": tracks_failed,
            "tracks_transcoded": tracks_transcoded,
            "total_tracks": total_tracks,
            "failures": failures,
            "m3u_written": m3u_written,
        }

    # ------------------------------------------------------------------
    # Folder sync
    # ------------------------------------------------------------------

    def copy_track(self, source_path: str, dest_path: str) -> bool:
        """Copy one track to dest_path (duplicates are already filtered by the diff pass)."""
        try:
            if not Path(source_path).exists():
                logger.error(f"Source file not found: {source_path}")
                return False
            Path(dest_path).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, dest_path)
            logger.debug(f"Copied: {source_path} → {dest_path}")
            return True
        except OSError as e:
            logger.error(f"Error copying {source_path}: {e}")
            return False

    def clear_device_folder(self, device_path: str) -> list[str]:
        """Remove music/ and playlists/ before a fresh folder sync; returns error messages (empty on success)."""
        errors = []
        for subdir in ("music", "playlists"):
            target = Path(device_path) / subdir
            if not target.exists():
                continue
            try:
                shutil.rmtree(target)
                logger.info(f"Cleared folder: {target}")
            except OSError as e:
                logger.error(f"Could not clear {target}: {e}")
                errors.append(f"Could not clear {target}: {e}")
        return errors

    @staticmethod
    def _write_local_m3u(m3u_path: Path, content: str) -> bool:
        """Write one M3U file; True on success."""
        try:
            m3u_path.write_text(content, encoding="utf-8")
            return True
        except OSError as e:
            logger.error(f"Failed to write M3U {m3u_path}: {e}")
            return False

    def sync_playlist_to_device(self, playlist_data: dict, device_path: str, progress_callback=None, should_cancel=None, transcode_to_mp3: bool = False, transcode_bitrate: str = "320k") -> dict:
        """Sync one playlist or mood to a local folder (music/ + playlists/)."""
        music_dir = Path(device_path) / "music"
        playlists_dir = Path(device_path) / "playlists"
        try:
            music_dir.mkdir(parents=True, exist_ok=True)
            playlists_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            logger.error(f"Cannot create sync folders in {device_path}: {e}")
            return self._empty_result(playlist_data["name"], f"Destination folder is not writable: {e}")

        transport = _Transport(
            verb="copied",
            progress_label="Copying",
            music_subpath="../music",  # must match the real folder name: ext4 is case-sensitive
            diff=lambda tracks, failures: self._diff_local_pool(tracks, music_dir, failures),
            copy_one=lambda t: self.copy_track(self._effective_source(t), str(music_dir / t["device_filename"])),
            list_existing=lambda: self._list_local_pool(str(music_dir)),
            write_m3u=lambda name, content: self._write_local_m3u(playlists_dir / f"{name}.m3u", content),
        )
        return self._sync_item(playlist_data, transport, progress_callback, should_cancel, transcode_to_mp3, transcode_bitrate)

    # ------------------------------------------------------------------
    # MTP sync
    # ------------------------------------------------------------------

    def _get_mtp_device(self, device_uri: str) -> MtpDevice | None:
        """The live, mounted MtpDevice for device_uri, or None if it isn't connected."""
        match = next((d for d in self.mtp.list_devices() if d.uri == device_uri), None)
        if match is None:
            logger.error(f"MTP device not found: {device_uri}")
            return None
        self.mtp.ensure_mounted(match)
        return match

    def clear_mtp_folders(self, device_uri: str, music_path: str) -> list[str]:
        """Delete the device's music and Playlists folders; returns error messages (empty on success)."""
        device = self._get_mtp_device(device_uri)
        if device is None:
            return ["Device not found — nothing was cleared"]
        errors = []
        for uri in (self.mtp.build_music_uri(device, music_path), self.mtp.build_playlists_dir_uri(device, music_path)):
            if self.mtp.remove_remote_dir(device, uri):
                logger.info(f"Cleared MTP folder: {uri}")
            else:
                errors.append(f"Could not clear {uri}")
        return errors

    def sync_playlist_to_mtp(
        self, playlist_data: dict, device_uri: str, music_path: str, progress_callback=None, should_cancel=None, transcode_to_mp3: bool = False, transcode_bitrate: str = "320k"
    ) -> dict:
        """Sync one playlist or mood to an MTP device (music folder + sibling Playlists folder)."""
        device = self._get_mtp_device(device_uri)
        if device is None:
            return self._empty_result(playlist_data["name"], "Device not found — is it plugged in with File Transfer selected?")

        music_dir_uri = self.mtp.build_music_uri(device, music_path)
        self.mtp.make_remote_dir(device, music_dir_uri)
        self.mtp.make_remote_dir(device, self.mtp.build_playlists_dir_uri(device, music_path))

        transport = _Transport(
            verb="sent",
            progress_label="Sending",
            music_subpath=self.mtp.m3u_music_subpath(music_path),
            diff=lambda tracks, failures: self._diff_mtp_pool(tracks, device, music_dir_uri, failures),
            copy_one=lambda t: self.mtp.copy_file(device, self._effective_source(t), music_dir_uri + t["device_filename"]),
            list_existing=(lambda: self.mtp.list_remote_dir(device, music_dir_uri)) if self.mtp.can_list(device) else None,
            write_m3u=lambda name, content: self.mtp.copy_text_as_file(device, content, self.mtp.build_playlist_uri(device, music_path, name)),
        )
        return self._sync_item(playlist_data, transport, progress_callback, should_cancel, transcode_to_mp3, transcode_bitrate)
