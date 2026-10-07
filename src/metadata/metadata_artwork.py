"""Embedded artwork extraction from audio file bytes."""

import io
from pathlib import Path
import struct
from typing import ClassVar

from PIL import Image

from src.foundation.logger_config import logger
from src.metadata.metadata_byte_utils import id3_frames_offset, is_valid_frame_id, split_id3_string, syncsafe_to_int
from src.metadata.metadata_image_utils import ARTWORK_TYPE_TO_ROLE, determine_image_format
from src.metadata.metadata_mp4_atoms import find_atom


class ArtworkExtractor:
    """Extracts embedded artwork, separately from text metadata."""

    # Picture-type -> role convention shared with the writers.
    PICTURE_TYPE_ROLES = ARTWORK_TYPE_TO_ROLE

    # Formats with role-based (front/rear/liner) read and write support.
    SUPPORTED_EXTENSIONS: ClassVar[set[str]] = {".flac", ".mp3"}

    def __init__(self):
        """Register the per-extension artwork readers."""
        self.format_handlers = {".mp3": self._extract_mp3_artwork, ".flac": self._extract_flac_artwork, ".m4a": self._extract_alac_artwork, ".mp4": self._extract_alac_artwork}

    def extract_artwork(self, data, file_ext):
        """Return the first embedded picture of the file bytes as a dict, or None."""
        try:
            handler = self.format_handlers.get(file_ext.lower())
            if not handler:
                logger.debug(f"No artwork handler for format: {file_ext}")
                return None

            artwork = handler(data)

            if artwork:
                logger.debug(f"Successfully extracted artwork: {len(artwork.get('data', []))} bytes")
            else:
                logger.debug("No artwork found")

            return artwork

        except AttributeError as e:
            logger.warning(f"Error extracting artwork: {e}")
            return None

    def _iter_id3_frames(self, data, version_major, end_pos):
        """Yield (frame_id, frame_start, frame_size) for each ID3v2 frame up to end_pos."""
        pos = id3_frames_offset(data)
        while pos < end_pos - 10:
            if not is_valid_frame_id(data[pos : pos + (3 if version_major == 2 else 4)]):
                break  # padding or a corrupt header
            if version_major == 2:  # ID3v2.2
                frame_id = data[pos : pos + 3].decode("ascii", errors="ignore")
                frame_size = struct.unpack(">I", b"\x00" + data[pos + 3 : pos + 6])[0]
                frame_start = pos + 6
            else:  # ID3v2.3/2.4
                frame_id = data[pos : pos + 4].decode("ascii", errors="ignore")
                frame_size = syncsafe_to_int(data[pos + 4 : pos + 8]) if version_major == 4 else struct.unpack(">I", data[pos + 4 : pos + 8])[0]
                frame_start = pos + 10

            if frame_size == 0:
                break

            yield frame_id, frame_start, frame_size
            pos = frame_start + frame_size

    def _extract_mp3_artwork(self, data):
        """Extract artwork from MP3 files (ID3v2 APIC frames)."""
        try:
            if len(data) < 10 or data[0:3] != b"ID3":
                return None

            version_major = data[3]
            size = syncsafe_to_int(data[6:10])
            end_pos = min(10 + size, len(data))

            for frame_id, frame_start, frame_size in self._iter_id3_frames(data, version_major, end_pos):
                if frame_id in ["APIC", "PIC"]:
                    return self._parse_id3_apic_frame(data[frame_start : frame_start + frame_size], version_major)

        except (IndexError, struct.error) as e:
            logger.warning(f"Error extracting MP3 artwork: {e}")

        return None

    def _extract_mp3_artwork_all(self, data):
        """Extract every APIC/PIC frame from an MP3's ID3 tag, keyed by raw picture type (first wins)."""
        pictures = {}
        try:
            if len(data) < 10 or data[0:3] != b"ID3":
                return pictures

            version_major = data[3]
            size = syncsafe_to_int(data[6:10])
            end_pos = min(10 + size, len(data))

            for frame_id, frame_start, frame_size in self._iter_id3_frames(data, version_major, end_pos):
                if frame_id in ["APIC", "PIC"]:
                    parsed_picture = self._parse_id3_apic_frame(data[frame_start : frame_start + frame_size], version_major)
                    if parsed_picture:
                        picture_type = parsed_picture["picture_type"]
                        if picture_type in pictures:
                            logger.warning(f"Duplicate ID3 picture type {picture_type} found; keeping first occurrence")
                        else:
                            pictures[picture_type] = parsed_picture

        except (IndexError, struct.error) as e:
            logger.warning(f"Error extracting MP3 artwork: {e}")

        return pictures

    def _flac_metadata_start(self, data):
        """Return the offset just past the "fLaC" marker (skipping a leading ID3v2 tag), or None."""
        if data[0:4] == b"fLaC":
            return 4
        if data[0:3] == b"ID3" and len(data) >= 10:
            id3_end = 10 + syncsafe_to_int(data[6:10])
            if data[id3_end : id3_end + 4] == b"fLaC":
                return id3_end + 4
        return None

    def _extract_flac_artwork(self, data):
        """Extract artwork from FLAC files (PICTURE block)."""
        try:
            pos = self._flac_metadata_start(data)
            if pos is None:
                return None
            while pos < len(data) - 4:
                # Read block header as big-endian
                header = struct.unpack(">I", data[pos : pos + 4])[0]
                pos += 4

                is_last = (header >> 31) & 1
                block_type = (header >> 24) & 0x7F
                block_size = header & 0xFFFFFF  # 24-bit size

                # A zero-size block is legal (empty SEEKTABLE); only an overrun means truncation.
                if pos + block_size > len(data):
                    break

                if block_type == 6 and block_size > 0:  # PICTURE block
                    picture_data = data[pos : pos + block_size]
                    parsed_picture = self._parse_flac_picture_block(picture_data)
                    if parsed_picture:
                        return parsed_picture

                if is_last:
                    break

                pos += block_size

        except (IndexError, struct.error) as e:
            logger.warning(f"Error extracting FLAC artwork: {e}")

        return None

    def _extract_flac_artwork_all(self, data):
        """Extract every PICTURE block from a FLAC file, keyed by raw picture type."""
        pictures = {}
        try:
            pos = self._flac_metadata_start(data)
            if pos is None:
                return pictures
            while pos < len(data) - 4:
                header = struct.unpack(">I", data[pos : pos + 4])[0]
                pos += 4

                is_last = (header >> 31) & 1
                block_type = (header >> 24) & 0x7F
                block_size = header & 0xFFFFFF  # 24-bit size

                # A zero-size block is legal (empty SEEKTABLE); only an overrun means truncation.
                if pos + block_size > len(data):
                    break

                if block_type == 6 and block_size > 0:  # PICTURE block
                    picture_data = data[pos : pos + block_size]
                    parsed_picture = self._parse_flac_picture_block(picture_data)
                    if parsed_picture:
                        picture_type = parsed_picture["picture_type"]
                        if picture_type in pictures:
                            logger.warning(f"Duplicate FLAC picture type {picture_type} found; keeping first occurrence")
                        else:
                            pictures[picture_type] = parsed_picture

                if is_last:
                    break

                pos += block_size

        except (IndexError, struct.error) as e:
            logger.warning(f"Error extracting FLAC artwork: {e}")

        return pictures

    # Growth step for the incremental FLAC metadata read; most covers fit in the first chunk.
    _FLAC_READ_CHUNK = 256 * 1024

    def extract_artwork_by_role(self, file_path, file_ext):
        """Return {role: picture} for the roles found in a FLAC or MP3 file ({} for other formats)."""
        # Reads only the tag region: the library-wide consistency scan calls this once per track.
        ext = file_ext.lower()
        if ext not in self.SUPPORTED_EXTENSIONS:
            return {}

        try:
            data = self._read_flac_metadata_prefix(file_path) if ext == ".flac" else self._read_mp3_id3_prefix(file_path)
        except OSError as e:
            logger.warning(f"Error reading {file_path} for role-based artwork: {e}")
            return {}

        all_pictures = self._extract_flac_artwork_all(data) if ext == ".flac" else self._extract_mp3_artwork_all(data)

        return self._pictures_to_roles(all_pictures, file_path)

    def _read_mp3_id3_prefix(self, file_path):
        """Read only the ID3v2 tag (header plus its declared size) of an MP3."""
        with Path(file_path).open("rb") as f:
            header = f.read(10)
            if len(header) < 10 or header[0:3] != b"ID3":
                return header
            size = syncsafe_to_int(header[6:10])
            return header + f.read(size)

    def _read_flac_metadata_prefix(self, file_path):
        """Read only the FLAC metadata-block region, growing the read until the last block is buffered."""
        with Path(file_path).open("rb") as f:
            buf = f.read(self._FLAC_READ_CHUNK)
            while True:
                pos = self._flac_metadata_start(buf)
                if pos is None:
                    more = f.read(self._FLAC_READ_CHUNK)
                    if not more:
                        return buf  # no "fLaC" marker found even at EOF
                    buf += more
                    continue

                end = self._flac_metadata_end(buf, pos)
                if end is not None:
                    return buf[:end]

                more = f.read(self._FLAC_READ_CHUNK)
                if not more:
                    return buf  # truncated/malformed; hand back what we have
                buf += more

    def _flac_metadata_end(self, data, pos):
        """Return the offset just past the last metadata block, or None if data is too short yet."""
        while True:
            if pos + 4 > len(data):
                return None
            header = struct.unpack(">I", data[pos : pos + 4])[0]
            is_last = (header >> 31) & 1
            block_size = header & 0xFFFFFF
            block_end = pos + 4 + block_size
            if block_end > len(data):
                return None
            if is_last:
                return block_end
            pos = block_end

    def _pictures_to_roles(self, all_pictures, file_path):
        """Map {picture_type: picture} to {role: picture}; a single untyped picture becomes the front cover."""
        by_role = {}
        leftovers = {}
        for picture_type, picture in all_pictures.items():
            role = self.PICTURE_TYPE_ROLES.get(picture_type)
            if role:
                by_role[role] = picture
            else:
                leftovers[picture_type] = picture

        if "front" not in by_role and len(leftovers) == 1:
            fallback_type, fallback_picture = next(iter(leftovers.items()))
            logger.debug(f"No typed front cover in {file_path}; treating untyped picture (type {fallback_type}) as front cover")
            by_role["front"] = fallback_picture
            del leftovers[fallback_type]

        for leftover_type in leftovers:
            logger.debug(f"Unmapped picture type {leftover_type} in {file_path} left unassigned")

        return by_role

    def _extract_alac_artwork(self, data):
        """Extract artwork from ALAC/M4A files: moov/udta/meta/ilst/covr."""
        try:
            end = len(data)
            moov = find_atom(data, b"moov", 0, end)
            if not moov:
                return None
            udta = find_atom(data, b"udta", *moov)
            if not udta:
                return None
            meta = find_atom(data, b"meta", *udta)
            if not meta:
                return None
            meta_start, meta_end = meta

            # 'meta' is a full box: 1-byte version + 3-byte flags precede
            # its children.
            ilst = find_atom(data, b"ilst", meta_start + 4, meta_end)
            if not ilst:
                return None

            covr = find_atom(data, b"covr", *ilst)
            if not covr:
                return None

            data_atom = find_atom(data, b"data", *covr)
            if not data_atom:
                return None
            d_start, d_end = data_atom
            if d_end - d_start < 8:
                return None

            # data atom payload: type indicator(4) + locale(4), then the image bytes.
            image_bytes = data[d_start + 8 : d_end]
            if image_bytes.startswith(b"\xff\xd8"):
                return self._process_image_data(image_bytes, "JPEG")
            if image_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
                return self._process_image_data(image_bytes, "PNG")

        except (IndexError, struct.error) as e:
            logger.warning(f"Error extracting ALAC artwork: {e}")

        return None

    def _parse_id3_apic_frame(self, frame_data, version_major):
        """Parse ID3v2 APIC (v2.3/2.4) or PIC (v2.2) frame."""
        try:
            if len(frame_data) < 2:
                return None

            encoding = frame_data[0]
            current_pos = 1

            if version_major == 2:
                # v2.2 PIC: fixed 3-byte image format code (e.g. "JPG"), not
                # a null-terminated MIME string.
                if current_pos + 3 > len(frame_data):
                    return None
                current_pos += 3
            else:
                # v2.3/2.4 APIC: null-terminated MIME type string.
                while current_pos < len(frame_data) and frame_data[current_pos] != 0:
                    current_pos += 1
                current_pos += 1

            # Picture type (1 byte)
            if current_pos >= len(frame_data):
                return None
            picture_type = frame_data[current_pos]
            current_pos += 1

            # The description terminator is 2 bytes for UTF-16 encodings.
            _description, image_data = split_id3_string(frame_data[current_pos:], encoding)

            if image_data:
                format_type = determine_image_format(image_data, "")
                processed_image = self._process_image_data(image_data, format_type)
                if processed_image:
                    processed_image["picture_type"] = picture_type
                    return processed_image

        except (IndexError, struct.error) as e:
            logger.warning(f"Error parsing ID3 APIC frame: {e}")

        return None

    def _parse_flac_picture_block(self, data):
        """Parse a FLAC METADATA_BLOCK_PICTURE payload."""
        try:
            pos = 0

            # Picture type (32 bits)
            if pos + 4 > len(data):
                return None
            picture_type = struct.unpack(">I", data[pos : pos + 4])[0]
            pos += 4

            # MIME type string
            if pos + 4 > len(data):
                return None
            mime_len = struct.unpack(">I", data[pos : pos + 4])[0]
            pos += 4

            if pos + mime_len > len(data):
                return None
            mime_type = data[pos : pos + mime_len].decode("utf-8", errors="ignore")
            pos += mime_len

            # Description string
            if pos + 4 > len(data):
                return None
            desc_len = struct.unpack(">I", data[pos : pos + 4])[0]
            pos += 4

            if pos + desc_len > len(data):
                return None
            pos += desc_len  # Skip description

            # Width (32 bits) - skip
            if pos + 4 > len(data):
                return None
            pos += 4

            # Height (32 bits) - skip
            if pos + 4 > len(data):
                return None
            pos += 4

            # Color depth (32 bits) - skip
            if pos + 4 > len(data):
                return None
            pos += 4

            # Colors used (32 bits) - skip
            if pos + 4 > len(data):
                return None
            pos += 4

            # Picture data length
            if pos + 4 > len(data):
                return None
            data_len = struct.unpack(">I", data[pos : pos + 4])[0]
            pos += 4

            # Picture data
            if pos + data_len > len(data):
                return None
            picture_data = data[pos : pos + data_len]

            # Validate we have actual image data
            if len(picture_data) < 8:
                return None

            format_type = determine_image_format(picture_data, mime_type)

            # Process the image to validate it and get dimensions
            processed_image = self._process_image_data(picture_data, format_type)
            if processed_image:
                processed_image["picture_type"] = picture_type
                return processed_image

        except (IndexError, struct.error) as e:
            logger.warning(f"Error parsing FLAC picture block: {e}")

        return None

    def _process_image_data(self, image_data, format_type):
        """Validate image bytes with PIL and return {data, format, width, height, size}, or None."""
        try:
            if len(image_data) < 8:
                return None

            image = Image.open(io.BytesIO(image_data))
            image.load()  # raises on a truncated or invalid image

            return {"data": image_data, "format": format_type, "width": image.width, "height": image.height, "size": len(image_data)}
        except (OSError, Image.DecompressionBombError) as e:
            logger.warning(f"Error processing image data: {e}")
            return None
