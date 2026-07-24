"""
SyncManager — all sync logic in one place.

sync_playlist_to_device()  →  local folder copy (always available)
sync_playlist_to_mtp()     →  gio/aft MTP transfer to Android phone

Both return the same result-dict shape so SyncWorker and the UI are
completely agnostic about which path is running.
"""

import hashlib
import os
import shutil
from typing import Dict, List, Optional, Tuple

from src.db.db_helpers import GetFromDB
from src.core.logger_config import logger
from src.sync.mtp_manager import MtpDevice, MtpManager


class SyncManager:
    def __init__(self, db_session):
        self.session = db_session
        self.get_db = GetFromDB(db_session)
        self.mtp = MtpManager()

    # ------------------------------------------------------------------
    # Database helpers
    # ------------------------------------------------------------------

    def get_playlists(self) -> List[Dict]:
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

    def get_moods(self) -> List[Dict]:
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

    def _track_to_dict(self, track) -> Dict:
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

    def get_playlist_tracks(self, playlist_id: int) -> List[Dict]:
        playlist_tracks = self.get_db.get_all_entities(
            "PlaylistTracks", playlist_id=playlist_id
        )
        return [self._track_to_dict(pt.track) for pt in playlist_tracks]

    def get_mood_tracks(self, mood_id: int) -> List[Dict]:
        associations = self.get_db.get_all_entities(
            "MoodTrackAssociation", mood_id=mood_id
        )
        return [self._track_to_dict(assoc.track) for assoc in associations]

    def get_item_tracks(self, item_data: Dict) -> List[Dict]:
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

    def _is_mtp_duplicate(
        self, device: MtpDevice, local_path: str, remote_uri: str
    ) -> bool:
        """
        True if the remote file already exists with the same size as local.
        Size-only check — full MD5 over USB is too slow to be practical.
        """
        remote_size = self.mtp.remote_file_size(device, remote_uri)
        if remote_size < 0:
            return False  # file doesn't exist
        try:
            return os.path.getsize(local_path) == remote_size
        except OSError:
            return False

    def _build_m3u_content(
        self,
        playlist_data: Dict,
        tracks: List[Dict],
        music_subpath: str = "../Music",
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
    # Folder sync (original — kept intact)
    # ------------------------------------------------------------------

    def copy_track(self, source_path: str, dest_path: str) -> Tuple[bool, str]:
        """
        Copy a track to dest_path.
        Returns (success, status) where status is 'copied', 'skipped', or 'error'.
        """
        try:
            if not os.path.exists(source_path):
                logger.error(f"Source file not found: {source_path}")
                return False, "error"
            if self._is_local_duplicate(source_path, dest_path):
                logger.debug(f"Duplicate skipped: {dest_path}")
                return True, "skipped"
            os.makedirs(os.path.dirname(dest_path), exist_ok=True)
            shutil.copy2(source_path, dest_path)
            logger.debug(f"Copied: {source_path} → {dest_path}")
            return True, "copied"
        except Exception as e:
            logger.error(f"Error copying {source_path}: {e}")
            return False, "error"

    def clear_device_folder(self, device_path: str):
        """Remove music/ and playlists/ subdirectories before a fresh folder sync."""
        for subdir in ("music", "playlists"):
            target = os.path.join(device_path, subdir)
            if os.path.exists(target):
                shutil.rmtree(target)
                logger.info(f"Cleared folder: {target}")

    def sync_playlist_to_device(
        self,
        playlist_data: Dict,
        device_path: str,
        progress_callback=None,
    ) -> Dict:
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
                "total_tracks": 0,
            }

        tracks_copied = 0
        tracks_skipped = 0
        total_tracks = len(tracks)
        processed_tracks = []

        for i, track in enumerate(tracks):
            if progress_callback:
                progress_callback(i, total_tracks, f"Copying: {track['title']}")

            if not track["file_path"] or not os.path.exists(track["file_path"]):
                logger.warning(f"Source file not found: {track['file_path']}")
                continue

            ext = os.path.splitext(track["file_path"])[1]
            device_filename = self._safe_filename(track["artist"], track["title"], ext)
            dest_path = os.path.join(music_dir, device_filename)

            success, status = self.copy_track(track["file_path"], dest_path)

            processed_track = track.copy()
            processed_track["device_filename"] = device_filename
            processed_track["copied_successfully"] = success
            processed_tracks.append(processed_track)

            if success:
                tracks_copied += 1 if status == "copied" else 0
                tracks_skipped += 1 if status == "skipped" else 0

        m3u_content = self._build_m3u_content(playlist_data, processed_tracks)
        safe_name = "".join(
            c for c in playlist_name if c.isalnum() or c in (" ", "-", "_")
        ).strip()
        m3u_path = os.path.join(playlists_dir, f"{safe_name}.m3u")
        try:
            with open(m3u_path, "w", encoding="utf-8") as f:
                f.write(m3u_content)
        except Exception as e:
            logger.error(f"Failed to write M3U: {e}")

        return {
            "playlist_name": playlist_name,
            "success": tracks_copied > 0 or tracks_skipped > 0,
            "message": f"{tracks_copied} copied, {tracks_skipped} skipped",
            "tracks_copied": tracks_copied,
            "tracks_skipped": tracks_skipped,
            "total_tracks": total_tracks,
        }

    # ------------------------------------------------------------------
    # MTP sync
    # ------------------------------------------------------------------

    def _get_mtp_device(self, device_uri: str) -> Optional[MtpDevice]:
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
        playlist_data: Dict,
        device_uri: str,
        music_path: str,
        progress_callback=None,
    ) -> Dict:
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
                "total_tracks": 0,
            }

        # Ensure remote directories exist
        self.mtp.make_remote_dir(device, self.mtp.build_music_uri(device, music_path))
        self.mtp.make_remote_dir(
            device, self.mtp.build_playlists_dir_uri(device, music_path)
        )

        tracks = self.get_item_tracks(playlist_data)
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
                progress_callback(i, total_tracks, f"Sending: {track['title']}")

            local_path = track.get("file_path", "")
            if not local_path or not os.path.exists(local_path):
                logger.warning(f"Source file not found: {local_path}")
                continue

            ext = os.path.splitext(local_path)[1]
            device_filename = self._safe_filename(track["artist"], track["title"], ext)
            remote_uri = self.mtp.build_file_uri(device, music_path, device_filename)

            # Skip if file already exists with matching size
            if self._is_mtp_duplicate(device, local_path, remote_uri):
                logger.debug(f"MTP duplicate skipped: {device_filename}")
                tracks_skipped += 1
                processed_track = track.copy()
                processed_track["device_filename"] = device_filename
                processed_track["copied_successfully"] = True
                processed_tracks.append(processed_track)
                continue

            success = self.mtp.copy_file(device, local_path, remote_uri)

            processed_track = track.copy()
            processed_track["device_filename"] = device_filename
            processed_track["copied_successfully"] = success
            processed_tracks.append(processed_track)

            if success:
                tracks_copied += 1
            else:
                logger.error(f"Failed to send: {device_filename}")

        # Push M3U — relative path from Playlists dir back up to Music dir
        music_folder_name = music_path.strip("/").split("/")[-1]
        parent_path = (
            "/".join(music_path.strip("/").split("/")[:-1]) if "/" in music_path else ""
        )
        if parent_path:
            # If music is in a subdirectory, need to go up to parent then down to Music
            depth = len(music_path.strip("/").split("/"))
            go_up = "../" * depth
            music_subpath = f"{go_up}{music_folder_name}"
        else:
            # Simple case: Music and Playlists are siblings
            music_subpath = f"../{music_folder_name}"

        m3u_content = self._build_m3u_content(
            playlist_data,
            processed_tracks,
            music_subpath=music_subpath,
        )
        safe_name = "".join(
            c for c in playlist_name if c.isalnum() or c in (" ", "-", "_")
        ).strip()
        playlist_uri = self.mtp.build_playlist_uri(device, music_path, safe_name)
        self.mtp.copy_text_as_file(device, m3u_content, playlist_uri)

        return {
            "playlist_name": playlist_name,
            "success": tracks_copied > 0 or tracks_skipped > 0,
            "message": f"{tracks_copied} sent, {tracks_skipped} skipped",
            "tracks_copied": tracks_copied,
            "tracks_skipped": tracks_skipped,
            "total_tracks": total_tracks,
        }
