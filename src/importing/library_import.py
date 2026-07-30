"""
library_import.py

This code handles importing audio files into a music library database and parsing very robust metadata.
"""

from enum import Enum
from pathlib import Path
from typing import Any, ClassVar

import psutil
from PySide6.QtCore import Signal
from sqlalchemy.exc import SQLAlchemyError

from src.common.cancellable_worker import CancellableWorker
from src.core.config_setup import app_config
from src.core.logger_config import logger
from src.importing.artist_field_extraction import (
    ALBUM_ARTIST_FIELDS,
    TRACK_ARTIST_FIELDS,
    extract_artists_from_metadata,
)
from src.importing.library_import_album import AlbumImporter
from src.metadata.metadata_extraction import MetadataExtractor


class ImportResult(Enum):
    """Outcome of TrackImporter.add_track — a self-documenting replacement
    for an easily-misread True/False/None tri-state return."""

    IMPORTED = "imported"
    SKIPPED = "skipped"
    FAILED = "failed"


class TrackImporter:
    """
    Handles importing tracks into the database with comprehensive error handling.

    Responsibilities:
    - Recursively scan paths for supported audio files
    - Receive metadata and manage database relationships
    - Ensure robust error handling and continue-on-error behavior
    """

    SUPPORTED_EXTENSIONS: ClassVar = {
        ".mp3",
        ".flac",
        ".wav",
        ".m4a",
        ".aac",
        ".ogg",
        ".opus",
    }

    def __init__(self, controller):
        self.controller = controller

    def add_track(self, file_path: str) -> ImportResult:
        """
        Main method to add a track with full metadata extraction and relationship mapping.

        All database writes for this track (album, disc, artists, the track
        itself, and every relationship) are one transaction: they're
        committed together at the end, or rolled back together if any step
        fails, instead of each individual insert committing on its own as
        soon as it happens.

        Args:
            file_path: Path to the audio file

        Returns:
            ImportResult: IMPORTED, SKIPPED (already in the library), or FAILED.
        """
        try:
            # Normalize once so the dedup check and the stored
            # track_file_path always agree, regardless of how the caller
            # spelled the path (relative, symlinked, trailing slash, ...).
            file_path = str(Path(file_path).resolve())

            # Check first — no point doing any work if this file is already in the library
            if self._track_exists(file_path):
                return ImportResult.SKIPPED

            logger.info(f"Processing track: {file_path}")

            # Extract metadata using metadata_extraction
            metadata_extractor = MetadataExtractor()
            metadata = metadata_extractor.extract_metadata(file_path)
            if not metadata:
                logger.error(f"Failed to extract metadata for: {file_path}")
                return ImportResult.FAILED
            logger.debug(f"Metadata keys: {list(metadata.keys())}")
            self._log_metadata_debug(metadata, file_path)
            self._debug_metadata_types(metadata)

            session = self.controller.add.session
            try:
                # Process entities in correct order. commit=False throughout:
                # this whole block is one transaction, committed once at the
                # end (or rolled back together on any failure) rather than
                # each insert committing as soon as it happens.
                album_extractor = AlbumImporter(self.controller)
                album = album_extractor._get_or_create_album(metadata)
                disc = album_extractor._get_or_create_disc(album.album_id, metadata)
                artists = self._process_artists(metadata, album)
                track = self._create_track(metadata, album, file_path, disc)

                if not track:
                    raise RuntimeError(
                        f"Failed to create track entity for: {file_path}"
                    )

                # Create relationships
                self._create_track_artist_relationships(track, artists, metadata)
                self._create_track_genre_relationships(track, metadata)
                album_extractor._create_album_artist_relationships(
                    album.album_id,
                    [a.artist_id for a in artists.get("Album Artist", [])],
                )

                self._process_playlist_tags(track, metadata)

                session.commit()
            except (SQLAlchemyError, RuntimeError):
                session.rollback()
                raise

            logger.info(f"Successfully imported track: {track.track_name}")
            return ImportResult.IMPORTED

        except (OSError, SQLAlchemyError, RuntimeError) as e:
            logger.error(f"Error importing track {file_path}: {e!s}", exc_info=True)
            return ImportResult.FAILED

    def _process_artists(self, metadata: dict[str, Any], album) -> dict[str, list]:
        """Process all artist types and return organized dictionary."""
        # Use proper display names as keys from the start
        artists_dict = {
            "Primary Artist": [],
            "Album Artist": [],
            "Composer": [],
            "Conductor": [],
            "Producer": [],
            "Engineer": [],
            "Remixer": [],
            "Lyricist": [],
            "Arranger": [],
            "Mixer": [],
            "Writer": [],
            "Vocalist": [],
            "Narrator": [],
            "Orchestra": [],
            "Choir": [],
            "DJ": [],
            "Mastering Engineer": [],
            "Other": [],
        }

        # Process primary artists
        primary_artists = extract_artists_from_metadata(metadata, TRACK_ARTIST_FIELDS)
        artists_dict["Primary Artist"] = self._create_artists_list(primary_artists)

        # Process album artists (same field priority AlbumImporter uses when
        # resolving the album itself, so the two stay consistent)
        album_artists = extract_artists_from_metadata(metadata, ALBUM_ARTIST_FIELDS)
        artists_dict["Album Artist"] = self._create_artists_list(album_artists)

        # Map metadata keys to display role names
        role_mappings = {
            "artist_composer": "Composer",
            "artist_conductor": "Conductor",
            "artist_producer": "Producer",
            "artist_engineer": "Engineer",
            "artist_remixer": "Remixer",
            "artist_lyricist": "Lyricist",
            "artist_arranger": "Arranger",
            "artist_mixer": "Mixer",
            "artist_writer": "Writer",
            "artist_vocalist": "Vocalist",
            "artist_narrator": "Narrator",
            "artist_orchestra": "Orchestra",
            "artist_choir": "Choir",
            "artist_dj": "DJ",
            "artist_mastering_engineer": "Mastering Engineer",
        }

        for metadata_key, role_name in role_mappings.items():
            role_artists = extract_artists_from_metadata(metadata, [metadata_key])
            artists_dict[role_name] = self._create_artists_list(role_artists)

        # Log the results for debugging
        self._log_artist_processing_results(artists_dict)

        return artists_dict

    def _create_artists_list(self, artist_names: list[str]) -> list[Any]:
        """
        Create artist objects from a list of artist names.

        Args:
            artist_names: List of artist names

        Returns:
            List of artist objects
        """
        artists = []
        for artist_name in artist_names:
            artist = self._get_or_create_artist(artist_name)
            if artist:
                artists.append(artist)

        return artists

    def _log_artist_processing_results(self, artists_dict: dict[str, list]):
        """Log the results of artist processing for debugging."""
        total_artists = sum(len(artists) for artists in artists_dict.values())
        if total_artists == 0:
            logger.warning("No artists were processed from metadata")
            return

        logger.debug("Artist processing results:")
        for role, artists in artists_dict.items():
            if artists:
                artist_names = [artist.artist_name for artist in artists]
                logger.debug(f"  {role}: {artist_names}")

    def _get_or_create_artist(self, artist_name: str):
        """Get existing artist or create new one."""
        try:
            if not artist_name or artist_name.strip() == "":
                return None

            # Look for existing artist
            existing_artist = self.controller.get.get_entity_object(
                "Artist", artist_name=artist_name
            )
            if existing_artist:
                return existing_artist

            # Create new artist
            artist_data = {
                "artist_name": artist_name,
                "isgroup": 0,  # Default to individual artist
            }

            new_artist = self.controller.add.add_entity(
                "Artist", commit=False, **artist_data
            )
            logger.debug(f"Created new artist: {artist_name}")
            return new_artist

        except SQLAlchemyError as e:
            logger.error(f"Error creating artist {artist_name}: {e}")
            return None

    def _create_track(self, metadata: dict[str, Any], album, file_path: str, disc=None):
        """Create track entity with comprehensive metadata."""
        try:
            # Extract and clean metadata values
            def clean_value(value):
                if value is None:
                    return None
                if isinstance(value, list):
                    # Convert list to string with semicolon delimiter
                    return "; ".join(str(item) for item in value if item)
                if isinstance(value, dict):
                    # Convert dict to string representation or skip
                    return str(value)
                return value

            # FIX: Use correct field names from metadata extraction and add album relationship
            track_data = {
                "track_name": clean_value(
                    metadata.get("track_name", Path(file_path).stem)
                ),
                "track_file_path": file_path,
                "duration": clean_value(metadata.get("duration")),
                "file_size": clean_value(metadata.get("file_size")),
                "file_extension": clean_value(metadata.get("file_extension")),
                "track_number": clean_value(metadata.get("track_number")),
                "bit_rate": clean_value(metadata.get("bit_rate")),
                "sample_rate": clean_value(metadata.get("sample_rate")),
                "bit_depth": clean_value(metadata.get("bit_depth")),
                "channels": clean_value(metadata.get("channels")),
                "bpm": clean_value(metadata.get("bpm")),
                "key": clean_value(metadata.get("key")),
                "isrc": clean_value(metadata.get("isrc")),
                "comment": clean_value(metadata.get("comment")),
                "lyrics": clean_value(metadata.get("lyrics")),
                "user_rating": clean_value(metadata.get("user_rating", 0)),
                "play_count": clean_value(metadata.get("play_count")),
                "is_explicit": clean_value(metadata.get("is_explicit")),
                "is_instrumental": clean_value(metadata.get("is_instrumental")),
                "is_classical": clean_value(metadata.get("is_classical")),
                "date_added": clean_value(metadata.get("date_added")),
                "track_copyright": clean_value(metadata.get("track_copyright")),
                "track_description": clean_value(metadata.get("track_description")),
                "work_name": clean_value(metadata.get("work_name")),
                "movement_name": clean_value(metadata.get("movement_name")),
                "movement_number": clean_value(metadata.get("movement_number")),
                "classical_catalog_prefix": clean_value(
                    metadata.get("classical_catalog_prefix")
                ),
                "classical_catalog_number": clean_value(
                    metadata.get("classical_catalog_number")
                ),
                "MBID": clean_value(metadata.get("MBID")),
                "track_gain": clean_value(metadata.get("track_gain")),
                "classical_tempo": clean_value(metadata.get("classical_tempo")),
                "mode": clean_value(metadata.get("mode")),
                "track_quality": clean_value(metadata.get("track_quality")),
                "side": clean_value(metadata.get("side")),
                "track_barcode": clean_value(metadata.get("track_barcode")),
                "danceability": clean_value(metadata.get("danceability")),
                "valence": clean_value(metadata.get("valence")),
                "energy": clean_value(metadata.get("energy")),
                "acousticness": clean_value(metadata.get("acousticness")),
                "key_confidence": clean_value(metadata.get("key_confidence")),
                "tempo_confidence": clean_value(metadata.get("tempo_confidence")),
                "album_id": album.album_id if album else None,
                "disc_id": disc.disc_id if disc else None,
            }

            # Remove None values and ensure all values are database-compatible
            track_data = {k: v for k, v in track_data.items() if v is not None}

            # Additional validation for specific problematic fields
            problematic_fields = ["comment", "lyrics", "track_description"]
            for field in problematic_fields:
                if field in track_data and isinstance(track_data[field], (list, dict)):
                    track_data[field] = str(track_data[field])

            track = self.controller.add.add_entity("Track", commit=False, **track_data)

            if not track:
                logger.error(f"Failed to create track entity for: {file_path}")
                return None

            return track

        except SQLAlchemyError as e:
            logger.error(f"Error creating track: {e}", exc_info=True)
            return None

    def _create_track_artist_relationships(
        self, track, artists_dict: dict, metadata: dict[str, Any]
    ):
        """Create TrackArtistRole relationships for all artist types.

        Each (role, artist) pair is isolated: one bad relationship is
        logged and skipped rather than aborting every relationship still
        left in the loop (the previous behavior, since a single wrapping
        try/except meant one failure silently dropped all credits after
        it).
        """
        role_cache = {}  # Cache role lookups

        for (
            role_name,
            artists,
        ) in artists_dict.items():  # role_name is already correct!
            if role_name == "Album Artist":  # Album artists handled separately
                continue

            for artist in artists:
                if not artist:
                    continue

                try:
                    if role_name not in role_cache:
                        role = self.controller.get.get_entity_object(
                            "Role", role_name=role_name
                        )
                        if not role:
                            role = self.controller.add.add_entity(
                                "Role",
                                commit=False,
                                role_name=role_name,
                                role_type="credits",
                            )
                        role_cache[role_name] = role
                    else:
                        role = role_cache[role_name]

                    # Create relationship
                    relationship_data = {
                        "track_id": track.track_id,
                        "artist_id": artist.artist_id,
                        "role_id": role.role_id,
                    }

                    self.controller.add.add_entity(
                        "TrackArtistRole", commit=False, **relationship_data
                    )
                    logger.debug(
                        f"Created {role_name} relationship: {artist.artist_name} -> {track.track_name}"
                    )
                except SQLAlchemyError as e:
                    logger.error(
                        f"Error creating {role_name} relationship for "
                        f"{artist.artist_name}: {e}"
                    )

    def _process_playlist_tags(self, track, metadata: dict):
        """Read PLAYLIST tags from metadata and add the track to those playlists.

        This is called after a track has been successfully created in the
        database. It looks for playlist names stored in the file's tags
        (written there by the metadata writer) and reconstructs the
        playlist membership.

        For FLAC/OGG files: reads the PLAYLIST Vorbis comment (may be a list).
        For MP3 files: reads the TXXX:PLAYLIST tag and splits on " ; ".

        If a playlist already exists (matched by exact name), the track is
        added to it. If it doesn't exist, a new playlist is created first.
        Tracks are always added at the end of the playlist (highest position + 1).

        Args:
            track:    The newly created Track ORM object.
            metadata: The full metadata dict from MetadataExtractor.
        """
        try:
            # ── 1. Collect playlist name(s) from metadata ──────────────
            playlist_names = []

            # Vorbis: stored as PLAYLIST (may be a list if multiple playlists)
            vorbis_playlists = metadata.get("PLAYLIST") or metadata.get("playlist")
            if vorbis_playlists:
                if isinstance(vorbis_playlists, list):
                    playlist_names.extend(vorbis_playlists)
                else:
                    playlist_names.append(str(vorbis_playlists))

            # ID3: stored as TXXX:PLAYLIST, multiple values joined by " ; "
            id3_playlists = metadata.get("TXXX:PLAYLIST") or metadata.get(
                "txxx:playlist"
            )
            if id3_playlists:
                # id3_playlists may itself be a list (if somehow multiple TXXX:PLAYLIST frames exist)
                if isinstance(id3_playlists, list):
                    for entry in id3_playlists:
                        # Each entry may contain " ; "-separated names
                        playlist_names.extend([
                            p.strip() for p in str(entry).split(" ; ") if p.strip()
                        ])
                else:
                    playlist_names.extend([
                        p.strip() for p in str(id3_playlists).split(" ; ") if p.strip()
                    ])

            # Remove duplicates and blanks
            playlist_names = list(
                dict.fromkeys(name for name in playlist_names if name)
            )

            if not playlist_names:
                return  # No playlist tags found — nothing to do

            logger.info(
                f"Track '{track.track_name}' has playlist tags: {playlist_names}"
            )

            # ── 2. For each playlist name, find-or-create the playlist ──
            for playlist_name in playlist_names:
                self._add_track_to_playlist_by_name(track, playlist_name)

        except SQLAlchemyError as e:
            logger.error(
                f"Error processing playlist tags for track {track.track_id}: {e}",
                exc_info=True,
            )

    def _add_track_to_playlist_by_name(self, track, playlist_name: str):
        """Find or create a playlist by name, then add the track to it.

        If the playlist already exists, the track is appended at the end.
        If the track is already in that playlist (e.g. importing the same file
        twice), the duplicate is silently skipped.

        Args:
            track:         The Track ORM object to add.
            playlist_name: The exact name of the playlist.
        """
        try:
            # ── Find or create the playlist ────────────────────────────
            playlist = self.controller.get.get_entity_object(
                "Playlist", playlist_name=playlist_name
            )

            if not playlist:
                logger.info(f"Creating new playlist from tag: '{playlist_name}'")
                playlist = self.controller.add.add_entity(
                    "Playlist",
                    commit=False,
                    playlist_name=playlist_name,
                    is_smart=0,
                )
                if not playlist:
                    logger.error(f"Failed to create playlist '{playlist_name}'")
                    return

            # ── Check the track isn't already in this playlist ─────────
            existing = self.controller.get.get_entity_object(
                "PlaylistTracks",
                playlist_id=playlist.playlist_id,
                track_id=track.track_id,
            )
            if existing:
                logger.debug(
                    f"Track '{track.track_name}' is already in playlist '{playlist_name}' — skipping"
                )
                return

            # ── Determine the next position ────────────────────────────
            # Get all current tracks in the playlist to find the highest position
            existing_tracks = self.controller.get.get_all_entities(
                "PlaylistTracks", playlist_id=playlist.playlist_id
            )
            next_position = max((pt.position for pt in existing_tracks), default=0) + 1

            # ── Add the track ──────────────────────────────────────────
            from datetime import datetime, timezone

            self.controller.add.add_entity(
                "PlaylistTracks",
                commit=False,
                playlist_id=playlist.playlist_id,
                track_id=track.track_id,
                position=next_position,
                date_added=datetime.now(timezone.utc),
            )

            logger.info(
                f"Added '{track.track_name}' to playlist '{playlist_name}' at position {next_position}"
            )

        except SQLAlchemyError as e:
            logger.error(
                f"Error adding track to playlist '{playlist_name}': {e}",
                exc_info=True,
            )

    def _create_track_genre_relationships(self, track, metadata: dict[str, Any]):
        """Create TrackGenre relationships with better multi-value support.

        Each genre is isolated: one bad genre is logged and skipped
        rather than aborting every genre still left in the loop.
        """
        genres = metadata.get("genre_name", [])

        # Handle multiple genres in various formats
        if isinstance(genres, str):
            # Split by common delimiters
            genres = [genre.strip() for genre in genres.split(";") if genre.strip()]
        elif genres is None:
            genres = []
        else:
            # Ensure no empty values
            genres = [genre for genre in genres if genre and genre.strip()]

        logger.debug(f"Processing {len(genres)} genres for track: {genres}")

        excluded_genres = {g.lower() for g in app_config.get_excluded_genres()}

        for genre_name in genres:
            if not genre_name:
                continue

            if genre_name.lower() in excluded_genres:
                logger.debug(
                    f"Skipping excluded genre '{genre_name}' for track: {track.track_name}"
                )
                continue

            try:
                # Get or create genre, checking aliases first so a name
                # merged/aliased to a canonical genre doesn't recreate it.
                genre = self.controller.get.resolve_entity_or_alias(
                    "Genre", "genre_name", genre_name
                )
                if not genre:
                    genre = self.controller.add.add_entity(
                        "Genre", commit=False, genre_name=genre_name
                    )

                # Create relationship
                relationship_data = {
                    "track_id": track.track_id,
                    "genre_id": genre.genre_id,
                }

                self.controller.add.add_entity(
                    "TrackGenre", commit=False, **relationship_data
                )
                logger.debug(
                    f"Created genre relationship: {genre_name} -> {track.track_name}"
                )
            except SQLAlchemyError as e:
                logger.error(
                    f"Error creating genre relationship for '{genre_name}': {e}"
                )

    def process_path(self, path: str) -> list[str]:
        """
        Recursively scan the given path for audio files with supported extensions.

        Args:
            path: File system path to scan

        Returns:
            Sorted, deduplicated list of audio file paths

        """
        path_obj = Path(path)
        if not path_obj.exists():
            logger.error(f"Path does not exist: {path}")
            return []

        if path_obj.is_file():
            return [str(path_obj)] if self._is_supported_audio_file(path_obj) else []

        return self._scan_directory(path_obj)

    def _is_supported_audio_file(self, file_path: Path) -> bool:
        """Check if file has supported audio extension."""
        return file_path.suffix.lower() in self.SUPPORTED_EXTENSIONS

    def _scan_directory(self, directory: Path) -> list[str]:
        """Scan directory recursively for audio files."""
        audio_files = set()
        try:
            for file_path in directory.rglob("*"):
                if self._should_process_file(file_path):
                    audio_files.add(str(file_path))
        except OSError as e:
            logger.error(f"Error scanning directory {directory}: {e}", exc_info=True)
            return []

        return sorted(audio_files)

    def _should_process_file(self, file_path: Path) -> bool:
        """Check if file should be processed as audio file."""
        try:
            return file_path.is_file() and self._is_supported_audio_file(file_path)
        except (OSError, PermissionError) as e:
            logger.debug(f"Skipping inaccessible file {file_path}: {e}")
            return False

    def _track_exists(self, file_path: str) -> bool:
        """Check if track already exists in database."""
        existing_track = self.controller.get.get_entity_object(
            "Track", track_file_path=file_path
        )
        if existing_track:
            logger.info(f"Skipping existing track: {file_path}")
            return True
        return False

    def _log_metadata_debug(self, metadata: dict[str, Any], file_path: str):
        """Log metadata for debugging purposes with multi-value focus."""
        logger.debug(f"Metadata for {file_path}:")

        # Log multi-value fields specifically
        multi_value_fields = [
            "artist_name",
            "album_artist_name",
            "genre_name",
            "artist_composer",
            "artist_producer",
            "artist_remixer",
        ]

        logger.debug("Multi-value fields:")
        for field in multi_value_fields:
            value = metadata.get(field, "NOT FOUND")
            logger.debug(f"  {field}: {value} (type: {type(value)})")

        # Log first 20 items (skip binary data)
        for key, value in list(metadata.items())[:20]:
            if key != "album_art_data" and key not in multi_value_fields:
                logger.debug(f"  {key}: {value}")

    def _debug_metadata_types(self, metadata: dict[str, Any]):
        """Log metadata types for debugging."""
        logger.debug("Metadata types:")
        for key, value in list(metadata.items())[:30]:  # First 30 items
            if value is not None and not isinstance(value, (str, int, float, bool)):
                logger.debug(f"  {key}: {type(value)} -> {str(value)[:100]}...")


