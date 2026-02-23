# sync_utility.py
import hashlib
import json
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from PySide6.QtCore import QThread, Signal

from db_helpers import GetFromDB
from logger_config import logger
from status_utility import StatusManager

# ---------------------------------------------------------------------------
# Sync Profile dataclass
# ---------------------------------------------------------------------------


@dataclass
class SyncProfile:
    """A named sync profile that remembers a device folder and playlist selection."""

    name: str
    path: str
    playlist_ids: List[int] = field(default_factory=list)
    clear_before_sync: bool = False

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "path": self.path,
            "playlist_ids": self.playlist_ids,
            "clear_before_sync": self.clear_before_sync,
        }

    @staticmethod
    def from_dict(data: dict) -> "SyncProfile":
        return SyncProfile(
            name=data.get("name", "Unnamed"),
            path=data.get("path", ""),
            playlist_ids=data.get("playlist_ids", []),
            clear_before_sync=data.get("clear_before_sync", False),
        )


# ---------------------------------------------------------------------------
# Profile store — persists to a JSON file next to config.ini
# ---------------------------------------------------------------------------


class SyncProfileStore:
    """Load and save sync profiles to disk as JSON."""

    def __init__(self, profiles_path: Optional[str] = None):
        if profiles_path is None:
            from asset_paths import config as asset_config

            profiles_path = str(
                Path(asset_config("config.ini")).parent / "sync_profiles.json"
            )
        self.profiles_path = Path(profiles_path)

    def load(self) -> List[SyncProfile]:
        if not self.profiles_path.exists():
            return []
        try:
            with open(self.profiles_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return [SyncProfile.from_dict(d) for d in data]
        except Exception as e:
            logger.error(f"Failed to load sync profiles: {e}")
            return []

    def save(self, profiles: List[SyncProfile]):
        try:
            self.profiles_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.profiles_path, "w", encoding="utf-8") as f:
                json.dump([p.to_dict() for p in profiles], f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save sync profiles: {e}")


# ---------------------------------------------------------------------------
# SyncManager
# ---------------------------------------------------------------------------


class SyncManager:
    """Sync manager: copies tracks, creates M3U playlists, handles duplicates."""

    def __init__(self, db_session):
        self.session = db_session
        self.get_db = GetFromDB(db_session)

    # ------------------------------------------------------------------
    # Database helpers
    # ------------------------------------------------------------------

    def get_playlists(self) -> List[Dict]:
        playlists = self.get_db.get_all_entities("Playlist")
        return [
            {
                "playlist_id": pl.playlist_id,
                "name": pl.playlist_name,
                "description": pl.playlist_description,
                "track_count": pl.track_count,
                "is_smart": pl.is_smart,
            }
            for pl in playlists
        ]

    def get_playlist_tracks(self, playlist_id: int) -> List[Dict]:
        playlist_tracks = self.get_db.get_all_entities(
            "PlaylistTracks", playlist_id=playlist_id
        )
        tracks = []
        for pt in playlist_tracks:
            track = pt.track
            artists = track.primary_artists
            artist_name = "Various Artists"
            if artists:
                artist_name = " & ".join([a.artist_name for a in artists])
            tracks.append(
                {
                    "track_id": track.track_id,
                    "file_path": track.track_file_path,
                    "title": track.track_name,
                    "artist": artist_name,
                    "duration": track.duration,
                }
            )
        return tracks

    # ------------------------------------------------------------------
    # Duplicate detection
    # ------------------------------------------------------------------

    def _file_md5(self, file_path: str, chunk_size: int = 65536) -> str:
        """Return MD5 hex digest of a file."""
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

    def _is_duplicate(self, source_path: str, dest_path: str) -> bool:
        """
        Return True if dest_path already contains an identical copy of source_path.
        Uses file-size as a fast pre-check, then MD5 for confirmation.
        """
        if not os.path.exists(dest_path):
            return False
        try:
            src_size = os.path.getsize(source_path)
            dst_size = os.path.getsize(dest_path)
            if src_size != dst_size:
                return False
            # Same size — do full MD5 comparison
            return self._file_md5(source_path) == self._file_md5(dest_path)
        except OSError:
            return False

    # ------------------------------------------------------------------
    # File operations
    # ------------------------------------------------------------------

    def copy_track(self, source_path: str, dest_path: str) -> tuple[bool, str]:
        """
        Copy a track to dest_path.
        Returns (success, status) where status is 'copied', 'skipped', or 'error'.
        """
        try:
            if not os.path.exists(source_path):
                logger.error(f"Source file not found: {source_path}")
                return False, "error"

            if self._is_duplicate(source_path, dest_path):
                logger.debug(f"Duplicate skipped: {dest_path}")
                return True, "skipped"

            os.makedirs(os.path.dirname(dest_path), exist_ok=True)
            shutil.copy2(source_path, dest_path)
            logger.debug(f"Copied: {source_path} -> {dest_path}")
            return True, "copied"

        except Exception as e:
            logger.error(f"Error copying {source_path}: {e}")
            return False, "error"

    def clear_device_folder(self, device_path: str):
        """Remove music/ and playlists/ subdirectories before a fresh sync."""
        for subdir in ("music", "playlists"):
            target = os.path.join(device_path, subdir)
            if os.path.exists(target):
                shutil.rmtree(target)
                logger.info(f"Cleared folder: {target}")

    # ------------------------------------------------------------------
    # M3U creation
    # ------------------------------------------------------------------

    def create_m3u_playlist(
        self, playlist_data: Dict, tracks: List[Dict], target_dir: str
    ) -> str:
        playlist_name = playlist_data["name"]
        safe_name = "".join(
            c for c in playlist_name if c.isalnum() or c in (" ", "-", "_")
        ).strip()
        m3u_path = os.path.join(target_dir, f"{safe_name}.m3u")

        with open(m3u_path, "w", encoding="utf-8") as f:
            f.write("#EXTM3U\n")
            for track in tracks:
                if not track.get("copied_successfully", False):
                    continue
                duration = track["duration"] or 0
                f.write(f"#EXTINF:{duration},{track['artist']} - {track['title']}\n")
                rel_path = os.path.join(".", track["device_filename"])
                f.write(f"{rel_path}\n")

        logger.info(f"Created M3U playlist: {m3u_path}")
        return m3u_path

    # ------------------------------------------------------------------
    # Main sync entry point
    # ------------------------------------------------------------------

    def sync_playlist_to_device(
        self,
        playlist_data: Dict,
        device_path: str,
        progress_callback=None,
    ) -> Dict:
        """Sync a single playlist to device_path. Returns a result dict."""
        playlist_id = playlist_data["playlist_id"]
        playlist_name = playlist_data["name"]

        music_dir = os.path.join(device_path, "music")
        playlists_dir = os.path.join(device_path, "playlists")
        os.makedirs(music_dir, exist_ok=True)
        os.makedirs(playlists_dir, exist_ok=True)

        tracks = self.get_playlist_tracks(playlist_id)
        if not tracks:
            return {
                "playlist_name": playlist_name,
                "success": False,
                "message": "Playlist is empty",
                "tracks_copied": 0,
                "tracks_skipped": 0,
                "total_tracks": 0,
            }

        tracks_copied = 0
        tracks_skipped = 0
        total_tracks = len(tracks)
        processed_tracks = []

        for i, track in enumerate(tracks):
            if progress_callback:
                progress_callback(i, total_tracks, f"Processing: {track['title']}")

            if not track["file_path"] or not os.path.exists(track["file_path"]):
                logger.warning(f"Source file not found: {track['file_path']}")
                continue

            safe_artist = "".join(
                c for c in track["artist"] if c.isalnum() or c in (" ", "-", "_")
            ).strip()
            safe_title = "".join(
                c for c in track["title"] if c.isalnum() or c in (" ", "-", "_")
            ).strip()
            file_ext = os.path.splitext(track["file_path"])[1]
            device_filename = f"{safe_artist} - {safe_title}{file_ext}"
            dest_path = os.path.join(music_dir, device_filename)

            success, status = self.copy_track(track["file_path"], dest_path)

            processed_track = track.copy()
            processed_track["device_filename"] = device_filename
            processed_track["copied_successfully"] = success
            processed_tracks.append(processed_track)

            if success:
                if status == "copied":
                    tracks_copied += 1
                elif status == "skipped":
                    tracks_skipped += 1

        m3u_path = None
        if any(t["copied_successfully"] for t in processed_tracks):
            m3u_path = self.create_m3u_playlist(
                playlist_data, processed_tracks, playlists_dir
            )

        if progress_callback:
            progress_callback(total_tracks, total_tracks, "Complete")

        return {
            "playlist_name": playlist_name,
            "success": tracks_copied > 0 or tracks_skipped > 0,
            "message": (
                f"Copied {tracks_copied}, skipped {tracks_skipped} duplicates"
                f" / {total_tracks} tracks"
            ),
            "tracks_copied": tracks_copied,
            "tracks_skipped": tracks_skipped,
            "total_tracks": total_tracks,
            "m3u_path": m3u_path,
        }


# ---------------------------------------------------------------------------
# Background worker
# ---------------------------------------------------------------------------


class SyncWorker(QThread):
    """Background worker for sync operations."""

    progress = Signal(int, int, str)  # current, total, message
    playlist_complete = Signal(dict)  # playlist result
    finished = Signal(list)  # all results

    def __init__(
        self,
        sync_manager: SyncManager,
        playlists: List[Dict],
        device_path: str,
        clear_before_sync: bool = False,
    ):
        super().__init__()
        self.sync_manager = sync_manager
        self.playlists = playlists
        self.device_path = device_path
        self.clear_before_sync = clear_before_sync
        self.results = []
        self._is_cancelled = False

    def run(self):
        try:
            status_manager = StatusManager()

            # Optionally clear destination before syncing
            if self.clear_before_sync and not self._is_cancelled:
                self.progress.emit(0, 1, "Clearing destination folder...")
                self.sync_manager.clear_device_folder(self.device_path)

            total_playlists = len(self.playlists)
            for i, playlist in enumerate(self.playlists):
                if self._is_cancelled:
                    status_manager.show_message("Sync cancelled", 3000)
                    break

                self.progress.emit(i, total_playlists, f"Starting: {playlist['name']}")
                status_manager.show_message(f"Syncing: {playlist['name']}", 0)

                result = self.sync_manager.sync_playlist_to_device(
                    playlist, self.device_path, self._progress_callback
                )

                self.results.append(result)
                self.playlist_complete.emit(result)

            self.finished.emit(self.results)

        except Exception as e:
            logger.error(f"Sync worker error: {e}")
            StatusManager().end_task(f"Sync error: {str(e)}", 5000)
            self.finished.emit([])

    def _progress_callback(self, current: int, total: int, message: str):
        self.progress.emit(current, total, message)

    def cancel(self):
        self._is_cancelled = True
