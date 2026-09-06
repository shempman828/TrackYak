"""
SyncManager — all sync logic in one place.

sync_playlist_to_device()  →  local folder copy (always available)
sync_playlist_to_mtp()     →  gio/aft MTP transfer to Android phone

Both return the same result-dict shape so SyncWorker and the UI are
completely agnostic about which path is running.
"""

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import os
from pathlib import Path
import shutil

from sqlalchemy.orm import selectinload

from src.db.db_helpers import GetFromDB
from src.db.db_tables import MoodTrackAssociation, PlaylistTracks, Track, TrackArtistRole
from src.foundation.logger_config import logger
from src.sync.mtp_manager import MtpDevice, MtpManager
from src.sync.transcode import (
    LOSSLESS_EXTENSIONS,
    TranscodeCache,
    TranscodeError,
    ffmpeg_available,
    is_lossless_path,
)

# Post-copy verification retries this many times before a track is
# reported as failed (so up to _MAX_RETRIES + 1 total copy attempts).
_MAX_RETRIES = 2

# Local duplicate confirmation (MD5) is disk I/O-bound, so run it across
# a small thread pool rather than sequentially in the diff pre-pass.
_DUPLICATE_CHECK_WORKERS = 8

# Extensions the prune pass is willing to delete from a device's music/
# folder. Anything else (a stray .zip, a folder, an extension-less file) is
# left alone even when it isn't in the desired set — see _is_prunable_music_name.
_LOSSY_EXTENSIONS = {".mp3", ".m4a", ".aac", ".ogg", ".oga", ".opus", ".wma", ".mp4"}
_PRUNABLE_AUDIO_EXTENSIONS = LOSSLESS_EXTENSIONS | _LOSSY_EXTENSIONS


