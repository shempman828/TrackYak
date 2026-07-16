"""Shared artist-field-priority and normalization logic.

TrackImporter (all credited-artist roles) and AlbumImporter (album artists
specifically) both need to pull artist names out of the flattened metadata
dict by trying a prioritized list of field names and normalizing whatever
shape the value comes in (delimited string vs. list). Previously each had
its own independent copy of this logic, and the copies had quietly drifted:
AlbumImporter's never split delimited strings ("Artist A; Artist B") into
separate artists the way TrackImporter's did. Centralizing it here removes
that drift risk.
"""

from typing import Any, Dict, List

from src.core.logger_config import logger

# Field-name priority lists, in the order they should be checked.
ALBUM_ARTIST_FIELDS = ["artist_album_artist", "album_artist_name"]
TRACK_ARTIST_FIELDS = ["artist_name", "artist_primary_artist"]


def normalize_artists_data(artists_data: Any) -> List[str]:
    """Normalize artist metadata from various formats to a consistent list.

    Handles:
    - String: "Artist1; Artist2" or "Artist1, Artist2"
    - List: ["Artist1", "Artist2"]
    - None: returns an empty list
    """
    if artists_data is None:
        return []

    if isinstance(artists_data, str):
        artists = []
        for delimiter in [";", ",", "/", "|"]:
            if delimiter in artists_data:
                artists = [
                    artist.strip()
                    for artist in artists_data.split(delimiter)
                    if artist.strip()
                ]
                break

        if not artists:
            artists = [artists_data.strip()] if artists_data.strip() else []

        return artists

    if isinstance(artists_data, list):
        return [
            artist.strip() for artist in artists_data if artist and artist.strip()
        ]

    return normalize_artists_data(str(artists_data))


def extract_artists_from_metadata(
    metadata: Dict[str, Any], field_names: List[str]
) -> List[str]:
    """Try each field name in priority order; return the first field's
    normalized artist list that isn't empty.

    Args:
        metadata: The metadata dictionary.
        field_names: Field names to check, in priority order.

    Returns:
        List of artist names, or an empty list if none of the fields had
        usable data.
    """
    for field_name in field_names:
        artists_data = metadata.get(field_name)
        if artists_data is None:
            continue

        if isinstance(artists_data, list) and len(artists_data) == 0:
            continue

        normalized = normalize_artists_data(artists_data)
        if normalized:
            return normalized

    logger.debug(f"No artist data found in metadata fields: {field_names}")
    return []
