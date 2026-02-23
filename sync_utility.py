# sync_module.py
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List

from PySide6.QtCore import QThread, Signal

from db_helpers import GetFromDB
from logger_config import logger
from status_utility import StatusManager


@dataclass
class SyncDevice:
    """Represents a sync target device."""

    name: str
    path: str
    type: str = "android"


class SyncManager:
    """Simplified sync manager focusing on compatibility and ease of use."""

    def __init__(self, db_session):
        self.session = db_session
        self.get_db = GetFromDB(db_session)

    def discover_devices(self) -> List[SyncDevice]:
        """Discover potential sync devices using simple folder browsing."""
        devices = []

        # Simple approach: let user browse to any folder
        devices.append(SyncDevice(name="Browse for folder...", path="", type="custom"))

        # Also check common music directories
        common_paths = [
            Path.home() / "Music",
            Path.home() / "Documents" / "Music",
        ]

        for path in common_paths:
            if path.exists():
                devices.append(
                    SyncDevice(name=f"Local: {path.name}", path=str(path), type="local")
                )

        return devices

    def get_playlists(self) -> List[Dict]:
        """Get all playlists from database."""
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
        """Get all tracks for a playlist."""
        playlist_tracks = self.get_db.get_all_entities(
            "PlaylistTracks", playlist_id=playlist_id
        )

        tracks = []
        for pt in playlist_tracks:
            track = pt.track
            artists = track.primary_artists

            artist_name = "Various Artists"
            if artists:
                artist_name = " & ".join([artist.artist_name for artist in artists])

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

    def create_m3u_playlist(
        self, playlist_data: Dict, tracks: List[Dict], target_dir: str
    ) -> str:
        """Create M3U playlist file with relative paths to music folder."""
        playlist_name = playlist_data["name"]
        # Clean filename for safety
        safe_name = "".join(
            c for c in playlist_name if c.isalnum() or c in (" ", "-", "_")
        ).strip()
        m3u_path = os.path.join(target_dir, f"{safe_name}.m3u")

        with open(m3u_path, "w", encoding="utf-8") as f:
            f.write("#EXTM3U\n")

            for track in tracks:
                if not track.get("copied_successfully", False):
                    continue

                # Write extended info
                duration = track["duration"] or 0
                f.write(f"#EXTINF:{duration},{track['artist']} - {track['title']}\n")

                # Use relative path from playlist location to music file
                # All files are in the same music directory, so just use filename
                rel_path = os.path.join(".", track["device_filename"])
                f.write(f"{rel_path}\n")

        logger.info(f"Created M3U playlist: {m3u_path}")
        return m3u_path

    def _get_file_hash(self, file_path: str) -> str:
        """Generate a simple hash to detect duplicate files."""
        try:
            stat = os.stat(file_path)
            return f"{stat.st_size}_{stat.st_mtime}"
        except OSError as e:
            logger.warning(f"Could not hash file {file_path}: {e}")
            return str(os.path.getsize(file_path)) if os.path.exists(file_path) else "0"

    def copy_track(self, source_path: str, dest_path: str) -> bool:
        """Copy a single track file, skip if already exists."""
        try:
            if not os.path.exists(source_path):
                logger.error(f"Source file not found: {source_path}")
                return False

            # Check if destination already exists with same content
            if os.path.exists(dest_path):
                source_hash = self._get_file_hash(source_path)
                dest_hash = self._get_file_hash(dest_path)

                if source_hash == dest_hash:
                    logger.debug(f"File already exists with same content: {dest_path}")
                    return True
                else:
                    logger.debug(f"File exists but differs: {dest_path}")
                    # For now, skip - could add option to overwrite later
                    return True

            # Create directory if needed
            os.makedirs(os.path.dirname(dest_path), exist_ok=True)

            # Copy file
            shutil.copy2(source_path, dest_path)
            logger.debug(f"Copied: {source_path} -> {dest_path}")
            return True

        except Exception as e:
            logger.error(f"Error copying {source_path}: {e}")
            return False

    def sync_playlist_to_device(
        self, playlist_data: Dict, device_path: str, progress_callback=None
    ) -> Dict:
        """Sync a single playlist to device - simplified flat structure."""
        playlist_id = playlist_data["playlist_id"]
        playlist_name = playlist_data["name"]

        # Create simple directory structure
        music_dir = os.path.join(device_path, "music")
        playlists_dir = os.path.join(device_path, "playlists")
        os.makedirs(music_dir, exist_ok=True)
        os.makedirs(playlists_dir, exist_ok=True)

        # Get playlist tracks
        tracks = self.get_playlist_tracks(playlist_id)
        if not tracks:
            return {
                "playlist_name": playlist_name,
                "success": False,
                "message": "Playlist is empty",
                "tracks_copied": 0,
                "total_tracks": 0,
            }

        # Copy track files to flat structure
        tracks_copied = 0
        total_tracks = len(tracks)
        processed_tracks = []

        for i, track in enumerate(tracks):
            if progress_callback:
                progress_callback(i, total_tracks, f"Processing: {track['title']}")

            if not track["file_path"] or not os.path.exists(track["file_path"]):
                logger.warning(f"Source file not found: {track['file_path']}")
                continue

            # Create safe filename
            safe_artist = "".join(
                c for c in track["artist"] if c.isalnum() or c in (" ", "-", "_")
            ).strip()
            safe_title = "".join(
                c for c in track["title"] if c.isalnum() or c in (" ", "-", "_")
            ).strip()
            file_ext = os.path.splitext(track["file_path"])[1]

            # Simple filename: Artist - Title.ext
            device_filename = f"{safe_artist} - {safe_title}{file_ext}"
            dest_path = os.path.join(music_dir, device_filename)

            # Try to copy
            success = self.copy_track(track["file_path"], dest_path)

            # Store result for M3U creation
            processed_track = track.copy()
            processed_track["device_filename"] = device_filename
            processed_track["copied_successfully"] = success
            processed_tracks.append(processed_track)

            if success:
                tracks_copied += 1

        # Create M3U playlist only if we have successful copies
        m3u_path = None
        if any(t["copied_successfully"] for t in processed_tracks):
            m3u_path = self.create_m3u_playlist(
                playlist_data, processed_tracks, playlists_dir
            )

        result = {
            "playlist_name": playlist_name,
            "success": tracks_copied > 0,
            "message": f"Copied {tracks_copied}/{total_tracks} tracks",
            "tracks_copied": tracks_copied,
            "total_tracks": total_tracks,
            "m3u_path": m3u_path,
        }

        if progress_callback:
            progress_callback(total_tracks, total_tracks, "Complete")

        return result


class SyncWorker(QThread):
    """Background worker for sync operations."""

    progress = Signal(int, int, str)  # current, total, message
    playlist_complete = Signal(dict)  # playlist result
    finished = Signal(list)  # all results

    def __init__(
        self, sync_manager: SyncManager, playlists: List[Dict], device_path: str
    ):
        super().__init__()
        self.sync_manager = sync_manager
        self.playlists = playlists
        self.device_path = device_path
        self.results = []
        self._is_cancelled = False

    def run(self):
        """Execute sync operation in background thread."""
        try:
            status_manager = StatusManager()
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
            status_manager = StatusManager()
            status_manager.end_task(f"Sync error: {str(e)}", 5000)
            self.finished.emit([])

    def _progress_callback(self, current: int, total: int, message: str):
        """Forward progress updates to main thread."""
        self.progress.emit(current, total, message)

    def cancel(self):
        """Cancel the sync operation."""
        self._is_cancelled = True
