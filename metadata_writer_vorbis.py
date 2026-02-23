import struct
from typing import Any, Dict


class VorbisCommentWriter:
    """Handles writing Vorbis comments to FLAC/OGG files."""

    def __init__(self):
        self.vendor_string = "MusicLibrary Database Writer"

    def escape_value(self, value: str) -> str:
        """Escape special characters in Vorbis comment values."""
        if value is None:
            return ""
        # Basic escaping - replace newlines and problematic characters
        return str(value).replace("\n", " ").replace("=", "\\=")

    def create_comment(self, field: str, value: Any) -> bytes:
        """Create a single Vorbis comment."""
        if value is None:
            return b""

        value_str = self.escape_value(str(value))
        comment = f"{field.upper()}={value_str}"
        encoded_comment = comment.encode("utf-8")

        # Length-prefixed string
        return struct.pack("<I", len(encoded_comment)) + encoded_comment

    def build_vorbis_comments(self, comments: Dict[str, Any]) -> bytes:
        """Build complete Vorbis comment block."""
        if not comments:
            return b""

        # Vendor string
        vendor_bytes = self.vendor_string.encode("utf-8")
        vendor_length = struct.pack("<I", len(vendor_bytes))

        # Comment count
        comment_count = struct.pack("<I", len(comments))

        # Build comments
        comment_data = b""
        for field, value in comments.items():
            if value is not None and value != "":
                comment_data += self.create_comment(field, value)

        # Vorbis comment header
        header = b"\x03vorbis"

        return header + vendor_length + vendor_bytes + comment_count + comment_data
