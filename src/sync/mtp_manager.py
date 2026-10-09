"""MTP back-end checks, MtpDevice and MtpManager: Android device detection and file transfer over USB."""

import contextlib
from dataclasses import dataclass
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

from src.foundation.logger_config import logger

DEFAULT_MUSIC_PATH = "Music"

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


def normalize_music_path(path: str) -> str:
    """Clean a user-typed device music path to 'a/b' form; empty falls back to DEFAULT_MUSIC_PATH."""
    # ".." and "." parts are dropped so the target can never climb above the storage root.
    parts = [p.strip() for p in (path or "").replace("\\", "/").split("/")]
    parts = [p for p in parts if p and p not in (".", "..")]
    return "/".join(parts) or DEFAULT_MUSIC_PATH


def _run_bounded(cmd: list[str], timeout: int) -> "subprocess.CompletedProcess | None":
    """Run `cmd` with captured text output; None on spawn failure or timeout, never an unbounded wait."""
    # subprocess.run(timeout=...) waits without limit after the kill; a gio call stuck in USB I/O
    # ignores SIGKILL, so give it a short grace period and then abandon it.
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    except OSError as e:
        logger.warning(f"Failed to spawn '{cmd[0]}': {e}")
        return None
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            proc.communicate(timeout=2)
        except subprocess.TimeoutExpired:
            logger.warning(f"'{' '.join(cmd)}' ignored SIGKILL after timeout — abandoning (wedged MTP backend?)")
            for pipe in (proc.stdout, proc.stderr):
                if pipe is not None:
                    with contextlib.suppress(OSError):
                        pipe.close()
        else:
            logger.warning(f"'{' '.join(cmd)}' timed out after {timeout}s")
        return None
    return subprocess.CompletedProcess(cmd, proc.returncode, stdout, stderr)


# ---------------------------------------------------------------------------
# MtpDevice dataclass
# ---------------------------------------------------------------------------


