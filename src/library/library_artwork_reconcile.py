"""
library_artwork_reconcile.py

One-time reconciliation pass, run once before switching the app over to
treating embedded FLAC art as the source of truth.

The images/album_art directory (referenced by Album.front_cover_path /
rear_cover_path / album_liner_path) is authoritative for this pass — every
FLAC track gets brought into agreement with it, not the other way around:

    - Directory has an image for a role, the track's embedded picture is
      missing or different -> embed the directory's image into the track.
    - Directory has no image for a role, the track has one embedded
      -> strip it from the track.
    - Otherwise -> no-op.

Defaults to dry_run=True. Nothing is written to any file until you
explicitly construct with dry_run=False. Always review a dry run's summary
and audit log before running for real.
"""

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.core.logger_config import logger
from src.metadata.metadata_artwork import ArtworkExtractor
from src.metadata.metadata_writer import MetadataWriter


class ArtworkReconciler:
    """Brings embedded FLAC art into agreement with images/album_art."""

    ROLE_COLUMNS = {
        "front": "front_cover_path",
        "rear": "rear_cover_path",
        "liner": "album_liner_path",
    }

    def __init__(self, controller, dry_run: bool = True, limit: Optional[int] = None):
        self.controller = controller
        self.dry_run = dry_run
        self.limit = limit
        self.writer = MetadataWriter(controller)
        self.extractor = ArtworkExtractor()
        self.audit_log: List[Dict[str, Any]] = []

    # ------------------------------------------------------------------ #
    #  Public entry point                                                  #
    # ------------------------------------------------------------------ #

    def run(self) -> Dict[str, int]:
        summary = {
            "albums_scanned": 0,
            "tracks_scanned": 0,
            "tracks_skipped_missing_file": 0,
            "tracks_skipped_not_flac": 0,
            "embedded_front": 0,
            "embedded_rear": 0,
            "embedded_liner": 0,
            "stripped_front": 0,
            "stripped_rear": 0,
            "stripped_liner": 0,
            "already_in_sync": 0,
            "errors": 0,
        }

        all_tracks = self.controller.get.get_all_entities("Track")
        if not all_tracks:
            logger.warning("ArtworkReconciler: no tracks found in database.")
            return summary

        if self.limit is not None:
            all_tracks = all_tracks[: self.limit]

        total = len(all_tracks)
        logger.info(
            f"ArtworkReconciler: starting reconciliation pass over {total} "
            f"tracks (dry_run={self.dry_run}, limit={self.limit})."
        )

        album_art_cache: Dict[int, Dict[str, Dict[str, Optional[str]]]] = {}
        visited_album_ids: set = set()

        for i, track in enumerate(all_tracks, start=1):
            if i % 500 == 0:
                logger.info(f"ArtworkReconciler: {i}/{total} tracks processed …")

            summary["tracks_scanned"] += 1

            file_path = track.track_file_path
            if not file_path or not Path(file_path).is_file():
                summary["tracks_skipped_missing_file"] += 1
                continue

            if Path(file_path).suffix.lower() != ".flac":
                summary["tracks_skipped_not_flac"] += 1
                continue

            album_id = getattr(track, "album_id", None)
            if album_id is None:
                continue

            if album_id not in album_art_cache:
                album = self.controller.get.get_entity_object(
                    "Album", album_id=album_id
                )
                album_art_cache[album_id] = self._hash_directory_art(album)
            if album_id not in visited_album_ids:
                summary["albums_scanned"] += 1
                visited_album_ids.add(album_id)

            directory_art = album_art_cache[album_id]

            try:
                embedded = self.extractor.extract_artwork_by_role(file_path, ".flac")
            except Exception as e:
                logger.error(f"ArtworkReconciler: error reading {file_path}: {e}")
                summary["errors"] += 1
                continue

            for role in self.ROLE_COLUMNS:
                dir_hash = directory_art[role]["hash"]
                embedded_picture = embedded.get(role)
                embedded_hash = (
                    hashlib.sha256(embedded_picture["data"]).hexdigest()
                    if embedded_picture
                    else None
                )

                action = self._decide_action(dir_hash, embedded_hash)

                if action == "noop":
                    summary["already_in_sync"] += 1
                    continue

                self._log_action(album_id, track, role, action, dir_hash, embedded_hash)

                if self.dry_run:
                    summary[f"{action}_{role}"] += 1
                    continue

                success = self._apply_action(file_path, role, action, directory_art)
                if success:
                    summary[f"{action}_{role}"] += 1
                else:
                    summary["errors"] += 1

        logger.info(f"ArtworkReconciler: complete. Summary: {summary}")
        return summary

    def export_audit_log(self, path: Optional[str] = None) -> List[Dict[str, Any]]:
        """Return the audit log, optionally also writing it out as JSON."""
        if path:
            with open(path, "w") as f:
                json.dump(self.audit_log, f, indent=2, default=str)
        return self.audit_log

    # ------------------------------------------------------------------ #
    #  Internal helpers                                                    #
    # ------------------------------------------------------------------ #

    def _hash_directory_art(self, album) -> Dict[str, Dict[str, Optional[str]]]:
        """For each role, the album's stored loose-file path and its hash
        (both None if the column is empty or the file no longer exists)."""
        result: Dict[str, Dict[str, Optional[str]]] = {}
        for role, column in self.ROLE_COLUMNS.items():
            path = getattr(album, column, None) if album else None
            if path and Path(path).is_file():
                try:
                    with open(path, "rb") as f:
                        file_hash = hashlib.sha256(f.read()).hexdigest()
                    result[role] = {"path": path, "hash": file_hash}
                    continue
                except Exception as e:
                    logger.error(f"ArtworkReconciler: error reading {path}: {e}")
            result[role] = {"path": None, "hash": None}
        return result

    def _decide_action(
        self, dir_hash: Optional[str], embedded_hash: Optional[str]
    ) -> str:
        if dir_hash is not None:
            return "noop" if embedded_hash == dir_hash else "embedded"
        return "stripped" if embedded_hash is not None else "noop"

    def _apply_action(
        self,
        file_path: str,
        role: str,
        action: str,
        directory_art: Dict[str, Dict[str, Optional[str]]],
    ) -> bool:
        if action == "embedded":
            dir_path = directory_art[role]["path"]
            try:
                with open(dir_path, "rb") as f:
                    image_bytes = f.read()
            except Exception as e:
                logger.error(f"ArtworkReconciler: error reading {dir_path}: {e}")
                return False
            return self.writer.write_artwork_to_file(file_path, role, image_bytes)

        if action == "stripped":
            return self.writer.write_artwork_to_file(file_path, role, None)

        return False

    def _log_action(
        self,
        album_id: int,
        track,
        role: str,
        action: str,
        dir_hash: Optional[str],
        embedded_hash: Optional[str],
    ) -> None:
        label = f"would_{action}" if self.dry_run else action
        self.audit_log.append(
            {
                "album_id": album_id,
                "track_id": getattr(track, "track_id", None),
                "track_path": track.track_file_path,
                "role": role,
                "action": label,
                "dir_hash": dir_hash,
                "embedded_hash_before": embedded_hash,
            }
        )
