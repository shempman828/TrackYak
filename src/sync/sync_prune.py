"""SyncPruneMixin: remove destination files that belong to playlists/moods no longer in a profile."""

from collections.abc import Callable
from pathlib import Path

from src.foundation.logger_config import logger
from src.sync.sync_naming import is_m3u_name, is_prunable_music_name, predicted_device_filename, safe_playlist_name
from src.sync.transcode import ffmpeg_available


class SyncPruneMixin:
    """Prune pass for SyncManager; needs get_item_tracks(), _list_local_pool(), _get_mtp_device() and self.mtp."""

    @staticmethod
    def _empty_prune_result() -> dict:
        """A prune result that removed nothing."""
        return {"removed_tracks": [], "removed_playlists": [], "removed_count": 0}

    def _desired_device_contents(self, tracked_items: list[dict], transcode_to_mp3: bool) -> tuple[set[str], set[str]]:
        """(music filenames, m3u filenames) that should be on the device for `tracked_items`."""
        desired_music: set[str] = set()
        for item in tracked_items:
            for track in self.get_item_tracks(item):
                if not track.get("file_path"):
                    continue  # no source on record: it can't have been copied
                desired_music.add(predicted_device_filename(track, transcode_to_mp3))
        desired_m3u = {f"{safe_playlist_name(it)}.m3u" for it in tracked_items}
        return desired_music, desired_m3u

    def prune_device(self, profile, tracked_items: list[dict], should_cancel: Callable[[], bool] | None = None) -> dict:
        """Delete this app's music/.m3u files that `tracked_items` no longer wants; returns removed names."""
        # Stay conservative on a cancelled run, and on aft (no listing => can't know what's there).
        if should_cancel and should_cancel():
            return self._empty_prune_result()

        transcode = profile.transcode_to_mp3 and ffmpeg_available()
        desired_music, desired_m3u = self._desired_device_contents(tracked_items, transcode)

        if profile.is_mtp:
            return self._prune_mtp(profile, desired_music, desired_m3u)
        return self._prune_folder(profile, desired_music, desired_m3u)

    def _prune_folder(self, profile, desired_music: set[str], desired_m3u: set[str]) -> dict:
        """Prune a local folder destination."""
        base = Path(profile.path)
        removed_tracks = self._reconcile_local_dir(base / "music", desired_music, is_prunable_music_name)
        removed_playlists = self._reconcile_local_dir(base / "playlists", desired_m3u, is_m3u_name)
        return {"removed_tracks": removed_tracks, "removed_playlists": removed_playlists, "removed_count": len(removed_tracks) + len(removed_playlists)}

    def _reconcile_local_dir(self, directory: Path, desired: set[str], name_ok: Callable[[str], bool]) -> list[str]:
        """Delete files in `directory` that pass `name_ok` but aren't in `desired`."""
        removed: list[str] = []
        for name in self._list_local_pool(str(directory)):
            if name in desired or not name_ok(name):
                continue
            try:
                (directory / name).unlink()
                removed.append(name)
                logger.info(f"Prune: removed {directory / name}")
            except OSError as e:
                logger.error(f"Prune failed to remove {name}: {e}")
        return removed

    def _prune_mtp(self, profile, desired_music: set[str], desired_m3u: set[str]) -> dict:
        """Prune an MTP destination (gio backend only)."""
        result = self._empty_prune_result()
        device = self._get_mtp_device(profile.device_uri)
        if device is None or not self.mtp.can_list(device):
            return result

        music_dir_uri = self.mtp.build_music_uri(device, profile.music_path)
        playlists_dir_uri = self.mtp.build_playlists_dir_uri(device, profile.music_path)
        result["removed_tracks"] = self._reconcile_mtp_dir(device, music_dir_uri, desired_music, is_prunable_music_name)
        result["removed_playlists"] = self._reconcile_mtp_dir(device, playlists_dir_uri, desired_m3u, is_m3u_name)
        result["removed_count"] = len(result["removed_tracks"]) + len(result["removed_playlists"])
        return result

    def _reconcile_mtp_dir(self, device, dir_uri: str, desired: set[str], name_ok: Callable[[str], bool]) -> list[str]:
        """Delete remote files in `dir_uri` that pass `name_ok` but aren't in `desired`."""
        removed: list[str] = []
        for name in self.mtp.list_remote_dir(device, dir_uri):
            if name in desired or not name_ok(name):
                continue
            file_uri = dir_uri.rstrip("/") + "/" + name
            if self.mtp.delete_remote_file(device, file_uri):
                removed.append(name)
                logger.info(f"Prune: removed {file_uri}")
        return removed
