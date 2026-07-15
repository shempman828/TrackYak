"""
library_artwork_consistency.py

Detects albums whose embeddable (FLAC/MP3) tracks disagree on their
embedded art for a given role (front/rear/liner) - i.e. some tracks have
a different picture than others, or some have one and some don't.

This is a prerequisite check for the thumbnail cache, which reads a
single "representative" track per album/role and assumes every track in
the album agrees. That assumption should already hold today (the
artwork reconciler embedded the same directory-sourced image into every
track), but it isn't an enforced invariant, so this module checks it
explicitly rather than trusting it silently.

Read-only by design: it only ever reads files and reports findings via
the audit-log-style export_report(). Resolving a conflict (deciding
which track's art should win) is a separate, explicit step performed by
the caller (see run_artwork_consistency_check.py's --resolve flag).
"""

import hashlib
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.core.logger_config import logger
from src.metadata.metadata_artwork import ArtworkExtractor
from src.metadata.metadata_writer import MetadataWriter


class ArtworkConsistencyChecker:
    """Finds albums where tracks disagree on embedded art per role."""

    def __init__(self, controller, limit: Optional[int] = None):
        self.controller = controller
        self.limit = limit
        self.extractor = ArtworkExtractor()
        self.writer = MetadataWriter(controller)
        self.conflicts: List[Dict[str, Any]] = []

    def run(self) -> Dict[str, int]:
        summary = {
            "albums_scanned": 0,
            "albums_skipped_insufficient_tracks": 0,
            "conflicts_found": 0,
        }

        albums = self.controller.get.get_all_entities("Album")
        if not albums:
            logger.warning("ArtworkConsistencyChecker: no albums found in database.")
            return summary

        if self.limit is not None:
            albums = albums[: self.limit]

        total = len(albums)
        logger.info(
            f"ArtworkConsistencyChecker: starting consistency scan over {total} albums."
        )

        for i, album in enumerate(albums, start=1):
            if i % 500 == 0:
                logger.info(f"ArtworkConsistencyChecker: {i}/{total} albums processed …")

            summary["albums_scanned"] += 1

            tracks = [
                t
                for t in (getattr(album, "tracks", None) or [])
                if t.track_file_path
                and Path(t.track_file_path).suffix.lower()
                in ArtworkExtractor.SUPPORTED_EXTENSIONS
            ]
            if len(tracks) < 2:
                # Nothing to disagree with.
                summary["albums_skipped_insufficient_tracks"] += 1
                continue

            per_role_hashes: Dict[str, Dict[int, Optional[str]]] = {
                role: {} for role in ArtworkExtractor.PICTURE_TYPE_ROLES.values()
            }
            track_paths: Dict[int, str] = {}
            track_dimensions: Dict[int, Dict[str, Any]] = {}

            for track in tracks:
                file_path = track.track_file_path
                if not Path(file_path).is_file():
                    continue

                ext = Path(file_path).suffix.lower()
                try:
                    embedded = self.extractor.extract_artwork_by_role(file_path, ext)
                except Exception as e:
                    logger.error(
                        f"ArtworkConsistencyChecker: error reading {file_path}: {e}"
                    )
                    continue

                track_paths[track.track_id] = file_path
                track_dimensions[track.track_id] = {}

                for role in per_role_hashes:
                    picture = embedded.get(role)
                    if picture:
                        picture_hash = hashlib.sha256(picture["data"]).hexdigest()
                        per_role_hashes[role][track.track_id] = picture_hash
                        track_dimensions[track.track_id][role] = {
                            "width": picture.get("width"),
                            "height": picture.get("height"),
                        }
                    else:
                        per_role_hashes[role][track.track_id] = None

            for role, hashes_by_track in per_role_hashes.items():
                distinct_values = set(hashes_by_track.values())
                if len(distinct_values) <= 1:
                    continue  # everyone agrees (including all-None)

                summary["conflicts_found"] += 1
                self.conflicts.append(
                    {
                        "album_id": album.album_id,
                        "album_name": getattr(album, "album_name", None),
                        "role": role,
                        "tracks": [
                            {
                                "track_id": track_id,
                                "track_path": track_paths.get(track_id),
                                "hash": picture_hash,
                                "dimensions": track_dimensions.get(track_id, {}).get(
                                    role
                                ),
                            }
                            for track_id, picture_hash in hashes_by_track.items()
                        ],
                    }
                )

        logger.info(f"ArtworkConsistencyChecker: complete. Summary: {summary}")
        return summary

    def resolve_conflict(
        self, album, role: str, winning_track_id: int
    ) -> List[str]:
        """
        Make every embeddable track in `album` agree with `winning_track_id`
        for `role`, by reading the winning track's picture (or absence of
        one) and writing it to every other track. Returns the list of
        track file paths that failed to write, so the caller can report
        them - mirrors ArtworkReconciler's failure-reporting pattern.
        """
        tracks = [
            t
            for t in (getattr(album, "tracks", None) or [])
            if t.track_file_path
            and Path(t.track_file_path).suffix.lower()
            in ArtworkExtractor.SUPPORTED_EXTENSIONS
        ]
        winner = next((t for t in tracks if t.track_id == winning_track_id), None)
        if winner is None:
            raise ValueError(
                f"Track {winning_track_id} is not an embeddable track of album "
                f"{getattr(album, 'album_id', None)}"
            )

        winner_ext = Path(winner.track_file_path).suffix.lower()
        winning_picture = self.extractor.extract_artwork_by_role(
            winner.track_file_path, winner_ext
        ).get(role)
        image_bytes = winning_picture["data"] if winning_picture else None

        failed = []
        for track in tracks:
            if track.track_id == winning_track_id:
                continue
            try:
                success = self.writer.write_artwork_to_file(
                    track.track_file_path, role, image_bytes
                )
            except Exception as e:
                logger.error(
                    f"ArtworkConsistencyChecker: error resolving {role} on "
                    f"{track.track_file_path}: {e}"
                )
                success = False
            if not success:
                failed.append(track.track_file_path)
        return failed

    def export_report(self, path: Optional[str] = None) -> List[Dict[str, Any]]:
        """Return the conflict report, optionally also writing it as JSON."""
        if path:
            import json

            with open(path, "w") as f:
                json.dump(self.conflicts, f, indent=2, default=str)
        return self.conflicts
