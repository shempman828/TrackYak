"""Shared image format detection helpers for embedded artwork read/write."""

from typing import Optional

_FORMAT_TO_MIME = {
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "GIF": "image/gif",
    "BMP": "image/bmp",
}


def determine_image_format(image_data: bytes, mime_type: str = "") -> Optional[str]:
    """Determine image format from magic bytes, falling back to MIME type."""
    if image_data.startswith(b"\xff\xd8\xff"):
        return "JPEG"
    elif image_data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "PNG"
    elif image_data.startswith(b"GIF8"):
        return "GIF"
    elif image_data.startswith(b"BM"):
        return "BMP"

    if "jpeg" in mime_type.lower() or "jpg" in mime_type.lower():
        return "JPEG"
    elif "png" in mime_type.lower():
        return "PNG"

    return None


def mime_type_for_format(format_type: Optional[str]) -> str:
    """Map an internal format label (JPEG/PNG/...) to a MIME type for embedding."""
    return _FORMAT_TO_MIME.get(format_type, "application/octet-stream")