class ImportWorker(CancellableWorker):
    """
    Background import worker with resource monitoring and graceful error handling.
    Processes audio files in batches with comprehensive progress tracking.
    """

    progress = Signal(int, int)  # current, total
    finished = Signal(int)  # successful_imports
    error_occurred = Signal(str)  # error_message
    resource_warning = Signal(str, float)  # warning_type, value

    def __init__(self, controller, paths: list[str]):
        super().__init__()
        self.controller = controller
        self.paths = [Path(path) for path in paths]
        self.importer = TrackImporter(controller)
        self.processed_count = 0
        self._resource_check_interval = 50
        self._memory_warning_threshold_mb = 500
        self._clear_cache_interval = 20

    def run(self):
        """Process all paths in the background with comprehensive monitoring."""
        try:
            successful_imports = self._process_all_files()
            logger.info(f"Import completed: {successful_imports} successful imports")
            self.finished.emit(successful_imports)
        except Exception as e:  # ruff: ignore[blind-except]
            error_msg = f"Import worker failed: {e!s}"
            logger.exception(error_msg)
            self.error_occurred.emit(error_msg)

    def _process_all_files(self) -> int:
        """Collect and process all audio files, returning successful import count."""
        all_files = self._collect_audio_files()
        if not all_files:
            logger.warning("No audio files found in provided paths")
            return 0

        total_files = len(all_files)
        logger.info(f"Total files to process: {total_files}")
        self.progress.emit(0, total_files)

        successful_imports = 0
        for i, file_path in enumerate(all_files):
            if self.is_cancelled:
                logger.info("Import stopped by user request")
                break

            self.processed_count = i + 1
            successful_imports += self._process_single_file(file_path, i, total_files)
            self._perform_periodic_resource_check(i)

        return successful_imports

    def _collect_audio_files(self) -> list[Path]:
        """Collect all audio files from all provided paths."""
        all_files = []
        for path in self.paths:
            if self.is_cancelled:
                break
            if path.exists():
                files = self.importer.process_path(str(path))
                all_files.extend([Path(f) for f in files])
                logger.info(f"Found {len(files)} files in {path}")
            else:
                logger.warning(f"Path does not exist: {path}")
        return all_files

    def _process_single_file(self, file_path: Path, index: int, total: int) -> int:
        """Process a single file and return 1 if newly imported, 0 otherwise."""
        try:
            result = self.importer.add_track(str(file_path))
            self.progress.emit(index + 1, total)

            # Periodically detach committed entities from the SQLAlchemy
            # session's identity map. Every add_entity()/get_entity_object()
            # call during import leaves its object cached in the session, and
            # since the session lives for the whole import (and beyond), that
            # map grows without bound over a large library.
            if index % self._clear_cache_interval == 0:
                self.controller.get.session.expunge_all()
                logger.debug("Expunged ORM session cache to free memory")

            return 1 if result is ImportResult.IMPORTED else 0
        except MemoryError:
            # Clear the session's identity map and try to continue
            self.controller.get.session.expunge_all()
            error_msg = f"Memory error processing file {index + 1}/{total}: {file_path}"
            logger.error(error_msg)
            self.error_occurred.emit(error_msg)
        except Exception as e:
            error_msg = f"Error processing {file_path.name}: {str(e)[:200]}"
            logger.exception(error_msg)
        return 0

    def _perform_periodic_resource_check(self, index: int):
        """Check system resources at specified intervals."""
        if index % self._resource_check_interval == 0:
            self._check_resources()

    def _check_resources(self):
        """Check system resources and emit warnings if thresholds exceeded."""
        process = psutil.Process()
        memory_mb = process.memory_info().rss / 1024 / 1024
        cpu_percent = process.cpu_percent()

        logger.info(
            f"Resource check - Processed: {self.processed_count}, "
            f"Memory: {memory_mb:.1f}MB, CPU: {cpu_percent:.1f}%"
        )

        if memory_mb > self._memory_warning_threshold_mb:
            warning_msg = f"High memory usage: {memory_mb:.1f}MB"
            logger.warning(warning_msg)
            self.resource_warning.emit("memory", memory_mb)
