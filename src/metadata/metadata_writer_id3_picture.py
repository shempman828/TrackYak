"""Construct raw ID3v2 APIC frames for embedding artwork into MP3 files."""

import struct

from src.metadata.metadata_image_utils import determine_image_format, mime_type_for_format


class Id3PictureWriter:
    """Builds APIC frames, per the same MusicBrainz/ID3-APIC picture-type
    convention used by the FLAC writer (and the reader, ArtworkExtractor)."""

    ROLE_TO_TYPE = {"front": 3, "rear": 4, "liner": 5}
    TYPE_TO_ROLE = {v: k for k, v in ROLE_TO_TYPE.items()}

    def build_apic_frame(self, role: str, image_bytes: bytes, description: str = "") -> bytes:
        """Build a complete APIC frame (header + body) for `role`, always as
        an ID3v2.3-style frame - matching what ID3TagWriter.build_id3_tag
        writes the surrounding tag as."""
        if role not in self.ROLE_TO_TYPE:
            raise ValueError(f"Unknown artwork role: {role}")

        format_type = determine_image_format(image_bytes)
        mime = mime_type_for_format(format_type)
        picture_type = self.ROLE_TO_TYPE[role]

        payload = bytearray()
        payload += struct.pack(">B", 0x00)  # encoding: ISO-8859-1 (simplest, ASCII-safe)
        payload += mime.encode("ascii", errors="ignore") + b"\x00"
        payload += struct.pack(">B", picture_type)
        payload += description.encode("iso-8859-1", errors="ignore") + b"\x00"
        payload += image_bytes

        frame_header = b"APIC" + struct.pack(">I", len(payload)) + b"\x00\x00"
        return frame_header + bytes(payload)
