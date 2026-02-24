"""
Vorbis comment block writer for FLAC and OGG files.

Key design: Vorbis comments support repeated keys (e.g. multiple GENRE tags).
We accept Dict[str, str | List[str]] and expand lists into repeated comment entries.
This is the correct format per the Vorbis I specification and expected by Picard.
"""

import struct
from typing import Dict, List, Union


class VorbisCommentWriter:
    """Handles writing Vorbis comments to FLAC/OGG files."""

    VENDOR_STRING = "MusicLibrary Database Writer"

    def __init__(self):
        self.vendor_string = self.VENDOR_STRING

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build_vorbis_comments(
        self, comments: Dict[str, Union[str, List[str]]]
    ) -> bytes:
        """Build a complete Vorbis comment block from a tag dict.

        Values may be a plain string or a list of strings. Lists are expanded
        into repeated comment entries (e.g. multiple GENRE= lines), which is
        the correct Vorbis specification behaviour and what Picard expects.

        Returns raw bytes suitable for embedding in a FLAC metadata block
        or an OGG Vorbis page (without the Vorbis packet framing byte).
        """
        if not comments:
            # Still write a valid empty comment block
            comments = {}

        # Flatten to list of (field, value) pairs, expanding lists
        pairs: List[tuple[str, str]] = []
        for field, value in comments.items():
            if value is None or value == "":
                continue
            field_upper = field.upper()
            if isinstance(value, list):
                for v in value:
                    s = self._escape(str(v))
                    if s:
                        pairs.append((field_upper, s))
            else:
                s = self._escape(str(value))
                if s:
                    pairs.append((field_upper, s))

        # Vendor string
        vendor_bytes = self.vendor_string.encode("utf-8")
        vendor_block = struct.pack("<I", len(vendor_bytes)) + vendor_bytes

        # Comment list
        comment_count = struct.pack("<I", len(pairs))
        comment_data = b"".join(
            self._encode_comment(field, value) for field, value in pairs
        )

        return vendor_block + comment_count + comment_data

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _escape(self, value: str) -> str:
        """Sanitise a tag value: strip newlines, leave = unescaped in value."""
        if not value:
            return ""
        # Newlines are illegal in Vorbis comment values
        return value.replace("\r\n", " ").replace("\n", " ").replace("\r", " ").strip()

    def _encode_comment(self, field: str, value: str) -> bytes:
        """Encode a single FIELD=value comment entry with its length prefix."""
        comment = f"{field}={value}"
        encoded = comment.encode("utf-8")
        return struct.pack("<I", len(encoded)) + encoded
