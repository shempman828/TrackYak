"""
library_artwork_consistency.py

Detects albums whose embeddable (FLAC/MP3) tracks disagree on their
embedded art for a given role (front/rear/liner) - i.e. some tracks have
a different picture than others, or some have one and some don't.

The thumbnail cache (src/image/artwork_cache.py) reads a single
"representative" track per album/role and assumes every track in the
album agrees. Nothing enforces that invariant; this module is the
on-demand check for it, driven by the Tools -> "Artwork Conflicts…"
dialog (src/library/artwork_consistency_dialog.py). Resolving a conflict
(deciding which variant wins and re-embedding it into every track) is the
dialog's job, via the same CoverEmbedWorker the album editor uses.

Read-only by design: `run()` only ever reads files and fills
`self.conflicts`.
"""

from collections.abc import Callable
import hashlib
from pathlib import Path
from typing import Any

from src.core.logger_config import logger
from src.metadata.metadata_artwork import ArtworkExtractor


class ArtworkConsistencyChecker:
    """Finds albums where tracks disagree on embedded art per role."""

    def __init__(self, controller, limit: int | None = None):
        self.controller = controller
        self.limit = limit
        self.extractor = ArtworkExtractor()
        self.conflicts: list[dict[str, Any]] = []

    def run(
        self,
        progress_callback: Callable[[int, int], None] | None = None,
        is_cancelled: Callable[[], bool] | None = None,
    ) -> dict[str, int]:
        """Scan every album, filling `self.conflicts`. Returns a summary dict.

        `progress_callback(scanned, total)` is called once per album before
        it is processed. `is_cancelled()` is polled once per album; when it
        returns True the scan stops and `run()` returns with whatever has
        been found so far.
        """
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
        logger.info(f"ArtworkConsistencyChecker: starting consistency scan over {total} albums.")

        for i, album in enumerate(albums, start=1):
            if is_cancelled is not None and is_cancelled():
                logger.info(f"ArtworkConsistencyChecker: cancelled after {i - 1}/{total} albums.")
                break
            if progress_callback is not None:
                progress_callback(i, total)

            summary["albums_scanned"] += 1

            tracks = [
                t
                for t in (getattr(album, "tracks", None) or [])
                if t.track_file_path
                and Path(t.track_file_path).suffix.lower() in ArtworkExtractor.SUPPORTED_EXTENSIONS
            ]
            if len(tracks) < 2:
                # Nothing to disagree with.
                summary["albums_skipped_insufficient_tracks"] += 1
                continue

            per_role_hashes: dict[str, dict[int, str | None]] = {
                role: {} for role in ArtworkExtractor.PICTURE_TYPE_ROLES.values()
            }
            track_paths: dict[int, str] = {}
            track_dimensions: dict[int, dict[str, Any]] = {}

            for track in tracks:
                file_path = track.track_file_path
                if not Path(file_path).is_file():
                    continue

                ext = Path(file_path).suffix.lower()
                try:
                    embedded = self.extractor.extract_artwork_by_role(file_path, ext)
                except Exception:
                    # Intentional broad boundary catch: this runs once per
                    # track inside a library-wide consistency scan (see class
                    # docstring) — a single corrupt/unreadable file must not
                    # abort the scan for the rest of the album or catalog.
                    logger.exception(f"ArtworkConsistencyChecker: error reading {file_path}")
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
                                "dimensions": track_dimensions.get(track_id, {}).get(role),
                            }
                            for track_id, picture_hash in hashes_by_track.items()
                        ],
                    }
                )

        logger.info(f"ArtworkConsistencyChecker: complete. Summary: {summary}")
        return summary