class SyncManager:
    def __init__(self, db_session):
        self.session = db_session
        self.get_db = GetFromDB(db_session)
        self.mtp = MtpManager()
        self.transcode_cache = TranscodeCache()

    # ------------------------------------------------------------------
    # Database helpers
    # ------------------------------------------------------------------

    def get_playlists(self) -> list[dict]:
        playlists = self.get_db.get_all_entities("Playlist")
        return [
            {
                "kind": "playlist",
                "playlist_id": pl.playlist_id,
                "name": pl.playlist_name,
                "description": pl.playlist_description,
                "track_count": pl.track_count,
                "size": pl.playlist_size,
                "is_smart": pl.is_smart,
                "parent_id": pl.parent_id,
            }
            for pl in playlists
        ]

    def get_moods(self) -> list[dict]:
        moods = self.get_db.get_all_entities("Mood")
        return [
            {
                "kind": "mood",
                "mood_id": mood.mood_id,
                "name": mood.mood_name,
                "description": mood.mood_description,
                "track_count": mood.track_count,
                "size": mood.mood_size,
                "parent_id": mood.parent_id,
            }
            for mood in moods
        ]

    def _track_to_dict(self, track) -> dict:
        artists = track.primary_artists
        artist_name = "Various Artists"
        if artists:
            artist_name = " & ".join([a.artist_name for a in artists])
        return {
            "track_id": track.track_id,
            "file_path": track.track_file_path,
            "title": track.track_name,
            "artist": artist_name,
            "duration": track.duration,
        }

    def get_playlist_tracks(self, playlist_id: int) -> list[dict]:
        playlist_tracks = self.get_db.get_all_entities(
            "PlaylistTracks",
            playlist_id=playlist_id,
            load_options=[
                selectinload(PlaylistTracks.track)
                .selectinload(Track.artist_roles)
                .selectinload(TrackArtistRole.artist),
                selectinload(PlaylistTracks.track)
                .selectinload(Track.artist_roles)
                .selectinload(TrackArtistRole.role),
            ],
        )
        return [self._track_to_dict(pt.track) for pt in playlist_tracks]

    def get_mood_tracks(self, mood_id: int) -> list[dict]:
        associations = self.get_db.get_all_entities(
            "MoodTrackAssociation",
            mood_id=mood_id,
            load_options=[
                selectinload(MoodTrackAssociation.track)
                .selectinload(Track.artist_roles)
                .selectinload(TrackArtistRole.artist),
                selectinload(MoodTrackAssociation.track)
                .selectinload(Track.artist_roles)
                .selectinload(TrackArtistRole.role),
            ],
        )
        return [self._track_to_dict(assoc.track) for assoc in associations]

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
        """Append a {title, artist, reason} entry for the sync Log tab."""
        if failures is None:
            return
        failures.append(
            {"title": track.get("title", "?"), "artist": track.get("artist", "?"), "reason": reason}
        )

    @staticmethod
    def _empty_result(playlist_name: str, message: str) -> dict:
        """A zeroed result dict (shared by the 'empty'/'no device' early exits)."""
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
        """The file actually copied to the device: the transcoded MP3 if one
        was produced for this track, otherwise the library original."""
        return track.get("sync_source_path") or track["file_path"]

    def _prepare_transcodes(
        self,
        tracks: list[dict],
        failures: list[dict] | None,
        progress_callback=None,
        progress_total: int = 0,
        should_cancel: Callable[[], bool] | None = None,
        bitrate: str = "320k",
    ) -> None:
        """
        Transcode every lossless track to a cached MP3 and point
        track['sync_source_path'] at it. Lossy tracks are left untouched.

        A track whose transcode fails or is cancelled is flagged
        '_transcode_skipped' (so the diff pass ignores it) and its reason is
        recorded in `failures` here.
        """
        for i, track in enumerate(tracks):
            src = track.get("file_path")
            if not src or not is_lossless_path(src):
                continue
            if self._unusable_source_reason(track):
                continue  # the diff pass records the real "source missing" reason
            if should_cancel and should_cancel():
                track["_transcode_skipped"] = True
                self._record_failure(failures, track, "cancelled before it was copied")
                continue
            if progress_callback:
                progress_callback(i, progress_total, f"Converting to MP3: {track['title']}")
            try:
                track["sync_source_path"] = str(self.transcode_cache.get_or_create(src, bitrate))
            except (TranscodeError, OSError) as e:
                track["_transcode_skipped"] = True
                logger.error(f"Transcode failed for {src}: {e}")
                self._record_failure(failures, track, f"could not convert to MP3: {e}")

    @staticmethod
    def _clean_component(s: str) -> str:
        """Strip a string down to chars that are safe in a filename."""
        return "".join(c for c in s if c.isalnum() or c in (" ", "-", "_")).strip()

    def _safe_filename(self, artist: str, title: str, ext: str) -> str:
        """Build a safe 'Artist - Title.ext' filename stripping illegal chars."""
        return f"{self._clean_component(artist)} - {self._clean_component(title)}{ext}"

    def _safe_playlist_name(self, name: str) -> str:
        """The on-disk stem for a playlist/mood's .m3u file."""
        return self._clean_component(name)

    def _predicted_device_filename(self, track: dict, transcode_to_mp3: bool) -> str:
        """
        The name this track's file has (or would have) on the device, without
        running the transcode. Mirrors the extension logic in _diff_local_pool
        / _diff_mtp_pool: a lossless source becomes '.mp3' only when transcode
        is actually in effect (caller has already AND-ed in ffmpeg_available).
        """
        src = track.get("file_path") or ""
        ext = ".mp3" if transcode_to_mp3 and is_lossless_path(src) else Path(src).suffix
        return self._safe_filename(track["artist"], track["title"], ext)

    @staticmethod
    def _is_m3u_name(name: str) -> bool:
        return name.lower().endswith(".m3u")

    @staticmethod
    def _is_prunable_music_name(name: str) -> bool:
        """
        True only for names that match this app's 'Artist - Title.ext' output,
        so a file the user dropped into music/ by hand is never deleted.
        """
        return " - " in name and Path(name).suffix.lower() in _PRUNABLE_AUDIO_EXTENSIONS

    def _file_md5(self, file_path: str, chunk_size: int = 65536) -> str:
        """Return MD5 hex digest of a local file."""
        md5 = hashlib.md5()
        try:
            with Path(file_path).open("rb") as f:
                while True:
                    chunk = f.read(chunk_size)
                    if not chunk:
                        break
                    md5.update(chunk)
            return md5.hexdigest()
        except OSError as e:
            logger.warning(f"MD5 failed for {file_path}: {e}")
            return ""

    def _is_local_duplicate(self, source_path: str, dest_path: str) -> bool:
        """
        True if dest_path already contains an identical copy of source_path.
        Fast size check first, then MD5 confirmation.
        """
        if not Path(dest_path).exists():
            return False
        try:
            if Path(source_path).stat().st_size != Path(dest_path).stat().st_size:
                return False
            return self._file_md5(source_path) == self._file_md5(dest_path)
        except OSError:
            return False

    def _list_local_pool(self, music_dir: str) -> dict[str, int]:
        """One directory scan → {filename: size} for everything already
        present in music_dir. Backs both the diff pre-pass and post-copy
        verification so neither pays a per-file stat/exists call."""
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

    def _diff_local_pool(
        self, tracks: list[dict], music_dir: str, failures: list[dict] | None = None
    ) -> tuple[list[dict], list[dict]]:
        """
        Partition tracks into (to_copy, to_skip) using ONE directory listing
        instead of a per-track existence check. Name+size matches are
        confirmed with MD5 in parallel (I/O-bound); everything else is
        scheduled to copy. Sets device_filename on every track it accepts.

        Tracks whose source file is missing or unreadable are appended to
        `failures` (if given) as {title, artist, reason} instead of being
        silently dropped.
        """
        existing = self._list_local_pool(music_dir)
        to_copy: list[dict] = []
        md5_candidates: list[tuple[dict, str]] = []

        for track in tracks:
            if track.get("_transcode_skipped"):
                continue  # transcode failed/cancelled — already in `failures`
            reason = self._unusable_source_reason(track)
            if reason:
                logger.warning(f"{reason}: {track.get('file_path')}")
                self._record_failure(failures, track, reason)
                continue
            source = self._effective_source(track)
            ext = Path(source).suffix
            device_filename = self._safe_filename(track["artist"], track["title"], ext)
            track["device_filename"] = device_filename
            try:
                source_size = Path(source).stat().st_size
            except OSError:
                to_copy.append(track)
                continue
            if existing.get(device_filename) == source_size:
                md5_candidates.append((track, str(Path(music_dir) / device_filename)))
            else:
                to_copy.append(track)

        to_skip: list[dict] = []
        if md5_candidates:
            with ThreadPoolExecutor(max_workers=_DUPLICATE_CHECK_WORKERS) as pool:
                futures = {
                    pool.submit(self._is_local_duplicate, self._effective_source(t), dest): t
                    for t, dest in md5_candidates
                }
                for future in as_completed(futures):
                    track = futures[future]
                    (to_skip if future.result() else to_copy).append(track)

        return to_copy, to_skip

    def _diff_mtp_pool(
        self,
        tracks: list[dict],
        device: MtpDevice,
        music_dir_uri: str,
        failures: list[dict] | None = None,
    ) -> tuple[list[dict], list[dict]]:
        """
        Partition tracks into (to_copy, to_skip) using ONE remote directory
        listing (one `gio list`) instead of a `gio info` round trip per
        track. Same size-only comparison the per-file check used — only the
        number of subprocess calls changes (N -> 1).

        Tracks whose source file is missing or unreadable are appended to
        `failures` (if given) as {title, artist, reason} instead of being
        silently dropped.
        """
        existing = self.mtp.list_remote_dir(device, music_dir_uri)
        to_copy: list[dict] = []
        to_skip: list[dict] = []

        for track in tracks:
            if track.get("_transcode_skipped"):
                continue  # transcode failed/cancelled — already in `failures`
            local_path = track.get("file_path", "")
            reason = self._unusable_source_reason(track)
            if reason:
                logger.warning(f"{reason}: {local_path}")
                self._record_failure(failures, track, reason)
                continue
            source = self._effective_source(track)
            ext = Path(source).suffix
            device_filename = self._safe_filename(track["artist"], track["title"], ext)
            track["device_filename"] = device_filename
            try:
                source_size = Path(source).stat().st_size
            except OSError:
                to_copy.append(track)
                continue
            if existing.get(device_filename) == source_size:
                to_skip.append(track)
            else:
                to_copy.append(track)

        return to_copy, to_skip

    def _copy_with_retry(
        self,
        tracks: list[dict],
        copy_one: Callable[[dict], bool],
        list_existing: Callable[[], dict[str, int]],
        expected_size: Callable[[dict], int | None],
        progress_callback=None,
        progress_total: int = 0,
        progress_label: str = "Copying",
        should_cancel: Callable[[], bool] | None = None,
    ) -> tuple[list[dict], list[dict]]:
        """
        Copy `tracks` via copy_one(), then verify each one actually landed
        by re-listing the destination ONCE and comparing sizes -- a
        transport reporting success is not trusted on its own, since MTP
        transfers can silently drop or truncate a file. Anything that
        doesn't verify is retried, up to _MAX_RETRIES extra rounds.
        Returns (succeeded, failed); succeeded tracks are marked
        copied_successfully=True.

        If `should_cancel` returns True, the copy loop stops before the next
        track (an in-flight copy_one still finishes -- it's a blocking
        subprocess) and everything not yet verified is returned as failed.
        """
        remaining = tracks
        succeeded: list[dict] = []
        cancelled = False

        for attempt in range(_MAX_RETRIES + 1):
            if not remaining or cancelled:
                break
            label = (
                progress_label if attempt == 0 else f"Retrying ({attempt + 1}/{_MAX_RETRIES + 1})"
            )
            for i, track in enumerate(remaining):
                if should_cancel and should_cancel():
                    cancelled = True
                    break
                if progress_callback:
                    progress_callback(i, progress_total, f"{label}: {track['title']}")
                track["_last_copy_ok"] = copy_one(track)

            existing = list_existing()
            still_failed = []
            for track in remaining:
                size = existing.get(track["device_filename"])
                expected = expected_size(track)
                if size is not None and expected is not None and size == expected:
                    track["copied_successfully"] = True
                    succeeded.append(track)
                else:
                    still_failed.append(track)
            remaining = still_failed
            if remaining and not cancelled and attempt < _MAX_RETRIES:
                logger.warning(
                    f"{len(remaining)} track(s) failed verification, "
                    f"retrying (attempt {attempt + 2}/{_MAX_RETRIES + 1})"
                )

        for track in remaining:
            track["copied_successfully"] = False
            if cancelled:
                logger.info(f"Sync cancelled before copying: {track.get('device_filename')}")
                track["failure_reason"] = "cancelled before it was copied"
            elif track.get("_last_copy_ok"):
                logger.error(f"Failed to sync after retries: {track.get('device_filename')}")
                track["failure_reason"] = (
                    f"copied but never verified on the destination after {_MAX_RETRIES + 1} "
                    "attempts (truncated or rejected by the device)"
                )
            else:
                logger.error(f"Failed to sync after retries: {track.get('device_filename')}")
                track["failure_reason"] = (
                    f"copy failed after {_MAX_RETRIES + 1} attempts (transport error)"
                )

        return succeeded, remaining

    def _build_m3u_content(
        self, playlist_data: dict, tracks: list[dict], music_subpath: str = "../Music"
    ) -> str:
        """Build the text content of an M3U playlist file."""
        lines = ["#EXTM3U"]
        for track in tracks:
            if not track.get("copied_successfully", False):
                continue
            duration = track.get("duration") or 0
            lines.append(f"#EXTINF:{duration},{track['artist']} - {track['title']}")
            lines.append(f"{music_subpath}/{track['device_filename']}")
        return "\n".join(lines) + "\n"

    # ------------------------------------------------------------------
    # Folder sync
    # ------------------------------------------------------------------

    def copy_track(self, source_path: str, dest_path: str) -> bool:
        """
        Copy a track to dest_path. Callers are expected to have already
        filtered out duplicates via _diff_local_pool.
        """
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

    def clear_device_folder(self, device_path: str):
        """Remove music/ and playlists/ subdirectories before a fresh folder sync."""
        for subdir in ("music", "playlists"):
            target = Path(device_path) / subdir
            if target.exists():
                shutil.rmtree(target)
                logger.info(f"Cleared folder: {target}")

    def sync_playlist_to_device(
        self,
        playlist_data: dict,
        device_path: str,
        progress_callback=None,
        should_cancel=None,
        transcode_to_mp3: bool = False,
        transcode_bitrate: str = "320k",
    ) -> dict:
        """Sync a single playlist or mood to a local folder path."""
        playlist_name = playlist_data["name"]

        music_dir = Path(device_path) / "music"
        playlists_dir = Path(device_path) / "playlists"
        music_dir.mkdir(parents=True, exist_ok=True)
        playlists_dir.mkdir(parents=True, exist_ok=True)

        tracks = self.get_item_tracks(playlist_data)
        if not tracks:
            return self._empty_result(playlist_name, "Playlist is empty")

        total_tracks = len(tracks)
        failures: list[dict] = []
        ffmpeg_missing = transcode_to_mp3 and not ffmpeg_available()
        if transcode_to_mp3 and not ffmpeg_missing:
            self._prepare_transcodes(
                tracks, failures, progress_callback, total_tracks, should_cancel, transcode_bitrate
            )
        to_copy, to_skip = self._diff_local_pool(tracks, music_dir, failures)
        for track in to_skip:
            track["copied_successfully"] = True

        def copy_one(track: dict) -> bool:
            dest_path = music_dir / track["device_filename"]
            return self.copy_track(self._effective_source(track), dest_path)

        def expected_size(track: dict) -> int | None:
            try:
                return Path(self._effective_source(track)).stat().st_size
            except OSError:
                return None

        succeeded, failed = self._copy_with_retry(
            to_copy,
            copy_one,
            lambda: self._list_local_pool(str(music_dir)),
            expected_size,
            progress_callback,
            progress_total=total_tracks,
            progress_label="Copying",
            should_cancel=should_cancel,
        )

        for track in failed:
            self._record_failure(failures, track, track.get("failure_reason", "unknown error"))

        processed_tracks = to_skip + succeeded + failed
        tracks_copied = len(succeeded)
        tracks_skipped = len(to_skip)
        tracks_failed = len(failures)
        tracks_transcoded = sum(1 for t in succeeded if t.get("sync_source_path"))

        m3u_content = self._build_m3u_content(playlist_data, processed_tracks)
        safe_name = self._safe_playlist_name(playlist_name)
        m3u_path = playlists_dir / f"{safe_name}.m3u"
        try:
            with m3u_path.open("w", encoding="utf-8") as f:
                f.write(m3u_content)
        except OSError as e:
            logger.error(f"Failed to write M3U: {e}")

        message = f"{tracks_copied} copied, {tracks_skipped} skipped"
        if tracks_transcoded:
            message += f", {tracks_transcoded} to MP3"
        if tracks_failed:
            message += f", {tracks_failed} failed"
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
        }

    # ------------------------------------------------------------------
    # MTP sync
    # ------------------------------------------------------------------

    def _get_mtp_device(self, device_uri: str) -> MtpDevice | None:
        """
        Return the live MtpDevice matching device_uri, or None if not found.
        Ensures the device is mounted before returning.
        """
        devices = self.mtp.list_devices()
        match = next((d for d in devices if d.uri == device_uri), None)
        if match is None:
            logger.error(f"MTP device not found: {device_uri}")
            return None
        self.mtp.ensure_mounted(match)
        return match

    def clear_mtp_folders(self, device_uri: str, music_path: str):
        """
        Delete the Music and companion Playlists folders on the device.
        Only called when clear_before_sync is True.
        """
        device = self._get_mtp_device(device_uri)
        if device is None:
            return
        for uri in (
            self.mtp.build_music_uri(device, music_path),
            self.mtp.build_playlists_dir_uri(device, music_path),
        ):
            self.mtp.remove_remote_dir(device, uri)
            logger.info(f"Cleared MTP folder: {uri}")

    def sync_playlist_to_mtp(
        self,
        playlist_data: dict,
        device_uri: str,
        music_path: str,
        progress_callback=None,
        should_cancel=None,
        transcode_to_mp3: bool = False,
        transcode_bitrate: str = "320k",
    ) -> dict:
        """
        Sync a single playlist or mood to a connected Android device via MTP.

        Track files land in:   {device_uri}/{music_path}/
        M3U playlist lands in: {device_uri}/{music_path}_Playlists/

        Returns the same result-dict shape as sync_playlist_to_device()
        so SyncWorker and the UI don't need to know which method ran.
        """
        playlist_name = playlist_data["name"]

        device = self._get_mtp_device(device_uri)
        if device is None:
            return self._empty_result(
                playlist_name, "Device not found — is it plugged in with File Transfer selected?"
            )

        # Ensure remote directories exist
        music_dir_uri = self.mtp.build_music_uri(device, music_path)
        self.mtp.make_remote_dir(device, music_dir_uri)
        self.mtp.make_remote_dir(device, self.mtp.build_playlists_dir_uri(device, music_path))

        tracks = self.get_item_tracks(playlist_data)
        if not tracks:
            return self._empty_result(playlist_name, "Playlist is empty")

        total_tracks = len(tracks)
        failures: list[dict] = []
        ffmpeg_missing = transcode_to_mp3 and not ffmpeg_available()
        if transcode_to_mp3 and not ffmpeg_missing:
            self._prepare_transcodes(
                tracks, failures, progress_callback, total_tracks, should_cancel, transcode_bitrate
            )
        to_copy, to_skip = self._diff_mtp_pool(tracks, device, music_dir_uri, failures)
        for track in to_skip:
            track["copied_successfully"] = True

        def copy_one(track: dict) -> bool:
            remote_uri = self.mtp.build_file_uri(device, music_path, track["device_filename"])
            return self.mtp.copy_file(device, self._effective_source(track), remote_uri)

        def expected_size(track: dict) -> int | None:
            try:
                return Path(self._effective_source(track)).stat().st_size
            except OSError:
                return None

        succeeded, failed = self._copy_with_retry(
            to_copy,
            copy_one,
            lambda: self.mtp.list_remote_dir(device, music_dir_uri),
            expected_size,
            progress_callback,
            progress_total=total_tracks,
            progress_label="Sending",
            should_cancel=should_cancel,
        )

        for track in failed:
            self._record_failure(failures, track, track.get("failure_reason", "unknown error"))

        processed_tracks = to_skip + succeeded + failed
        tracks_copied = len(succeeded)
        tracks_skipped = len(to_skip)
        tracks_failed = len(failures)
        tracks_transcoded = sum(1 for t in succeeded if t.get("sync_source_path"))

        # Push M3U — relative path from Playlists dir back up to Music dir
        music_folder_name = music_path.strip("/").split("/")[-1]
        parent_path = "/".join(music_path.strip("/").split("/")[:-1]) if "/" in music_path else ""
        if parent_path:
            # If music is in a subdirectory, need to go up to parent then down to Music
            depth = len(music_path.strip("/").split("/"))
            go_up = "../" * depth
            music_subpath = f"{go_up}{music_folder_name}"
        else:
            # Simple case: Music and Playlists are siblings
            music_subpath = f"../{music_folder_name}"

        m3u_content = self._build_m3u_content(
            playlist_data, processed_tracks, music_subpath=music_subpath
        )
        safe_name = self._safe_playlist_name(playlist_name)
        playlist_uri = self.mtp.build_playlist_uri(device, music_path, safe_name)
        self.mtp.copy_text_as_file(device, m3u_content, playlist_uri)

        message = f"{tracks_copied} sent, {tracks_skipped} skipped"
        if tracks_transcoded:
            message += f", {tracks_transcoded} to MP3"
        if tracks_failed:
            message += f", {tracks_failed} failed"
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
        }

    # ------------------------------------------------------------------
    # Prune — remove destination files for no-longer-tracked items
    # ------------------------------------------------------------------

    @staticmethod
    def _empty_prune_result() -> dict:
        return {"removed_tracks": [], "removed_playlists": [], "removed_count": 0}

    def _desired_device_contents(
        self, tracked_items: list[dict], transcode_to_mp3: bool
    ) -> tuple[set[str], set[str]]:
        """
        (music filenames, m3u filenames) that SHOULD be on the device given the
        currently-tracked playlists/moods. A track with no source file on record
        contributes nothing (it can't have been copied).
        """
        desired_music: set[str] = set()
        for item in tracked_items:
            for track in self.get_item_tracks(item):
                if not track.get("file_path"):
                    continue
                desired_music.add(self._predicted_device_filename(track, transcode_to_mp3))
        desired_m3u = {f"{self._safe_playlist_name(it['name'])}.m3u" for it in tracked_items}
        return desired_music, desired_m3u

    def prune_device(
        self, profile, tracked_items: list[dict], should_cancel: Callable[[], bool] | None = None
    ) -> dict:
        """
        Delete files from the destination that belong to playlists/moods no
        longer in `profile` (i.e. not represented in `tracked_items`).

        Only touches music/ files whose name matches this app's
        'Artist - Title.ext' scheme and .m3u files in playlists/ — never
        directories or user-dropped files. No-op if the sync was cancelled
        (stay conservative on a half-run) or on the aft MTP backend (no remote
        listing => can't know what's there).

        Returns {removed_tracks: [names], removed_playlists: [names],
        removed_count: int}.
        """
        if should_cancel and should_cancel():
            return self._empty_prune_result()

        transcode = profile.transcode_to_mp3 and ffmpeg_available()
        desired_music, desired_m3u = self._desired_device_contents(tracked_items, transcode)

        if profile.is_mtp:
            return self._prune_mtp(profile, desired_music, desired_m3u)
        return self._prune_folder(profile, desired_music, desired_m3u)

    def _prune_folder(self, profile, desired_music: set[str], desired_m3u: set[str]) -> dict:
        base = Path(profile.path)
        removed_tracks = self._reconcile_local_dir(
            base / "music", desired_music, self._is_prunable_music_name
        )
        removed_playlists = self._reconcile_local_dir(
            base / "playlists", desired_m3u, self._is_m3u_name
        )
        return {
            "removed_tracks": removed_tracks,
            "removed_playlists": removed_playlists,
            "removed_count": len(removed_tracks) + len(removed_playlists),
        }

    def _reconcile_local_dir(
        self, directory: Path, desired: set[str], name_ok: Callable[[str], bool]
    ) -> list[str]:
        removed: list[str] = []
        for name in self._list_local_pool(str(directory)):
            if name in desired or not name_ok(name):
                continue
            try:
                (directory / name).unlink()
                removed.append(name)
                logger.info(f"Prune: removed {directory / name}")
            except OSError as e:
                logger.error(f"Prune failed to remove {name}: {e}")
        return removed

    def _prune_mtp(self, profile, desired_music: set[str], desired_m3u: set[str]) -> dict:
        result = self._empty_prune_result()
        device = self._get_mtp_device(profile.device_uri)
        if device is None or device.backend != "gio":
            return result

        music_dir_uri = self.mtp.build_music_uri(device, profile.music_path)
        playlists_dir_uri = self.mtp.build_playlists_dir_uri(device, profile.music_path)
        result["removed_tracks"] = self._reconcile_mtp_dir(
            device, music_dir_uri, desired_music, self._is_prunable_music_name
        )
        result["removed_playlists"] = self._reconcile_mtp_dir(
            device, playlists_dir_uri, desired_m3u, self._is_m3u_name
        )
        result["removed_count"] = len(result["removed_tracks"]) + len(result["removed_playlists"])
        return result

    def _reconcile_mtp_dir(
        self, device, dir_uri: str, desired: set[str], name_ok: Callable[[str], bool]
    ) -> list[str]:
        removed: list[str] = []
        for name in self.mtp.list_remote_dir(device, dir_uri):
            if name in desired or not name_ok(name):
                continue
            file_uri = dir_uri.rstrip("/") + "/" + name
            if self.mtp.delete_remote_file(device, file_uri):
                removed.append(name)
                logger.info(f"Prune: removed {file_uri}")
        return removed