@dataclass
class MtpDevice:
    """One MTP device detected over USB: its gio URI (or aft name), display name, and backend."""

    uri: str
    name: str
    backend: str = "gio"

    @property
    def display_name(self) -> str:
        """Label shown in the UI, e.g. 'Galaxy S21  —  mtp://...'."""
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
    """Detect Android devices and transfer files via gio (primary) or aft-mtp-cli (fallback)."""

    # Always write through the mtp:// URI: the gvfs FUSE path is read-only on most systems.

    DEFAULT_MUSIC_PATH = DEFAULT_MUSIC_PATH

    # Timeout in seconds for one file transfer / listing / delete.
    _TIMEOUT = 60

    def __init__(self):
        # (device uri, normalized music path) -> storage-qualified relative music path.
        self._music_root_cache: dict[tuple[str, str], str] = {}

    # ---------------------------------------------------------------------------
    # Device detection
    # ---------------------------------------------------------------------------

    def list_devices(self) -> list[MtpDevice]:
        """Return connected MTP devices (gio first, then aft); [] if none or no backend."""
        if gio_available():
            devices = self._gio_list_devices()
            if devices:
                return devices

        if aft_available():
            return self._aft_list_devices()

        return []

    def _gio_list_devices(self) -> list[MtpDevice]:
        """Parse the Volume name + activation_root pairs out of `gio mount -li`."""
        result = _run_bounded(["gio", "mount", "-li"], timeout=10)
        if result is None:
            return []

        devices: list[MtpDevice] = []
        seen: set[str] = set()
        current_name = None

        for raw in result.stdout.splitlines():
            line = raw.strip()

            vol_match = re.match(r"^Volume\(\d+\):\s*(.+)$", line)
            if vol_match:
                current_name = vol_match.group(1).strip()
                continue

            uri_match = re.match(r"^activation_root=(mtp://.+)$", line)
            if uri_match:
                uri = uri_match.group(1).strip()
                if not uri.endswith("/"):
                    uri += "/"
                # A device can be listed under more than one volume block.
                if uri not in seen:
                    seen.add(uri)
                    devices.append(MtpDevice(uri=uri, name=current_name or uri, backend="gio"))
                current_name = None

        return devices

    def _aft_list_devices(self) -> list[MtpDevice]:
        """Parse 'Device N: Name' lines out of `aft-mtp-cli --list-devices`."""
        result = _run_bounded(["aft-mtp-cli", "--list-devices"], timeout=10)
        if result is None:
            return []

        devices = []
        for line in result.stdout.splitlines():
            m = re.match(r"Device\s+\d+:\s*(.+)", line.strip())
            if m:
                name = m.group(1).strip()
                devices.append(MtpDevice(uri=name, name=name, backend="aft"))

        return devices

    # ---------------------------------------------------------------------------
    # Mount
    # ---------------------------------------------------------------------------

    def ensure_mounted(self, device: MtpDevice) -> bool:
        """Mount a gio device before transfer; True if mounted (aft devices always count as mounted)."""
        if device.backend != "gio":
            return True
        result = _run_bounded(["gio", "mount", device.uri], timeout=15)
        # 0 = mounted now; 1 is also what gio returns when it is already mounted.
        return result is not None and result.returncode in (0, 1)

    # ---------------------------------------------------------------------------
    # Remote file info
    # ---------------------------------------------------------------------------

    @staticmethod
    def can_list(device: MtpDevice) -> bool:
        """True if this backend can list a remote folder (needed for diff, verify and prune)."""
        return device.backend == "gio"

    def list_remote_dir(self, device: MtpDevice, remote_dir_uri: str) -> dict[str, int]:
        """Return {filename: size} for one remote folder via one `gio list`; {} if it can't be listed."""
        if not self.can_list(device):
            return {}
        result = _run_bounded(["gio", "list", "-a", "standard::size", remote_dir_uri], timeout=self._TIMEOUT)
        if result is None or result.returncode != 0:
            logger.debug(f"gio list failed for {remote_dir_uri}")
            return {}

        listing: dict[str, int] = {}
        for line in result.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) < 2:
                continue
            try:
                listing[parts[0]] = int(parts[1])
            except ValueError:
                continue
        return listing

    def _list_root_names(self, device: MtpDevice) -> list[str] | None:
        """Names at the device root (its storage volumes); None if the root can't be listed."""
        result = _run_bounded(["gio", "list", device.uri.rstrip("/") + "/"], timeout=10)
        if result is None or result.returncode != 0:
            return None
        return [line.strip() for line in result.stdout.splitlines() if line.strip()]

    # ---------------------------------------------------------------------------
    # File transfer
    # ---------------------------------------------------------------------------

    def copy_file(self, device: MtpDevice, local_path: str, remote_uri: str) -> bool:
        """Copy a local file to the device with `gio copy` or aft-mtp-cli."""
        if device.backend == "gio":
            return self._gio_copy(local_path, remote_uri)
        return self._aft_copy(local_path, remote_uri)

    def _gio_copy(self, local_path: str, remote_uri: str) -> bool:
        """Push one file with `gio copy`; True on success."""
        try:
            # A raw path with spaces/special chars gets mangled by gio; a file:// URI does not.
            source_uri = Path(local_path).as_uri()
        except ValueError as e:
            logger.error(f"gio copy error: {e}")
            return False
        result = _run_bounded(["gio", "copy", source_uri, remote_uri], timeout=self._TIMEOUT)
        if result is None:
            logger.error(f"gio copy timed out or could not start: {local_path}")
            return False
        if result.returncode != 0:
            logger.error(f"gio copy failed ({local_path} → {remote_uri}): {result.stderr.strip()}")
            return False
        return True

    def _aft_copy(self, local_path: str, remote_path: str) -> bool:
        """Push one file with aft-mtp-cli; True on success."""
        result = _run_bounded(["aft-mtp-cli", "push", local_path, remote_path], timeout=self._TIMEOUT)
        if result is None:
            logger.error(f"aft-mtp-cli push timed out or could not start: {local_path}")
            return False
        if result.returncode != 0:
            logger.error(f"aft-mtp-cli push failed ({local_path}): {result.stderr.strip()}")
            return False
        return True

    def copy_text_as_file(self, device: MtpDevice, content: str, remote_uri: str) -> bool:
        """Write `content` (e.g. an M3U) to a remote file via a local temp file."""
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", suffix=".m3u", delete=False, encoding="utf-8") as tmp:
                tmp.write(content)
                tmp_path = tmp.name
            return self.copy_file(device, tmp_path, remote_uri)
        except OSError as e:
            logger.error(f"copy_text_as_file failed: {e}")
            return False
        finally:
            if tmp_path is not None:
                with contextlib.suppress(OSError):
                    Path(tmp_path).unlink(missing_ok=True)

    # ---------------------------------------------------------------------------
    # Remote directory operations
    # ---------------------------------------------------------------------------

    def make_remote_dir(self, device: MtpDevice, remote_uri: str) -> bool:
        """Create a directory (and parents) on the device."""
        if device.backend != "gio":
            return True
        result = _run_bounded(["gio", "mkdir", "-p", remote_uri], timeout=15)
        if result is None:
            logger.error(f"gio mkdir timed out ({remote_uri})")
            return False
        return result.returncode == 0 or "already exists" in result.stderr.lower()

    def remove_remote_dir(self, device: MtpDevice, remote_uri: str) -> bool:
        """Recursively delete a remote directory; True if it is gone afterwards."""
        if device.backend != "gio":
            return True
        dir_uri = remote_uri.rstrip("/") + "/"
        # `gio remove` has no recursive mode and refuses a non-empty folder, so empty it first.
        listing = _run_bounded(["gio", "list", "-a", "standard::type", dir_uri], timeout=self._TIMEOUT)
        if listing is not None and listing.returncode == 0:
            for line in listing.stdout.splitlines():
                parts = line.split("\t")
                if not parts[0]:
                    continue
                child = dir_uri + parts[0]
                if len(parts) >= 3 and parts[2] == "(directory)":
                    self.remove_remote_dir(device, child)
                else:
                    self.delete_remote_file(device, child)
        result = _run_bounded(["gio", "remove", "-f", dir_uri.rstrip("/")], timeout=self._TIMEOUT)
        if result is None or result.returncode != 0:
            detail = result.stderr.strip() if result is not None else "timed out"
            logger.error(f"gio remove failed ({remote_uri}): {detail}")
            return False
        return True

    def delete_remote_file(self, device: MtpDevice, remote_uri: str) -> bool:
        """Delete one remote file (gio only; aft has no listing, so prune never gets here)."""
        if device.backend != "gio":
            return False
        result = _run_bounded(["gio", "remove", remote_uri], timeout=self._TIMEOUT)
        if result is None or result.returncode != 0:
            detail = result.stderr.strip() if result is not None else "timed out"
            logger.error(f"gio remove failed ({remote_uri}): {detail}")
            return False
        return True

    # ---------------------------------------------------------------------------
    # URI construction helpers
    # ---------------------------------------------------------------------------

    def resolve_music_dir(self, device: MtpDevice, music_path: str) -> str:
        """The storage-qualified relative music folder, e.g. 'Internal shared storage/Music'."""
        path = normalize_music_path(music_path)
        key = (device.uri, path)
        cached = self._music_root_cache.get(key)
        if cached is not None:
            return cached
        # Only a real mtp:// root holds storage volumes; other schemes (file:// test stand-ins, aft names)
        # take the path as given.
        if device.backend != "gio" or not device.uri.startswith("mtp://"):
            return path
        roots = self._list_root_names(device)
        if not roots:
            return path  # device asleep / not listable: don't cache, retry next time
        first = path.split("/", 1)[0].casefold()
        if any(r.casefold() == first for r in roots):
            resolved = path  # the user named a storage volume explicitly
        else:
            storage = next((r for r in roots if "internal" in r.casefold()), roots[0])
            resolved = f"{storage}/{path}"
        self._music_root_cache[key] = resolved
        return resolved

    def build_music_uri(self, device: MtpDevice, music_path: str) -> str:
        """Full gio URI of the music folder (trailing slash)."""
        return f"{device.uri.rstrip('/')}/{self.resolve_music_dir(device, music_path)}/"

    def build_file_uri(self, device: MtpDevice, music_path: str, filename: str) -> str:
        """Full gio URI of one music file."""
        return f"{self.build_music_uri(device, music_path)}{filename}"

    def build_playlists_dir_uri(self, device: MtpDevice, music_path: str) -> str:
        """Full gio URI of the Playlists folder, a sibling of the music folder."""
        parent = self.resolve_music_dir(device, music_path).rpartition("/")[0]
        base = device.uri.rstrip("/")
        return f"{base}/{parent}/Playlists/" if parent else f"{base}/Playlists/"

    def build_playlist_uri(self, device: MtpDevice, music_path: str, safe_playlist_name: str) -> str:
        """Full gio URI of one M3U file in the Playlists folder."""
        return f"{self.build_playlists_dir_uri(device, music_path)}{safe_playlist_name}.m3u"

    @staticmethod
    def m3u_music_subpath(music_path: str) -> str:
        """Relative path from the Playlists folder back to the music folder."""
        # Playlists is always a sibling of the music folder, at any depth.
        return f"../{normalize_music_path(music_path).rsplit('/', 1)[-1]}"
