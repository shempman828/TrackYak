"""Construct raw FLAC METADATA_BLOCK_PICTURE payloads for embedding artwork."""

import io
import struct

from PIL import Image

from src.metadata.metadata_image_utils import determine_image_format, mime_type_for_format

_MODE_BIT_DEPTHS = {
    "1": 1,
    "L": 8,
    "P": 8,
    "RGB": 24,
    "RGBA": 32,
    "CMYK": 32,
    "I": 32,
    "F": 32,
}


class FlacPictureWriter:
    """Builds METADATA_BLOCK_PICTURE payloads, per the MusicBrainz/ID3-APIC
    picture-type convention used by the reader (ArtworkExtractor)."""

    ROLE_TO_TYPE = {"front": 3, "rear": 4, "liner": 5}
    TYPE_TO_ROLE = {v: k for k, v in ROLE_TO_TYPE.items()}

    def build_picture_block(
        self, role: str, image_bytes: bytes, description: str = ""
    ) -> bytes:
        """Build a raw METADATA_BLOCK_PICTURE payload (no block header) for `role`."""
        if role not in self.ROLE_TO_TYPE:
            raise ValueError(f"Unknown artwork role: {role}")

        image = Image.open(io.BytesIO(image_bytes))
        image.load()
        width, height = image.size
        depth = _MODE_BIT_DEPTHS.get(image.mode, 24)

        format_type = determine_image_format(image_bytes)
        mime = mime_type_for_format(format_type)

        picture_type = self.ROLE_TO_TYPE[role]
        mime_bytes = mime.encode("utf-8")
        desc_bytes = description.encode("utf-8")

        payload = bytearray()
        payload += struct.pack(">I", picture_type)
        payload += struct.pack(">I", len(mime_bytes))
        payload += mime_bytes
        payload += struct.pack(">I", len(desc_bytes))
        payload += desc_bytes
        payload += struct.pack(">I", width)
        payload += struct.pack(">I", height)
        payload += struct.pack(">I", depth)
        payload += struct.pack(">I", 0)  # colors used - 0 means not indexed/palette
        payload += struct.pack(">I", len(image_bytes))
        payload += image_bytes
        return bytes(payload)
