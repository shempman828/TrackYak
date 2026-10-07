"""Vorbis comment block writer for FLAC and Ogg files."""

import struct

from src.foundation.logger_config import logger


class VorbisCommentWriter:
    """Serializes a tag dict into a raw Vorbis comment block."""

    VENDOR_STRING = "MusicLibrary Database Writer"

    def __init__(self):
        """Set the vendor string written into each block."""
        self.vendor_string = self.VENDOR_STRING

    def build_vorbis_comments(self, comments: dict[str, str | list[str]]) -> bytes:
        """Build a raw comment block (no Ogg framing); list values become repeated entries."""
        comments = comments or {}  # an empty dict still gives a valid empty block

        pairs: list[tuple[str, str]] = []
        for field, value in comments.items():
            if value is None or value == "":
                continue
            field_upper = field.upper()
            if isinstance(value, list):
                for v in value:
                    s = self.sanitize_value(str(v))
                    if s:
                        pairs.append((field_upper, s))
            else:
                s = self.sanitize_value(str(value))
                if s:
                    pairs.append((field_upper, s))

        # Vendor string
        vendor_bytes = self.vendor_string.encode("utf-8")
        vendor_block = struct.pack("<I", len(vendor_bytes)) + vendor_bytes

        # Comment list
        comment_count = struct.pack("<I", len(pairs))
        comment_data = b"".join(self._encode_comment(field, value) for field, value in pairs)

        logger.debug(f"Built Vorbis comment block with {len(pairs)} entries")
        return vendor_block + comment_count + comment_data

    def sanitize_value(self, value: str) -> str:
        """Replace newlines with spaces and strip the value; public so diffs apply the same normalisation."""
        if not value:
            return ""
        # Newlines are illegal in Vorbis comment values
        return value.replace("\r\n", " ").replace("\n", " ").replace("\r", " ").strip()

    def _encode_comment(self, field: str, value: str) -> bytes:
        """Encode a single FIELD=value comment entry with its length prefix."""
        comment = f"{field}={value}"
        encoded = comment.encode("utf-8")
        return struct.pack("<I", len(encoded)) + encoded
