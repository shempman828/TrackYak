from typing import Any, Dict, List, Optional

from src.importing.artist_field_extraction import (
    ALBUM_ARTIST_FIELDS,
    extract_artists_from_metadata,
)
from src.core.logger_config import logger


class AlbumImporter:
    """Used by two independent callers with different transaction needs:
    TrackImporter (one all-or-nothing transaction per track — passes
    commit=False and commits/rolls back itself) and LibraryRepair (no
    wrapping transaction of its own — relies on the commit=True default
    below, same as before this class supported deferred commits)."""

    def __init__(self, controller):
        self.controller = controller

    def _get_or_create_album(self, metadata: Dict[str, Any], commit: bool = True):
        """Get existing album or create new one with comprehensive metadata.

        Raises on failure rather than swallowing: the album is required by
        the track/disc/relationship rows created after it, so a caller
        batching all of it into one transaction (commit=False) needs to see
        the failure and roll back the whole thing instead of silently
        proceeding without an album.
        """
        album_name = self._extract_album_name(metadata)
        release_year = self._extract_release_year(metadata)
        artist_ids = self._process_album_artists(metadata, commit=commit)

        existing_album = self._find_existing_album(
            album_name, release_year, artist_ids
        )
        if existing_album:
            return existing_album

        return self._create_new_album(
            album_name, release_year, artist_ids, metadata, commit=commit
        )

    def _extract_album_name(self, metadata: Dict[str, Any]) -> str:
        """Extract album name from metadata with fallback."""
        album_name = metadata.get("album_album_name") or metadata.get("album_name")
        return album_name or "Unknown Album"

    def _extract_release_year(self, metadata: Dict[str, Any]) -> Optional[str]:
        """Extract release year from metadata."""
        return metadata.get("album_release_year") or metadata.get("release_year")

    def _process_album_artists(
        self, metadata: Dict[str, Any], commit: bool = True
    ) -> List[int]:
        """Process album artists only, with proper role handling."""
        processed_artist_names = set()
        artist_ids = []

        # Process ONLY album artists - don't fall back to track artists.
        # Same field-priority/normalization TrackImporter uses for the
        # "Album Artist" role, so the two can't drift out of sync.
        album_artists = extract_artists_from_metadata(metadata, ALBUM_ARTIST_FIELDS)
        for artist_name in album_artists:
            artist_id = self._process_artist_name(
                artist_name, processed_artist_names, commit=commit
            )
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
            "release_month": metadata.get("album_release_month")
            or metadata.get("release_month"),
            "release_day": metadata.get("album_release_day")
            or metadata.get("release_day"),
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

    def _create_album_artist_relationships(
        self, album_id: int, artist_ids: List[int], commit: bool = True
    ):
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
                commit=commit,
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
        commit: bool = True,
    ):
        """Create a new album with all associated data and relationships."""
        album_data = self._prepare_album_data(album_name, release_year, metadata)

        new_album = self.controller.add.add_entity(
            "Album", commit=commit, **album_data
        )
        if not new_album:
            raise RuntimeError(f"Failed to create album: {album_name}")

        # Create relationships ONLY if we have album artists
        if artist_ids:
            self._create_album_artist_relationships(
                new_album.album_id, artist_ids, commit=commit
            )

        self._create_album_publisher_relationships(
            new_album.album_id, metadata, commit=commit
        )

        logger.debug(f"Created new album: {album_name} (ID: {new_album.album_id})")
        return new_album

    def _get_or_create_disc(
        self, album_id: int, metadata: Dict[str, Any], commit: bool = True
    ):
        """Get existing disc or create a new one based on the track's disc number.

        Returns None if the metadata has no disc number, in which case the
        track is simply left unassigned to any disc (matches prior behavior
        for single-disc releases with no DISCNUMBER/TPOS tag).
        """
        disc_number = metadata.get("disc_number")
        if disc_number is None:
            return None

        existing_disc = self.controller.get.get_entity_object(
            "Disc", album_id=album_id, disc_number=disc_number
        )
        if existing_disc:
            return existing_disc

        disc_data = {
            "album_id": album_id,
            "disc_number": disc_number,
            "disc_title": metadata.get("disc_title"),
            "media_type": metadata.get("media_type"),
        }
        disc_data = {k: v for k, v in disc_data.items() if v is not None}

        new_disc = self.controller.add.add_entity("Disc", commit=commit, **disc_data)
        logger.debug(
            f"Created new disc: album_id={album_id}, disc_number={disc_number}"
        )
        return new_disc

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
        self, album_id: int, metadata: Dict[str, Any], commit: bool = True
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

            name = publisher_name.strip()
            publisher = self._resolve_publisher(name)
            if not publisher:
                publisher = self.controller.add.add_entity(
                    "Publisher", commit=commit, publisher_name=name
                )

            # Create album-publisher relationship
            self.controller.add.add_entity(
                "AlbumPublisher",
                commit=commit,
                album_id=album_id,
                publisher_id=publisher.publisher_id,
            )
            logger.debug(
                f"Created publisher relationship: {publisher_name} -> album {album_id}"
            )

    def _resolve_publisher(self, publisher_name: str):
        """Resolve a publisher name to its canonical Publisher entity,
        checking known aliases so a name the user has aliased to a
        canonical publisher (directly, or via a merge) doesn't recreate a
        duplicate publisher on every import.
        """
        return self.controller.get.resolve_entity_or_alias(
            "Publisher", "publisher_name", publisher_name
        )

    def _process_artist_name(
        self,
        artist_name: str,
        processed_names: set,
        is_group: Optional[int] = None,
        commit: bool = True,
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

            new_artist = self.controller.add.add_entity(
                "Artist", commit=commit, **create_kwargs
            )
            return new_artist.artist_id
