# sync_utility.py
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from PySide6.QtCore import QThread, Signal

from db_helpers import GetFromDB
from logger_config import logger
from status_utility import StatusManager

# ---------------------------------------------------------------------------
# MTP back-end availability checks
# ---------------------------------------------------------------------------


def gio_available() -> bool:
    """Return True if the 'gio' command is present on this system."""
    return shutil.which("gio") is not None


def aft_available() -> bool:
    """Return True if android-file-transfer (aft-mtp-cli) is installed."""
    return shutil.which("aft-mtp-cli") is not None


def mtp_available() -> bool:
    """Return True if at least one MTP back-end is available."""
    return gio_available() or aft_available()


# ---------------------------------------------------------------------------
# MtpDevice dataclass
# ---------------------------------------------------------------------------


@dataclass
class MtpDevice:
    """
    Represents a single MTP device (Android phone) detected over USB.

    uri       -- the GIO protocol URI used for all gio operations,
                 e.g. "mtp://SAMSUNG_SM-G991B_R3CN90BXXXX/"
    name      -- friendly display name parsed from gio output,
                 e.g. "Galaxy S21"
    backend   -- which tool detected this device: "gio" or "aft"
    """

    uri: str
    name: str
    backend: str = "gio"

    @property
    def display_name(self) -> str:
        """Label shown in the UI, e.g. 'Galaxy S21  (mtp://...)'"""
        if self.name and self.name != self.uri:
            return f"{self.name}  —  {self.uri}"
        return self.uri

    @property
    def short_name(self) -> str:
        """Just the friendly name, falling back to the URI."""
        return self.name if self.name else self.uri


# ---------------------------------------------------------------------------
# MtpManager — device detection and file transfer
# ---------------------------------------------------------------------------


