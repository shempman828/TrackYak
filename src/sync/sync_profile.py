"""
SyncProfile — a named sync profile — and SyncProfileStore, which persists
a list of them to disk as JSON.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from src.core.logger_config import logger
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
    """

    name: str
    path: str
    playlist_ids: List[int] = field(default_factory=list)
    mood_ids: List[int] = field(default_factory=list)
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
            "mood_ids": self.mood_ids,
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
            mood_ids=data.get("mood_ids", []),
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
            from src.core.asset_paths import config as asset_config

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
