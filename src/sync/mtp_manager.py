"""
MTP back-end availability checks, MtpDevice, and MtpManager — device
detection and file transfer for Android devices connected over USB.
"""

import re
import shutil
import subprocess
import tempfile
import os
from dataclasses import dataclass
from pathlib import Path
from typing import List

from src.core.logger_config import logger

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