class MtpManager:
    """
    Detects Android devices and transfers files via MTP.

    Primary back-end:  gio (ships with gvfs-backends on all Ubuntu desktops,
                       works on GNOME / KDE / XFCE / Wayland)
    Fallback back-end: aft-mtp-cli (android-file-transfer-linux)

    All public methods return safe empty results on failure so callers
    never need to catch exceptions.

    Key design note
    ---------------
    gio copy MUST be used with the mtp:// URI — NOT with the FUSE path at
    /run/user/.../gvfs/mtp:...  The FUSE path is read-only on most systems
    and raises "Operation not supported" for writes.  We always construct
    remote URIs as:  {device_uri}{music_folder}/{filename}
    """

    DEFAULT_MUSIC_PATH = "Music"  # relative path on the device

    # Timeout in seconds for individual subprocess calls
    _TIMEOUT = 60

    # ---------------------------------------------------------------------------
    # Device detection
    # ---------------------------------------------------------------------------

    def list_devices(self) -> List[MtpDevice]:
        """
        Return a list of connected MTP devices.
        Tries gio first, falls back to aft-mtp-cli.
        Returns [] if no devices found or no backend available.
        """
        if gio_available():
            devices = self._gio_list_devices()
            if devices:
                return devices

        if aft_available():
            return self._aft_list_devices()

        return []

    def _gio_list_devices(self) -> List[MtpDevice]:
        """
        Parse `gio mount -li` output to find MTP devices.

        The relevant block in the output looks like:

            Volume(0): Galaxy S21
              Type: GProxyVolume (GProxyVolumeMonitorMTP)
              activation_root=mtp://SAMSUNG_SM-G991B_R3CN90BXXXX/

        We collect Volume name + activation_root pairs.
        """
        try:
            result = subprocess.run(
                ["gio", "mount", "-li"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            output = result.stdout
        except Exception as e:
            logger.warning(f"gio mount -li failed: {e}")
            return []

        devices = []
        current_name = None

        for line in output.splitlines():
            line = line.strip()

            # "Volume(N): Device Name"
            vol_match = re.match(r"^Volume\(\d+\):\s*(.+)$", line)
            if vol_match:
                current_name = vol_match.group(1).strip()
                continue

            # "activation_root=mtp://..."
            uri_match = re.match(r"^activation_root=(mtp://.+)$", line)
            if uri_match:
                uri = uri_match.group(1).strip()
                if not uri.endswith("/"):
                    uri += "/"
                name = current_name or uri
                devices.append(MtpDevice(uri=uri, name=name, backend="gio"))
                current_name = None

        return devices

    def _aft_list_devices(self) -> List[MtpDevice]:
        """
        Fall back to aft-mtp-cli --list-devices.
        Typical output: "Device 0: Samsung Galaxy S21"
        """
        try:
            result = subprocess.run(
                ["aft-mtp-cli", "--list-devices"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            output = result.stdout
        except Exception as e:
            logger.warning(f"aft-mtp-cli --list-devices failed: {e}")
            return []

        devices = []
        for line in output.splitlines():
            m = re.match(r"Device\s+\d+:\s*(.+)", line.strip())
            if m:
                name = m.group(1).strip()
                devices.append(MtpDevice(uri=name, name=name, backend="aft"))

        return devices

    # ---------------------------------------------------------------------------
    # Mount
    # ---------------------------------------------------------------------------

    def ensure_mounted(self, device: MtpDevice) -> bool:
        """
        Ensure the device is mounted via gio before transferring files.
        aft devices are always considered mounted (aft handles it internally).
        Returns True if already mounted or mount succeeds.
        """
        if device.backend != "gio":
            return True
        try:
            result = subprocess.run(
                ["gio", "mount", device.uri],
                capture_output=True,
                text=True,
                timeout=15,
            )
            # 0 = success; 1 often means already mounted — both are fine
            return result.returncode in (0, 1)
        except Exception as e:
            logger.warning(f"gio mount {device.uri} failed: {e}")
            return False

    # ---------------------------------------------------------------------------
    # Remote file info
    # ---------------------------------------------------------------------------

    def remote_file_size(self, device: MtpDevice, remote_uri: str) -> int:
        """
        Return the byte size of a remote file, or -1 if it doesn't exist
        or can't be queried.  Uses `gio info` — avoids the FUSE path entirely.
        """
        if device.backend != "gio":
            return -1
        try:
            result = subprocess.run(
                ["gio", "info", remote_uri],
                capture_output=True,
                text=True,
                timeout=self._TIMEOUT,
            )
            if result.returncode != 0:
                return -1
            for line in result.stdout.splitlines():
                m = re.match(r"\s*standard::size:\s*(\d+)", line)
                if m:
                    return int(m.group(1))
            return -1
        except Exception as e:
            logger.debug(f"gio info failed for {remote_uri}: {e}")
            return -1

    # ---------------------------------------------------------------------------
    # File transfer
    # ---------------------------------------------------------------------------

    def copy_file(
        self,
        device: MtpDevice,
        local_path: str,
        remote_uri: str,
    ) -> bool:
        """
        Copy a local file to the device.
        Uses `gio copy` for gio devices — this is the correct method that
        avoids the FUSE write limitation.
        Uses aft-mtp-cli for aft devices.
        """
        if device.backend == "gio":
            return self._gio_copy(local_path, remote_uri)
        else:
            return self._aft_copy(local_path, remote_uri)

    def _gio_copy(self, local_path: str, remote_uri: str) -> bool:
        try:
            # gio copy requires the source to be a file:// URI when the path
            # contains spaces or special characters — passing a raw path causes
            # gio to mangle it.  Path.as_uri() handles the encoding correctly.
            source_uri = Path(local_path).as_uri()
            result = subprocess.run(
                ["gio", "copy", source_uri, remote_uri],
                capture_output=True,
                text=True,
                timeout=self._TIMEOUT,
            )
            if result.returncode != 0:
                logger.error(
                    f"gio copy failed ({local_path} → {remote_uri}): "
                    f"{result.stderr.strip()}"
                )
                return False
            return True
        except subprocess.TimeoutExpired:
            logger.error(f"gio copy timed out: {local_path}")
            return False
        except Exception as e:
            logger.error(f"gio copy error: {e}")
            return False

    def _aft_copy(self, local_path: str, remote_path: str) -> bool:
        try:
            result = subprocess.run(
                ["aft-mtp-cli", "push", local_path, remote_path],
                capture_output=True,
                text=True,
                timeout=self._TIMEOUT,
            )
            if result.returncode != 0:
                logger.error(
                    f"aft-mtp-cli push failed ({local_path}): {result.stderr.strip()}"
                )
                return False
            return True
        except subprocess.TimeoutExpired:
            logger.error(f"aft-mtp-cli push timed out: {local_path}")
            return False
        except Exception as e:
            logger.error(f"aft-mtp-cli push error: {e}")
            return False

    def copy_text_as_file(
        self,
        device: MtpDevice,
        content: str,
        remote_uri: str,
    ) -> bool:
        """
        Write a string (e.g. M3U content) to a remote file.
        Writes to a local temp file first, then copies via the normal path.
        """
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".m3u", delete=False, encoding="utf-8"
            ) as tmp:
                tmp.write(content)
                tmp_path = tmp.name
            result = self.copy_file(device, tmp_path, remote_uri)
            os.unlink(tmp_path)
            return result
        except Exception as e:
            logger.error(f"copy_text_as_file failed: {e}")
            return False

    # ---------------------------------------------------------------------------
    # Remote directory operations
    # ---------------------------------------------------------------------------

    def make_remote_dir(self, device: MtpDevice, remote_uri: str) -> bool:
        """Create a directory on the device."""
        if device.backend != "gio":
            return True
        try:
            result = subprocess.run(
                ["gio", "mkdir", "-p", remote_uri],
                capture_output=True,
                text=True,
                timeout=15,
            )
            return result.returncode == 0 or "already exists" in result.stderr.lower()
        except Exception as e:
            logger.error(f"gio mkdir failed ({remote_uri}): {e}")
            return False

    def remove_remote_dir(self, device: MtpDevice, remote_uri: str) -> bool:
        """Recursively delete a remote directory."""
        if device.backend != "gio":
            return True
        try:
            result = subprocess.run(
                ["gio", "remove", remote_uri],
                capture_output=True,
                text=True,
                timeout=30,
            )
            return result.returncode == 0
        except Exception as e:
            logger.error(f"gio remove failed ({remote_uri}): {e}")
            return False

    # ---------------------------------------------------------------------------
    # URI construction helpers
    # ---------------------------------------------------------------------------

    def build_music_uri(self, device: MtpDevice, music_path: str) -> str:
        """
        Build the full gio URI for the music folder on this device.
        If music_path doesn't include a storage volume, try to detect it.
        """
        base = device.uri.rstrip("/")

        # If music_path already includes a storage volume, use it directly
        if music_path and ("Internal" in music_path or "storage" in music_path):
            path = music_path.strip("/")
            return f"{base}/{path}/"

        # Try to detect the storage volume
        try:
            # List the device root to find storage volume
            result = subprocess.run(
                ["gio", "list", base + "/"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                items = result.stdout.splitlines()
                # Look for common storage volume names
                for item in items:
                    if "Internal" in item or "storage" in item or "emulated" in item:
                        storage = item.strip()
                        path = music_path.strip("/")
                        if path:
                            return f"{base}/{storage}/{path}/"
                        return f"{base}/{storage}/"
        except Exception:
            pass

        # Fallback to original behavior
        path = music_path.strip("/")
        return f"{base}/{path}/"

    def build_file_uri(self, device: MtpDevice, music_path: str, filename: str) -> str:
        """Build the full gio URI for a single music file on this device."""
        base = device.uri.rstrip("/")
        path = music_path.strip("/")
        return f"{base}/{path}/{filename}"

    def build_playlists_dir_uri(self, device: MtpDevice, music_path: str) -> str:
        """
        Build the URI for the companion Playlists folder.
        Now creates "Playlists" folder at the same level as the Music folder.
        """
        base = device.uri.rstrip("/")
        # Get the parent directory of the music path
        path_parts = music_path.strip("/").split("/")
        if len(path_parts) > 1:
            # If music_path has subdirectories, keep the parent structure
            parent_path = "/".join(path_parts[:-1])
            return f"{base}/{parent_path}/Playlists/"
        else:
            # If music_path is just a single folder, put Playlists at same level
            return f"{base}/Playlists/"

    def build_playlist_uri(
        self, device: MtpDevice, music_path: str, safe_playlist_name: str
    ) -> str:
        """Build the full URI for a single M3U file in the Playlists folder."""
        base = device.uri.rstrip("/")
        # Get the parent directory of the music path
        path_parts = music_path.strip("/").split("/")
        if len(path_parts) > 1:
            # If music_path has subdirectories, keep the parent structure
            parent_path = "/".join(path_parts[:-1])
            return f"{base}/{parent_path}/Playlists/{safe_playlist_name}.m3u"
        else:
            # If music_path is just a single folder, put Playlists at same level
            return f"{base}/Playlists/{safe_playlist_name}.m3u"


# ---------------------------------------------------------------------------
# SyncProfile dataclass
# ---------------------------------------------------------------------------


@dataclass
class SyncProfile:
    """
    A named sync profile.

    Fields
    ------
    name              Display name chosen by the user.
    path              Local folder path for folder-based sync (fallback).
    playlist_ids      IDs of playlists selected for this profile.
    clear_before_sync Wipe destination before syncing.
    device_uri        MTP device URI  (empty = use folder sync).
    device_name       Friendly name for display in the UI.
    music_path        Target music folder on the device (relative path).
    """

    name: str
    path: str
    playlist_ids: List[int] = field(default_factory=list)
    clear_before_sync: bool = False
    device_uri: str = ""
    device_name: str = ""
    music_path: str = MtpManager.DEFAULT_MUSIC_PATH

    @property
    def is_mtp(self) -> bool:
        """True when this profile targets an MTP device."""
        return bool(self.device_uri)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "path": self.path,
            "playlist_ids": self.playlist_ids,
            "clear_before_sync": self.clear_before_sync,
            "device_uri": self.device_uri,
            "device_name": self.device_name,
            "music_path": self.music_path,
        }

    @staticmethod
    def from_dict(data: dict) -> "SyncProfile":
        return SyncProfile(
            name=data.get("name", "Unnamed"),
            path=data.get("path", ""),
            playlist_ids=data.get("playlist_ids", []),
            clear_before_sync=data.get("clear_before_sync", False),
            device_uri=data.get("device_uri", ""),
            device_name=data.get("device_name", ""),
            music_path=data.get("music_path", MtpManager.DEFAULT_MUSIC_PATH),
        )


# ---------------------------------------------------------------------------
# SyncProfileStore
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
    """
    All sync logic in one place.

    sync_playlist_to_device()  →  local folder copy (always available)
    sync_playlist_to_mtp()     →  gio/aft MTP transfer to Android phone

    Both return the same result-dict shape so SyncWorker and the UI are
    completely agnostic about which path is running.
    """

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
        """Sync a single playlist to a local folder path."""
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
        Sync a single playlist to a connected Android device via MTP.

        Track files land in:   {device_uri}/{music_path}/
        M3U playlist lands in: {device_uri}/{music_path}_Playlists/

        Returns the same result-dict shape as sync_playlist_to_device()
        so SyncWorker and the UI don't need to know which method ran.
        """
        playlist_id = playlist_data["playlist_id"]
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


# ---------------------------------------------------------------------------
# SyncWorker — background thread
# ---------------------------------------------------------------------------


class SyncWorker(QThread):
    """
    Runs the sync operation in a background thread.

    Accepts a SyncProfile and automatically picks MTP or folder sync.
    Both paths emit identical signals so the UI is fully agnostic.
    """

    progress = Signal(int, int, str)  # current, total, message
    playlist_complete = Signal(dict)  # one playlist result
    finished = Signal(list)  # all results

    def __init__(
        self,
        sync_manager: SyncManager,
        playlists: List[Dict],
        profile: SyncProfile,
    ):
        super().__init__()
        self.sync_manager = sync_manager
        self.playlists = playlists
        self.profile = profile
        self.results = []
        self._is_cancelled = False

    def run(self):
        try:
            status_manager = StatusManager
            profile = self.profile

            # ── Optionally clear destination ────────────────────────────────
            if profile.clear_before_sync and not self._is_cancelled:
                self.progress.emit(0, 1, "Clearing destination…")
                if profile.is_mtp:
                    self.sync_manager.clear_mtp_folders(
                        profile.device_uri, profile.music_path
                    )
                else:
                    self.sync_manager.clear_device_folder(profile.path)

            # ── Sync each playlist ──────────────────────────────────────────
            total = len(self.playlists)
            for i, playlist in enumerate(self.playlists):
                if self._is_cancelled:
                    status_manager.show_message("Sync cancelled", 3000)
                    break

                self.progress.emit(i, total, f"Starting: {playlist['name']}")
                status_manager.show_message(f"Syncing: {playlist['name']}", 0)

                if profile.is_mtp:
                    result = self.sync_manager.sync_playlist_to_mtp(
                        playlist,
                        profile.device_uri,
                        profile.music_path,
                        self._progress_callback,
                    )
                else:
                    result = self.sync_manager.sync_playlist_to_device(
                        playlist,
                        profile.path,
                        self._progress_callback,
                    )

                self.results.append(result)
                self.playlist_complete.emit(result)

            self.finished.emit(self.results)

        except Exception as e:
            logger.error(f"SyncWorker error: {e}")
            StatusManager.end_task(f"Sync error: {str(e)}", 5000)
            self.finished.emit([])

    def _progress_callback(self, current: int, total: int, message: str):
        self.progress.emit(current, total, message)

    def cancel(self):
        self._is_cancelled = True
