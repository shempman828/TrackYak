"""SyncProfile (one named sync target) and SyncProfileStore (JSON persistence for the list)."""

from dataclasses import dataclass, field
import json
from pathlib import Path
import tempfile
import time

from src.foundation.logger_config import logger
from src.sync.mtp_manager import DEFAULT_MUSIC_PATH, normalize_music_path
from src.sync.transcode import ALLOWED_BITRATES, DEFAULT_BITRATE

# ---------------------------------------------------------------------------
# SyncProfile dataclass
# ---------------------------------------------------------------------------


def _int_list(value) -> list[int]:
    """Keep only the int entries of a JSON list (anything else becomes [])."""
    if not isinstance(value, list):
        return []
    return [v for v in value if isinstance(v, int) and not isinstance(v, bool)]


def _str(value, default: str = "") -> str:
    """`value` if it is a str, else `default`."""
    return value if isinstance(value, str) else default


@dataclass
class SyncProfile:
    """A named sync target: destination (folder or MTP device), selection, and sync options."""

    name: str
    path: str  # local folder for folder sync
    playlist_ids: list[int] = field(default_factory=list)
    mood_ids: list[int] = field(default_factory=list)
    clear_before_sync: bool = False
    device_uri: str = ""  # empty = folder sync
    device_name: str = ""
    music_path: str = DEFAULT_MUSIC_PATH  # relative folder on the device
    transcode_to_mp3: bool = False
    transcode_bitrate: str = DEFAULT_BITRATE
    prune_untracked: bool = True  # new profiles on; legacy profiles load off (see from_dict)

    @property
    def is_mtp(self) -> bool:
        """True when this profile targets an MTP device."""
        return bool(self.device_uri)

    def to_dict(self) -> dict:
        """JSON-ready dict of every field."""
        return {
            "name": self.name,
            "path": self.path,
            "playlist_ids": self.playlist_ids,
            "mood_ids": self.mood_ids,
            "clear_before_sync": self.clear_before_sync,
            "device_uri": self.device_uri,
            "device_name": self.device_name,
            "music_path": self.music_path,
            "transcode_to_mp3": self.transcode_to_mp3,
            "transcode_bitrate": self.transcode_bitrate,
            "prune_untracked": self.prune_untracked,
        }

    @staticmethod
    def from_dict(data: dict) -> "SyncProfile":
        """Build a profile from saved JSON, replacing wrong-typed or invalid values with defaults."""
        bitrate = data.get("transcode_bitrate", DEFAULT_BITRATE)
        return SyncProfile(
            name=_str(data.get("name"), "Unnamed") or "Unnamed",
            path=_str(data.get("path")),
            playlist_ids=_int_list(data.get("playlist_ids")),
            mood_ids=_int_list(data.get("mood_ids")),
            clear_before_sync=bool(data.get("clear_before_sync", False)),
            device_uri=_str(data.get("device_uri")),
            device_name=_str(data.get("device_name")),
            music_path=normalize_music_path(_str(data.get("music_path"), DEFAULT_MUSIC_PATH)),
            transcode_to_mp3=bool(data.get("transcode_to_mp3", False)),
            transcode_bitrate=bitrate if bitrate in ALLOWED_BITRATES else DEFAULT_BITRATE,
            # Key absent => saved before pruning existed; stay additive rather than delete files unasked.
            prune_untracked=bool(data.get("prune_untracked", False)),
        )


# ---------------------------------------------------------------------------
# SyncProfileStore
# ---------------------------------------------------------------------------


class SyncProfileStore:
    """Load and save sync profiles to disk as JSON."""

    def __init__(self, profiles_path: str | None = None):
        if profiles_path is None:
            from src.foundation.asset_paths import config as asset_config

            profiles_path = str(Path(asset_config("config.ini")).parent / "sync_profiles.json")
        self.profiles_path = Path(profiles_path)

    def load(self) -> list[SyncProfile]:
        """Read the profiles file; a corrupt file is backed up (not lost) and [] returned."""
        if not self.profiles_path.exists():
            return []
        try:
            with self.profiles_path.open(encoding="utf-8") as f:
                data = json.load(f)
        except OSError as e:
            logger.error(f"Failed to read sync profiles: {e}")
            return []
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            logger.error(f"Sync profiles file is corrupt: {e}")
            self._backup_corrupt_file()
            return []
        if not isinstance(data, list):
            logger.error("Sync profiles file does not hold a list")
            self._backup_corrupt_file()
            return []
        profiles = []
        for entry in data:
            if isinstance(entry, dict):
                profiles.append(SyncProfile.from_dict(entry))
            else:
                logger.warning(f"Skipping malformed sync profile entry: {entry!r}")
        return profiles

    def save(self, profiles: list[SyncProfile]) -> bool:
        """Write all profiles atomically; True on success."""
        tmp_name = None
        try:
            self.profiles_path.parent.mkdir(parents=True, exist_ok=True)
            # Temp file + atomic rename: a crash mid-write never leaves a truncated profiles file.
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=self.profiles_path.parent, prefix=".sync_profiles.", suffix=".tmp", delete=False) as f:
                tmp_name = f.name
                json.dump([p.to_dict() for p in profiles], f, indent=2)
            Path(tmp_name).replace(self.profiles_path)
            return True
        except OSError as e:
            logger.error(f"Failed to save sync profiles: {e}")
            if tmp_name:
                Path(tmp_name).unlink(missing_ok=True)
            return False

    def _backup_corrupt_file(self) -> None:
        """Move an unreadable profiles file aside so the next save can't overwrite it."""
        backup = self.profiles_path.with_name(f"{self.profiles_path.name}.corrupt-{time.strftime('%Y%m%d-%H%M%S')}")
        try:
            self.profiles_path.replace(backup)
            logger.warning(f"Moved corrupt sync profiles file to {backup}")
        except OSError as e:
            logger.error(f"Could not back up corrupt sync profiles file: {e}")
