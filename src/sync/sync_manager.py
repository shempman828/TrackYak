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
import shutil

from sqlalchemy.orm import selectinload

from src.db.db_helpers import GetFromDB
from src.db.db_tables import MoodTrackAssociation, PlaylistTracks, Track, TrackArtistRole
from src.foundation.logger_config import logger
from src.sync.mtp_manager import MtpDevice, MtpManager

# Post-copy verification retries this many times before a track is
# reported as failed (so up to _MAX_RETRIES + 1 total copy attempts).
_MAX_RETRIES = 2

# Local duplicate confirmation (MD5) is disk I/O-bound, so run it across
# a small thread pool rather than sequentially in the diff pre-pass.
_DUPLICATE_CHECK_WORKERS = 8


class SyncManager:
    def __init__(self, db_session):
        self.session = db_session
        self.get_db = GetFromDB(db_session)
        self.mtp = MtpManager()

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

    def _safe_filename(self, artist: str, title: str, ext: str) -> str:
        """Build a safe 'Artist - Title.ext' filename stripping illegal chars."""

        def clean(s: str) -> str:
            return "".join(c for c in s if c.isalnum() or c in (" ", "-", "_")).strip()

        return f"{clean(artist)} - {clean(title)}{ext}"

    def _file_md5(self, file_path: str, chunk_size: int = 65536) -> str:
        """Return MD5 hex digest of a local file."""
        md5 = hashlib.md5()
        try:
            with open(file_path, "rb") as f:
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
        if not os.path.exists(dest_path):
            return False
        try:
            if os.path.getsize(source_path) != os.path.getsize(dest_path):
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

    def _diff_local_pool(self, tracks: list[dict], music_dir: str) -> tuple[list[dict], list[dict]]:
        """
        Partition tracks into (to_copy, to_skip) using ONE directory listing
        instead of a per-track existence check. Name+size matches are
        confirmed with MD5 in parallel (I/O-bound); everything else is
        scheduled to copy. Sets device_filename on every track it accepts.
        """
        existing = self._list_local_pool(music_dir)
        to_copy: list[dict] = []
        md5_candidates: list[tuple[dict, str]] = []

        for track in tracks:
            if not track["file_path"] or not os.path.exists(track["file_path"]):
                logger.warning(f"Source file not found: {track['file_path']}")
                continue
            ext = os.path.splitext(track["file_path"])[1]
            device_filename = self._safe_filename(track["artist"], track["title"], ext)
            track["device_filename"] = device_filename
            try:
                source_size = os.path.getsize(track["file_path"])
            except OSError:
                to_copy.append(track)
                continue
            if existing.get(device_filename) == source_size:
                md5_candidates.append((track, os.path.join(music_dir, device_filename)))
            else:
                to_copy.append(track)

        to_skip: list[dict] = []
        if md5_candidates:
            with ThreadPoolExecutor(max_workers=_DUPLICATE_CHECK_WORKERS) as pool:
                futures = {
                    pool.submit(self._is_local_duplicate, t["file_path"], dest): t
                    for t, dest in md5_candidates
                }
                for future in as_completed(futures):
                    track = futures[future]
                    (to_skip if future.result() else to_copy).append(track)

        return to_copy, to_skip

    def _diff_mtp_pool(
        self, tracks: list[dict], device: MtpDevice, music_dir_uri: str
    ) -> tuple[list[dict], list[dict]]:
        """
        Partition tracks into (to_copy, to_skip) using ONE remote directory
        listing (one `gio list`) instead of a `gio info` round trip per
        track. Same size-only comparison the per-file check used — only the
        number of subprocess calls changes (N -> 1).
        """
        existing = self.mtp.list_remote_dir(device, music_dir_uri)
        to_copy: list[dict] = []
        to_skip: list[dict] = []

        for track in tracks:
            local_path = track.get("file_path", "")
            if not local_path or not os.path.exists(local_path):
                logger.warning(f"Source file not found: {local_path}")
                continue
            ext = os.path.splitext(local_path)[1]
            device_filename = self._safe_filename(track["artist"], track["title"], ext)
            track["device_filename"] = device_filename
            try:
                source_size = os.path.getsize(local_path)
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
    ) -> tuple[list[dict], list[dict]]:
        """
        Copy `tracks` via copy_one(), then verify each one actually landed
        by re-listing the destination ONCE and comparing sizes -- a
        transport reporting success is not trusted on its own, since MTP
        transfers can silently drop or truncate a file. Anything that
        doesn't verify is retried, up to _MAX_RETRIES extra rounds.
        Returns (succeeded, failed); succeeded tracks are marked
        copied_successfully=True.
        """
        remaining = tracks
        succeeded: list[dict] = []

        for attempt in range(_MAX_RETRIES + 1):
            if not remaining:
                break
            label = (
                progress_label if attempt == 0 else f"Retrying ({attempt + 1}/{_MAX_RETRIES + 1})"
            )
            for i, track in enumerate(remaining):
                if progress_callback:
                    progress_callback(i, progress_total, f"{label}: {track['title']}")
                copy_one(track)

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
            if remaining and attempt < _MAX_RETRIES:
                logger.warning(
                    f"{len(remaining)} track(s) failed verification, "
                    f"retrying (attempt {attempt + 2}/{_MAX_RETRIES + 1})"
                )

        for track in remaining:
            track["copied_successfully"] = False
            logger.error(f"Failed to sync after retries: {track.get('device_filename')}")

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
            if not os.path.exists(source_path):
                logger.error(f"Source file not found: {source_path}")
                return False
            os.makedirs(os.path.dirname(dest_path), exist_ok=True)
            shutil.copy2(source_path, dest_path)
            logger.debug(f"Copied: {source_path} → {dest_path}")
            return True
        except OSError as e:
            logger.error(f"Error copying {source_path}: {e}")
            return False

    def clear_device_folder(self, device_path: str):
        """Remove music/ and playlists/ subdirectories before a fresh folder sync."""
        for subdir in ("music", "playlists"):
            target = os.path.join(device_path, subdir)
            if os.path.exists(target):
                shutil.rmtree(target)
                logger.info(f"Cleared folder: {target}")

    def sync_playlist_to_device(
        self, playlist_data: dict, device_path: str, progress_callback=None
    ) -> dict:
        """Sync a single playlist or mood to a local folder path."""
        playlist_name = playlist_data["name"]

        music_dir = os.path.join(device_path, "music")
        playlists_dir = os.path.join(device_path, "playlists")
        os.makedirs(music_dir, exist_ok=True)
        os.makedirs(playlists_dir, exist_ok=True)

        tracks = self.get_item_tracks(playlist_data)
        if not tracks:
            return {
                "playlist_name": playlist_name,
                "success": False,
                "message": "Playlist is empty",
                "tracks_copied": 0,
                "tracks_skipped": 0,
                "tracks_failed": 0,
                "total_tracks": 0,
            }

        total_tracks = len(tracks)
        to_copy, to_skip = self._diff_local_pool(tracks, music_dir)
        for track in to_skip:
            track["copied_successfully"] = True

        def copy_one(track: dict) -> bool:
            dest_path = os.path.join(music_dir, track["device_filename"])
            return self.copy_track(track["file_path"], dest_path)

        def expected_size(track: dict) -> int | None:
            try:
                return os.path.getsize(track["file_path"])
            except OSError:
                return None

        succeeded, failed = self._copy_with_retry(
            to_copy,
            copy_one,
            lambda: self._list_local_pool(music_dir),
            expected_size,
            progress_callback,
            progress_total=total_tracks,
            progress_label="Copying",
        )

        processed_tracks = to_skip + succeeded + failed
        tracks_copied = len(succeeded)
        tracks_skipped = len(to_skip)
        tracks_failed = len(failed)

        m3u_content = self._build_m3u_content(playlist_data, processed_tracks)
        safe_name = "".join(c for c in playlist_name if c.isalnum() or c in (" ", "-", "_")).strip()
        m3u_path = os.path.join(playlists_dir, f"{safe_name}.m3u")
        try:
            with open(m3u_path, "w", encoding="utf-8") as f:
                f.write(m3u_content)
        except OSError as e:
            logger.error(f"Failed to write M3U: {e}")

        message = f"{tracks_copied} copied, {tracks_skipped} skipped"
        if tracks_failed:
            message += f", {tracks_failed} failed"

        return {
            "playlist_name": playlist_name,
            "success": tracks_copied > 0 or tracks_skipped > 0,
            "message": message,
            "tracks_copied": tracks_copied,
            "tracks_skipped": tracks_skipped,
            "tracks_failed": tracks_failed,
            "total_tracks": total_tracks,
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
        self, playlist_data: dict, device_uri: str, music_path: str, progress_callback=None
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
            return {
                "playlist_name": playlist_name,
                "success": False,
                "message": "Device not found — is it plugged in with File Transfer selected?",
                "tracks_copied": 0,
                "tracks_skipped": 0,
                "tracks_failed": 0,
                "total_tracks": 0,
            }

        # Ensure remote directories exist
        music_dir_uri = self.mtp.build_music_uri(device, music_path)
        self.mtp.make_remote_dir(device, music_dir_uri)
        self.mtp.make_remote_dir(device, self.mtp.build_playlists_dir_uri(device, music_path))

        tracks = self.get_item_tracks(playlist_data)
        if not tracks:
            return {
                "playlist_name": playlist_name,
                "success": False,
                "message": "Playlist is empty",
                "tracks_copied": 0,
                "tracks_skipped": 0,
                "tracks_failed": 0,
                "total_tracks": 0,
            }

        total_tracks = len(tracks)
        to_copy, to_skip = self._diff_mtp_pool(tracks, device, music_dir_uri)
        for track in to_skip:
            track["copied_successfully"] = True

        def copy_one(track: dict) -> bool:
            remote_uri = self.mtp.build_file_uri(device, music_path, track["device_filename"])
            return self.mtp.copy_file(device, track["file_path"], remote_uri)

        def expected_size(track: dict) -> int | None:
            try:
                return os.path.getsize(track["file_path"])
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
        )

        processed_tracks = to_skip + succeeded + failed
        tracks_copied = len(succeeded)
        tracks_skipped = len(to_skip)
        tracks_failed = len(failed)

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
        safe_name = "".join(c for c in playlist_name if c.isalnum() or c in (" ", "-", "_")).strip()
        playlist_uri = self.mtp.build_playlist_uri(device, music_path, safe_name)
        self.mtp.copy_text_as_file(device, m3u_content, playlist_uri)

        message = f"{tracks_copied} sent, {tracks_skipped} skipped"
        if tracks_failed:
            message += f", {tracks_failed} failed"

        return {
            "playlist_name": playlist_name,
            "success": tracks_copied > 0 or tracks_skipped > 0,
            "message": message,
            "tracks_copied": tracks_copied,
            "tracks_skipped": tracks_skipped,
            "tracks_failed": tracks_failed,
            "total_tracks": total_tracks,
        }
