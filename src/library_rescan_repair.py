"""
library_rescan_repair.py

One-time repair pass over an existing library.
For every track already in the database, re-reads its file metadata and
backfills only the fields that are currently empty:

    - Album  : release_month, release_day
    - Album  : album-artist relationships (AlbumRoleAssociation)
    - Album  : front_cover_path / artwork file

Nothing is overwritten if a value already exists.
Safe to run multiple times — it will simply find nothing left to fix.
"""

from pathlib import Path
from typing import Any, Dict, List, Optional

import src.metdata_controller
from src.asset_paths import ALBUM_ART_DIR
from src.library_import_album import AlbumImporter
from src.logger_config import logger


class LibraryRepair:
    """
    Scans every track in the database, re-reads its file metadata, and
    backfills empty album fields without touching data that is already present.
    """

    def __init__(self, controller):
        self.controller = controller
        self._album_importer = AlbumImporter(controller)

    # ------------------------------------------------------------------ #
    #  Public entry point                                                  #
    # ------------------------------------------------------------------ #

    def run(self) -> Dict[str, int]:
        """
        Run the full repair pass.

        Returns a summary dict, e.g.:
            {
                "tracks_scanned": 50000,
                "tracks_skipped": 120,      # file missing / unreadable
                "albums_patched_date": 812,
                "albums_patched_artists": 340,
                "albums_patched_artwork": 275,
            }
        """
        summary = {
            "tracks_scanned": 0,
            "tracks_skipped": 0,
            "albums_patched_date": 0,
            "albums_patched_artists": 0,
            "albums_patched_artwork": 0,
        }

        # Keep a per-run cache so we only re-examine each album once
        visited_album_ids: set = set()

        all_tracks = self.controller.get.get_all_entities("Track")
        if not all_tracks:
            logger.warning("LibraryRepair: no tracks found in database.")
            return summary

        total = len(all_tracks)
        logger.info(f"LibraryRepair: starting repair pass over {total} tracks.")

        for i, track in enumerate(all_tracks, start=1):
            if i % 500 == 0:
                logger.info(f"LibraryRepair: {i}/{total} tracks processed …")

            summary["tracks_scanned"] += 1

            # ── 1. Check the file still exists ────────────────────────────
            file_path = track.track_file_path
            if not file_path or not Path(file_path).is_file():
                logger.debug(f"LibraryRepair: skipping missing file: {file_path}")
                summary["tracks_skipped"] += 1
                continue

            # ── 2. Skip if this track's album was already handled this run ─
            album_id = getattr(track, "album_id", None)
            if album_id is None or album_id in visited_album_ids:
                continue

            # ── 3. Fetch the album record ──────────────────────────────────
            album = self.controller.get.get_entity_object("Album", album_id=album_id)
            if not album:
                logger.debug(f"LibraryRepair: no album record for album_id={album_id}")
                continue

            # ── 4. Decide whether this album needs anything ────────────────
            needs_date = not album.release_month or not album.release_day
            needs_artists = not self._album_has_artists(album_id)
            needs_artwork = not album.front_cover_path

            if not any([needs_date, needs_artists, needs_artwork]):
                visited_album_ids.add(album_id)
                continue  # Album is already complete — nothing to do

            # ── 5. Extract metadata from the audio file ────────────────────
            metadata = self._extract_metadata(file_path)
            if not metadata:
                summary["tracks_skipped"] += 1
                visited_album_ids.add(album_id)
                continue

            # ── 6. Patch each field that is empty ─────────────────────────
            if needs_date:
                patched = self._patch_release_date(album, metadata)
                if patched:
                    summary["albums_patched_date"] += 1

            if needs_artists:
                patched = self._patch_album_artists(album, metadata)
                if patched:
                    summary["albums_patched_artists"] += 1

            if needs_artwork:
                patched = self._patch_artwork(album, metadata)
                if patched:
                    summary["albums_patched_artwork"] += 1

            visited_album_ids.add(album_id)

        logger.info(f"LibraryRepair: complete. Summary: {summary}")
        return summary

    # ------------------------------------------------------------------ #
    #  Patch helpers                                                       #
    # ------------------------------------------------------------------ #

    def _patch_release_date(self, album, metadata: Dict[str, Any]) -> bool:
        """
        Write release_month and/or release_day if they are currently empty.
        Returns True if at least one field was updated.
        """
        updates = {}

        if not album.release_month:
            month = metadata.get("album_release_month") or metadata.get("release_month")
            if month:
                updates["release_month"] = month

        if not album.release_day:
            day = metadata.get("album_release_day") or metadata.get("release_day")
            if day:
                updates["release_day"] = day

        if not updates:
            return False

        self.controller.update.update_entity("Album", album.album_id, **updates)
        logger.debug(
            f"LibraryRepair: patched date fields {list(updates.keys())} "
            f"for album '{album.album_name}' (ID: {album.album_id})"
        )
        return True

    def _patch_album_artists(self, album, metadata: Dict[str, Any]) -> bool:
        """
        Create AlbumRoleAssociation rows for any album artists found in
        metadata that do not already exist in the database.
        Returns True if at least one new relationship was created.
        """
        artist_names = self._album_importer._extract_album_artists_list(metadata)
        if not artist_names:
            logger.debug(
                f"LibraryRepair: no album artists in metadata for "
                f"'{album.album_name}' — cannot patch."
            )
            return False

        processed_names: set = set()
        new_artist_ids: List[int] = []

        for name in artist_names:
            artist_id = self._album_importer._process_artist_name(name, processed_names)
            if artist_id:
                new_artist_ids.append(artist_id)

        if not new_artist_ids:
            return False

        # _create_album_artist_relationships already skips existing rows
        self._album_importer._create_album_artist_relationships(
            album.album_id, new_artist_ids
        )
        logger.debug(
            f"LibraryRepair: patched album artists for '{album.album_name}' "
            f"(ID: {album.album_id}): {artist_names}"
        )
        return True

    def _patch_artwork(self, album, metadata: Dict[str, Any]) -> bool:
        """
        Extract artwork from the audio file and save it if the album record
        has no front_cover_path.
        Returns True if artwork was successfully saved.
        """
        if "album_art_data" not in metadata:
            logger.debug(
                f"LibraryRepair: no artwork in metadata for '{album.album_name}'."
            )
            return False

        album_artists = self._album_importer._extract_album_artists_list(metadata)

        # Resolve artist name for the folder path
        if album_artists:
            first = album_artists[0]
            artist_name = first if isinstance(first, str) else first.artist_name
        else:
            # Fall back to the database association if metadata has no artist
            artist_name = self._get_artist_name_from_db(album.album_id)

        album_art_data = metadata["album_art_data"]

        try:
            safe_artist = self._album_importer._sanitize_filename(artist_name)
            safe_album = self._album_importer._sanitize_filename(album.album_name)

            art_dir = ALBUM_ART_DIR / safe_artist / safe_album
            art_dir.mkdir(parents=True, exist_ok=True)

            image_format = album_art_data.get("format", "jpg").lower()
            if image_format == "jpeg":
                image_format = "jpg"

            front_cover_path = art_dir / f"frontcover.{image_format}"
            with open(front_cover_path, "wb") as f:
                f.write(album_art_data["data"])

            self.controller.update.update_entity(
                "Album", album.album_id, front_cover_path=str(front_cover_path)
            )

            logger.debug(
                f"LibraryRepair: saved artwork for '{album.album_name}' "
                f"-> {front_cover_path}"
            )
            return True

        except Exception as e:
            logger.error(
                f"LibraryRepair: failed to save artwork for '{album.album_name}': {e}"
            )
            return False

    # ------------------------------------------------------------------ #
    #  Internal utilities                                                  #
    # ------------------------------------------------------------------ #

    def _extract_metadata(self, file_path: str) -> Optional[Dict[str, Any]]:
        """Run the standard metadata extractor on a file path."""
        try:
            extractor = src.metdata_controller.ExtractMetadata()
            metadata = extractor.extract_metadata(file_path)
            if not metadata:
                logger.warning(
                    f"LibraryRepair: metadata extraction returned nothing for {file_path}"
                )
            return metadata
        except Exception as e:
            logger.error(
                f"LibraryRepair: error extracting metadata from {file_path}: {e}"
            )
            return None

    def _album_has_artists(self, album_id: int) -> bool:
        """Return True if at least one AlbumRoleAssociation exists for this album."""
        associations = self.controller.get.get_all_entities(
            "AlbumRoleAssociation", album_id=album_id
        )
        return bool(associations)

    def _get_artist_name_from_db(self, album_id: int) -> str:
        """
        Look up the first artist associated with this album in the database.
        Used as a fallback folder name when metadata carries no artist.
        """
        try:
            associations = self.controller.get.get_all_entities(
                "AlbumRoleAssociation", album_id=album_id
            )
            if associations:
                artist = self.controller.get.get_entity_object(
                    "Artist", artist_id=associations[0].artist_id
                )
                if artist:
                    return artist.artist_name
        except Exception as e:
            logger.debug(f"LibraryRepair: could not resolve artist from DB: {e}")

        return "Unknown Artist"
