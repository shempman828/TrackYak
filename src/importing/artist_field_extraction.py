"""Shared artist-field-priority and normalization logic for track/album import."""

from typing import Any

from src.foundation.logger_config import logger

# Field-name priority lists, in the order they should be checked.
ALBUM_ARTIST_FIELDS = ["artist_album_artist", "album_artist_name"]
TRACK_ARTIST_FIELDS = ["artist_name", "artist_primary_artist"]


def normalize_artists_data(artists_data: Any) -> list[str]:
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
        # "/" and "|" are deliberately not treated as delimiters here: they
        # commonly appear inside a single artist name (e.g. "AC/DC"), and
        # tag writers use ";" for genuine multi-artist values (see
        # metadata_text.py's " ; " joins).
        for delimiter in [";", ","]:
            if delimiter in artists_data:
                artists = [
                    artist.strip() for artist in artists_data.split(delimiter) if artist.strip()
                ]
                break

        if not artists:
            artists = [artists_data.strip()] if artists_data.strip() else []

        return artists

    if isinstance(artists_data, list):
        return [artist.strip() for artist in artists_data if artist and artist.strip()]

    logger.debug(f"Ignoring unsupported artist metadata type: {type(artists_data)}")
    return []


def extract_artists_from_metadata(metadata: dict[str, Any], field_names: list[str]) -> list[str]:
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
