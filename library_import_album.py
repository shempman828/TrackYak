from typing import Any, Dict, List, Optional

from asset_paths import ALBUM_ART_DIR
from logger_config import logger


class AlbumImporter:
    def __init__(self, controller):
        self.controller = controller

    def _get_or_create_album(self, metadata: Dict[str, Any]):
        """Get existing album or create new one with comprehensive metadata."""
        try:
            album_name = self._extract_album_name(metadata)
            release_year = self._extract_release_year(metadata)
            artist_ids = self._process_album_artists(metadata)

            existing_album = self._find_existing_album(
                album_name, release_year, artist_ids
            )
            if existing_album:
                return existing_album

            return self._create_new_album(
                album_name, release_year, artist_ids, metadata
            )

        except Exception as e:
            logger.error(f"Error in album processing: {e}", exc_info=True)
            return self._create_fallback_album(metadata)

    def _extract_album_name(self, metadata: Dict[str, Any]) -> str:
        """Extract album name from metadata with fallback."""
        album_name = metadata.get("album_album_name") or metadata.get("album_name")
        return album_name or "Unknown Album"

    def _extract_release_year(self, metadata: Dict[str, Any]) -> Optional[str]:
        """Extract release year from metadata."""
        return metadata.get("album_release_year") or metadata.get("release_year")

    def _extract_album_artists_list(self, metadata: Dict[str, Any]) -> List[str]:
        """Extract and normalize album artists list from metadata."""
        album_artists = (
            metadata.get("album_artist_name")
            or metadata.get("artist_album_artist")
            or []
        )

        if isinstance(album_artists, str):
            return [album_artists]
        elif album_artists is None:
            return []
        return album_artists

    def _process_album_artists(self, metadata: Dict[str, Any]) -> List[int]:
        """Process album artists only, with proper role handling."""
        processed_artist_names = set()
        artist_ids = []

        # Process ONLY album artists - don't fall back to track artists
        album_artists = self._extract_album_artists_list(metadata)
        for artist_name in album_artists:
            artist_id = self._process_artist_name(artist_name, processed_artist_names)
            if artist_id:
                artist_ids.append(artist_id)

        # Final deduplication and sorting
        return sorted(set(artist_ids))

    def _find_existing_album(
        self, album_name: str, release_year: Optional[str], artist_ids: List[int]
    ) -> Optional[Any]:
        """Find existing album using multiple strategies."""
        # Strategy 1: Try exact match with album name, year, and artists
        if artist_ids:
            existing_album = self.controller.get.get_album_exists(
                album_name, release_year, artist_ids
            )
            if existing_album:
                logger.debug(
                    f"Using existing album: {existing_album.album_name} (ID: {existing_album.album_id})"
                )
                return existing_album

        # Strategy 2: Try to find album by name and year only (as fallback)
        potential_albums = self.controller.get.get_all_entities(
            "Album", album_name=album_name, release_year=release_year
        )
        if potential_albums:
            existing_album = potential_albums[0]
            logger.debug(
                f"Using existing album (fallback match): {existing_album.album_name}"
            )
            return existing_album

        return None

    def _prepare_album_data(
        self, album_name: str, release_year: Optional[str], metadata: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Prepare album data dictionary from metadata."""
        album_data = {
            "album_name": album_name,
            "release_year": release_year,
            "release_month": metadata.get("release_month"),
            "release_day": metadata.get("release_day"),
            "album_description": metadata.get("album_description"),
            "catalog_number": metadata.get("album_catalog_number"),
            "is_compilation": metadata.get("is_compilation"),
            "album_language": metadata.get("album_language"),
            "album_gain": metadata.get("album_gain"),
            "album_peak": metadata.get("album_peak"),
            "release_type": metadata.get("album_release_type"),
            "status": metadata.get("status"),
            "estimated_sales": metadata.get("estimated_sales"),
            "MBID": metadata.get("album_MBID"),
        }

        # Remove None values
        return {k: v for k, v in album_data.items() if v is not None}

    def _create_album_artist_relationships(self, album_id: int, artist_ids: List[int]):
        """Create album-artist relationships for all artists."""
        # Get existing relationships first
        existing_associations = self.controller.get.get_all_entities(
            "AlbumRoleAssociation", album_id=album_id
        )
        existing_artist_ids = (
            {assoc.artist_id for assoc in existing_associations}
            if existing_associations
            else set()
        )

        for artist_id in artist_ids:
            if artist_id in existing_artist_ids:
                logger.debug(
                    f"Album-artist relationship already exists: album_id={album_id}, artist_id={artist_id}"
                )
                continue

            self.controller.add.add_entity(
                "AlbumRoleAssociation",
                album_id=album_id,
                artist_id=artist_id,
                role_id=1,  # Assuming 1 is the role ID for "Album Artist"
            )
            logger.debug(
                f"Created album-artist relationship: album_id={album_id}, artist_id={artist_id}"
            )

    def _create_new_album(
        self,
        album_name: str,
        release_year: Optional[str],
        artist_ids: List[int],
        metadata: Dict[str, Any],
    ):
        """Create a new album with all associated data and relationships."""
        album_data = self._prepare_album_data(album_name, release_year, metadata)

        new_album = self.controller.add.add_entity("Album", **album_data)

        # Create relationships ONLY if we have album artists
        if artist_ids:
            self._create_album_artist_relationships(new_album.album_id, artist_ids)

        self._create_album_publisher_relationships(new_album.album_id, metadata)

        # Handle artwork
        logger.debug(f"Created new album: {album_name} (ID: {new_album.album_id})")
        if "album_art_data" in metadata:
            album_artists = self._extract_album_artists_list(metadata)
            self._save_album_artwork(new_album, metadata, album_artists)

        return new_album

    def _get_album_artists(self, album_id: int) -> List[int]:
        """Get all artist IDs associated with an album."""
        try:
            associations = self.controller.get.get_all_entities(
                "AlbumRoleAssociation", album_id=album_id
            )
            return [assoc.artist_id for assoc in associations] if associations else []
        except Exception as e:
            logger.error(f"Error getting album artists: {e}")
            return []

    def _create_album_publisher_relationships(
        self, album_id: int, metadata: Dict[str, Any]
    ):
        """Create publisher relationships for an album."""
        publisher_names = metadata.get("publisher_name")
        if not publisher_names:
            return

        # Handle single publisher or list of publishers
        if isinstance(publisher_names, str):
            publisher_names = [publisher_names]

        for publisher_name in publisher_names:
            if not publisher_name or publisher_name.strip() == "":
                continue

            # Get or create publisher
            publisher = self.controller.get.get_entity_object(
                "Publisher", publisher_name=publisher_name
            )
            if not publisher:
                publisher = self.controller.add.add_entity(
                    "Publisher", publisher_name=publisher_name.strip()
                )

            # Create album-publisher relationship
            self.controller.add.add_entity(
                "AlbumPublisher", album_id=album_id, publisher_id=publisher.publisher_id
            )
            logger.debug(
                f"Created publisher relationship: {publisher_name} -> album {album_id}"
            )

    def _save_album_artwork(
        self, album, metadata: Dict[str, Any], album_artists: List[str]
    ):
        """Save album artwork to file system and update album record."""
        try:
            album_art_data = metadata.get("album_art_data")
            if not album_art_data:
                logger.debug(f"No album art data found for album: {album.album_name}")
                return

            # Get album artist name for directory structure
            if album_artists:
                # Use first album artist for directory
                album_artist_name = (
                    album_artists[0]
                    if isinstance(album_artists[0], str)
                    else album_artists[0].artist_name
                )
            else:
                album_artist_name = "Unknown Artist"

            # Sanitize names for filesystem
            safe_artist_name = self._sanitize_filename(album_artist_name)
            safe_album_name = self._sanitize_filename(album.album_name)

            # Create directory structure: ALBUM_ART_DIR/artist/album/
            # FIX: ALBUM_ART_DIR is a Path object, not a function
            art_dir = ALBUM_ART_DIR / safe_artist_name / safe_album_name
            art_dir.mkdir(parents=True, exist_ok=True)

            # Determine file extension from image format
            image_format = album_art_data.get("format", "jpg").lower()
            if image_format == "jpeg":
                image_format = "jpg"
            ext = f".{image_format}"

            # Save front cover
            front_cover_path = art_dir / f"frontcover{ext}"
            with open(front_cover_path, "wb") as f:
                f.write(album_art_data["data"])

            # Update album record with front cover path
            self.controller.update.update_entity(
                "Album", album.album_id, front_cover_path=str(front_cover_path)
            )

            logger.debug(f"Saved album art: {front_cover_path}")

        except Exception as e:
            logger.error(f"Error saving album artwork for {album.album_name}: {e}")

    def _sanitize_filename(self, name: str) -> str:
        """Sanitize filename for filesystem use."""
        if not name:
            return "Unknown"

        # Replace problematic characters
        invalid_chars = '<>:"/\\|?*'
        for char in invalid_chars:
            name = name.replace(char, "_")

        # Remove leading/trailing spaces and dots
        name = name.strip(" .")

        # Limit length
        if len(name) > 100:
            name = name[:100]

        return name

    def _process_artist_name(
        self, artist_name: str, processed_names: set, is_group: Optional[int] = None
    ) -> Optional[int]:
        """Process individual artist name and return artist ID."""
        if not artist_name or not artist_name.strip():
            return None

        # Normalize the artist name for comparison
        normalized_name = artist_name.strip().lower()
        if normalized_name in processed_names:
            return None  # Already processed this artist

        processed_names.add(normalized_name)

        # Use normalized name for database lookup to ensure case-insensitive matching
        artist = self.controller.get.get_entity_object(
            "Artist",
            artist_name=artist_name.strip(),  # Use the properly formatted name
        )
        if artist:
            return artist.artist_id
        else:
            # Create artist if doesn't exist
            create_kwargs = {"artist_name": artist_name.strip()}
            if is_group is not None:
                create_kwargs["isgroup"] = is_group

            new_artist = self.controller.add.add_entity("Artist", **create_kwargs)
            return new_artist.artist_id

    def _extract_track_artists_list(self, metadata: Dict[str, Any]) -> List[str]:
        """Extract and normalize track artists list from metadata."""
        track_artists = metadata.get("artist_name") or []

        if isinstance(track_artists, str):
            return [track_artists]
        return track_artists
