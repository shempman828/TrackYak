"""Shared image format detection helpers for embedded artwork read/write."""

from collections.abc import Callable
from typing import Any

from src.foundation.logger_config import logger

_FORMAT_TO_MIME = {"JPEG": "image/jpeg", "PNG": "image/png", "GIF": "image/gif", "BMP": "image/bmp"}

# MusicBrainz/ID3-APIC picture-type convention used to assign a role to
# each embedded picture, shared by every format's reader and writer so they
# all agree on which picture "is" the front/rear/liner cover. Picard uses
# type 5 ("Leaflet page") for liner/booklet art.
ARTWORK_ROLE_TO_TYPE = {"front": 3, "rear": 4, "liner": 5}
ARTWORK_TYPE_TO_ROLE = {v: k for k, v in ARTWORK_ROLE_TO_TYPE.items()}


def find_picture_indices_for_role(
    items: list[Any], role: str, picture_type_for_item: Callable[[Any], int | None]
) -> list[int]:
    """
    Find *every* index in `items` of a picture that represents `role`,
    using a typed + untyped-fallback-to-front rule: pictures whose type
    maps to `role` win; if none do and role is "front" and there's
    exactly one picture with no resolvable type, that one is treated as
    the (untyped) front cover.

    Normally the returned list has 0 or 1 entry, but a file that a
    third-party tagger appended a second same-type picture to (instead of
    replacing the first) yields more than one - writers strip all of them
    before re-embedding so the file ends up with a single picture per
    role. Indices are returned in ascending order.

    `picture_type_for_item(item)` returns the item's raw picture-type
    integer, or None if `item` isn't a picture at all (or its type can't
    be parsed) - such items are ignored entirely, matching
    ArtworkExtractor.extract_artwork_by_role so readers and writers agree.
    """
    typed_indices: dict[str, list[int]] = {}
    untyped_indices = []

    for idx, item in enumerate(items):
        picture_type = picture_type_for_item(item)
        if picture_type is None:
            continue
        mapped_role = ARTWORK_TYPE_TO_ROLE.get(picture_type)
        if mapped_role:
            typed_indices.setdefault(mapped_role, []).append(idx)
        else:
            untyped_indices.append(idx)

    if role in typed_indices:
        return typed_indices[role]

    if role == "front" and "front" not in typed_indices and len(untyped_indices) == 1:
        return [untyped_indices[0]]

    return []


def find_picture_index_for_role(
    items: list[Any], role: str, picture_type_for_item: Callable[[Any], int | None]
) -> int | None:
    """First index representing `role` (see find_picture_indices_for_role),
    or None. Used by the MP3 writer, which replaces a single APIC frame."""
    indices = find_picture_indices_for_role(items, role, picture_type_for_item)
    return indices[0] if indices else None


def determine_image_format(image_data: bytes, mime_type: str = "") -> str | None:
    """Determine image format from magic bytes, falling back to MIME type."""
    if image_data.startswith(b"\xff\xd8\xff"):
        return "JPEG"
    if image_data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "PNG"
    if image_data.startswith(b"GIF8"):
        return "GIF"
    if image_data.startswith(b"BM"):
        return "BMP"

    if "jpeg" in mime_type.lower() or "jpg" in mime_type.lower():
        return "JPEG"
    if "png" in mime_type.lower():
        return "PNG"

    logger.debug(
        f"Unrecognized image format (mime_type={mime_type!r}); "
        "could not determine format from magic bytes"
    )
    return None


def mime_type_for_format(format_type: str | None) -> str:
    """Map an internal format label (JPEG/PNG/...) to a MIME type for embedding."""
    return _FORMAT_TO_MIME.get(format_type, "application/octet-stream")
