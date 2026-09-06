"""
SyncProfile — a named sync profile — and SyncProfileStore, which persists
a list of them to disk as JSON.
"""

from dataclasses import dataclass, field
import json
from pathlib import Path

from src.foundation.logger_config import logger
from src.sync.mtp_manager import MtpManager

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
    transcode_to_mp3  Convert lossless sources to MP3 on the way to the device.
    transcode_bitrate CBR bitrate for that conversion (e.g. "320k").
    prune_untracked   After syncing, delete destination files that belong to
                      playlists/moods no longer tracked by this profile. New
                      profiles default this on; legacy profiles saved before
                      this field existed load it off (see from_dict).
    """

    name: str
    path: str
    playlist_ids: list[int] = field(default_factory=list)
    mood_ids: list[int] = field(default_factory=list)
    clear_before_sync: bool = False
    device_uri: str = ""
    device_name: str = ""
    music_path: str = MtpManager.DEFAULT_MUSIC_PATH
    transcode_to_mp3: bool = False
    transcode_bitrate: str = "320k"
    prune_untracked: bool = True

    @property
    def is_mtp(self) -> bool:
        """True when this profile targets an MTP device."""
        return bool(self.device_uri)

    def to_dict(self) -> dict:
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
        return SyncProfile(
            name=data.get("name", "Unnamed"),
            path=data.get("path", ""),
            playlist_ids=data.get("playlist_ids", []),
            mood_ids=data.get("mood_ids", []),
            clear_before_sync=data.get("clear_before_sync", False),
            device_uri=data.get("device_uri", ""),
            device_name=data.get("device_name", ""),
            music_path=data.get("music_path", MtpManager.DEFAULT_MUSIC_PATH),
            transcode_to_mp3=data.get("transcode_to_mp3", False),
            transcode_bitrate=data.get("transcode_bitrate", "320k"),
            # Key absent => a profile saved before this feature; keep its old
            # additive behaviour (off) rather than silently deleting files on
            # the next sync. New profiles get the dataclass default (on).
            prune_untracked=data.get("prune_untracked", False),
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
        if not self.profiles_path.exists():
            return []
        try:
            with self.profiles_path.open(encoding="utf-8") as f:
                data = json.load(f)
            return [SyncProfile.from_dict(d) for d in data]
        except (OSError, json.JSONDecodeError) as e:
            logger.error(f"Failed to load sync profiles: {e}")
            return []

    def save(self, profiles: list[SyncProfile]):
        try:
            self.profiles_path.parent.mkdir(parents=True, exist_ok=True)
            with self.profiles_path.open("w", encoding="utf-8") as f:
                json.dump([p.to_dict() for p in profiles], f, indent=2)
        except OSError as e:
            logger.error(f"Failed to save sync profiles: {e}")
