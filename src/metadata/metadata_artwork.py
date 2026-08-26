import io
import struct

from PIL import Image

from src.core.logger_config import logger
from src.metadata.metadata_byte_utils import syncsafe_to_int
from src.metadata.metadata_image_utils import ARTWORK_TYPE_TO_ROLE, determine_image_format
from src.metadata.metadata_mp4_atoms import find_atom


class ArtworkExtractor:
    """Dedicated album art extraction separate from text metadata."""

    # MusicBrainz/ID3-APIC picture-type convention used to assign a role to
    # each embedded picture, shared with the writers (see metadata_image_utils).
    PICTURE_TYPE_ROLES = ARTWORK_TYPE_TO_ROLE

    # Formats supported by extract_artwork_by_role / write_artwork_to_file's
    # role-based (front/rear/liner) read+write path.
    SUPPORTED_EXTENSIONS = {".flac", ".mp3"}

    def __init__(self):
        self.format_handlers = {
            ".mp3": self._extract_mp3_artwork,
            ".flac": self._extract_flac_artwork,
            ".m4a": self._extract_alac_artwork,
            ".mp4": self._extract_alac_artwork,
        }

    def extract_artwork(self, data, file_ext):
        """
        Extract artwork from audio file bytes already read by the caller.

        Args:
            data: Full file contents
            file_ext: File extension (.mp3, .flac, etc.)

        Returns:
            Dictionary with artwork data or None if no artwork found
        """
        try:
            handler = self.format_handlers.get(file_ext.lower())
            if not handler:
                logger.debug(f"No artwork handler for format: {file_ext}")
                return None

            artwork = handler(data)

            if artwork:
                logger.debug(
                    f"Successfully extracted artwork: {len(artwork.get('data', []))} bytes"
                )
            else:
                logger.debug("No artwork found")

            return artwork

        except AttributeError as e:
            logger.warning(f"Error extracting artwork: {e}")
            return None

    def _iter_id3_frames(self, data, version_major, end_pos):
        """Yield (frame_id, frame_start, frame_size) for each ID3v2 frame
        header from byte offset 10 (right after the ID3 tag header) up to
        end_pos. Stops as soon as a frame with size 0 is hit or fewer than
        10 bytes remain, mirroring where a real ID3v2 tag ends.
        """
        pos = 10
        while pos < end_pos - 10:
            if version_major == 2:  # ID3v2.2
                frame_id = data[pos : pos + 3].decode("ascii", errors="ignore")
                frame_size = struct.unpack(">I", b"\x00" + data[pos + 3 : pos + 6])[0]
                frame_start = pos + 6
            else:  # ID3v2.3/2.4
                frame_id = data[pos : pos + 4].decode("ascii", errors="ignore")
                frame_size = (
                    syncsafe_to_int(data[pos + 4 : pos + 8])
                    if version_major == 4
                    else struct.unpack(">I", data[pos + 4 : pos + 8])[0]
                )
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

            for frame_id, frame_start, frame_size in self._iter_id3_frames(
                data, version_major, end_pos
            ):
                if frame_id in ["APIC", "PIC"]:
                    return self._parse_id3_apic_frame(
                        data[frame_start : frame_start + frame_size], version_major
                    )

        except (IndexError, struct.error) as e:
            logger.warning(f"Error extracting MP3 artwork: {e}")

        return None

    def _extract_mp3_artwork_all(self, data):
        """Extract every APIC/PIC frame from an MP3's ID3 tag, keyed by raw picture type."""
        pictures = {}
        try:
            if len(data) < 10 or data[0:3] != b"ID3":
                return pictures

            version_major = data[3]
            size = syncsafe_to_int(data[6:10])
            end_pos = min(10 + size, len(data))

            for frame_id, frame_start, frame_size in self._iter_id3_frames(
                data, version_major, end_pos
            ):
                if frame_id in ["APIC", "PIC"]:
                    parsed_picture = self._parse_id3_apic_frame(
                        data[frame_start : frame_start + frame_size], version_major
                    )
                    if parsed_picture:
                        picture_type = parsed_picture["picture_type"]
                        if picture_type in pictures:
                            logger.warning(
                                f"Duplicate ID3 picture type {picture_type} found; "
                                "keeping first occurrence"
                            )
                        else:
                            pictures[picture_type] = parsed_picture

        except (IndexError, struct.error) as e:
            logger.warning(f"Error extracting MP3 artwork: {e}")

        return pictures

    def _flac_metadata_start(self, data):
        """
        Return the byte offset right after the "fLaC" marker, tolerating an
        optional leading ID3v2 tag. Native FLAC doesn't use ID3, but some
        tools prepend one anyway; lenient decoders (ffmpeg, foobar2000, etc.)
        skip over it, so real playable files in the wild have this shape.
        Returns None if no "fLaC" marker can be found either way.
        """
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

                # Safety check: a zero-size block is legitimate (e.g. an
                # empty SEEKTABLE placeholder some encoders write) and must
                # not be treated as corruption - only an actual overrun means
                # the file is malformed/truncated.
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

                # A zero-size block (e.g. an empty SEEKTABLE placeholder) is
                # legitimate and must not abort the scan - only an actual
                # overrun means the file is malformed/truncated.
                if pos + block_size > len(data):
                    break

                if block_type == 6 and block_size > 0:  # PICTURE block
                    picture_data = data[pos : pos + block_size]
                    parsed_picture = self._parse_flac_picture_block(picture_data)
                    if parsed_picture:
                        picture_type = parsed_picture["picture_type"]
                        if picture_type in pictures:
                            logger.warning(
                                f"Duplicate FLAC picture type {picture_type} found; "
                                "keeping first occurrence"
                            )
                        else:
                            pictures[picture_type] = parsed_picture

                if is_last:
                    break

                pos += block_size

        except (IndexError, struct.error) as e:
            logger.warning(f"Error extracting FLAC artwork: {e}")

        return pictures

    def extract_artwork_by_role(self, file_path, file_ext):
        """
        Extract embedded artwork keyed by role ("front"/"rear"/"liner").

        Only FLAC and MP3 are supported today; other formats return an
        empty dict. Returns a dict containing only the roles that were
        found - callers should use .get(role) rather than assuming all
        three keys exist.
        """
        ext = file_ext.lower()
        if ext not in self.SUPPORTED_EXTENSIONS:
            return {}

        try:
            with open(file_path, "rb") as f:
                data = f.read()
        except OSError as e:
            logger.warning(f"Error reading {file_path} for role-based artwork: {e}")
            return {}

        if ext == ".flac":
            all_pictures = self._extract_flac_artwork_all(data)
        else:
            all_pictures = self._extract_mp3_artwork_all(data)

        return self._pictures_to_roles(all_pictures, file_path)

    def _pictures_to_roles(self, all_pictures, file_path):
        """
        Map a {picture_type: picture} dict (as produced by either the FLAC
        or MP3 "extract all pictures" scan) to {role: picture}, applying
        the shared MusicBrainz/ID3-APIC type convention and the
        untyped-single-picture-is-front fallback rule.
        """
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
            logger.debug(
                f"No typed front cover in {file_path}; treating untyped picture "
                f"(type {fallback_type}) as front cover"
            )
            by_role["front"] = fallback_picture
            del leftovers[fallback_type]

        for leftover_type in leftovers:
            logger.debug(
                f"Unmapped picture type {leftover_type} in {file_path} left unassigned"
            )

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

            # data atom payload: type indicator(4) + locale/reserved(4),
            # then the raw image bytes.
            image_bytes = data[d_start + 8 : d_end]
            if image_bytes.startswith(b"\xff\xd8"):
                return self._process_image_data(image_bytes, "JPEG")
            elif image_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
                return self._process_image_data(image_bytes, "PNG")

        except (IndexError, struct.error) as e:
            logger.warning(f"Error extracting ALAC artwork: {e}")

        return None

    def _parse_id3_apic_frame(self, frame_data, version_major):
        """Parse ID3v2 APIC (v2.3/2.4) or PIC (v2.2) frame."""
        try:
            if len(frame_data) < 2:
                return None

            # Skip encoding byte
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

            # Skip description (null-terminated string)
            while current_pos < len(frame_data) and frame_data[current_pos] != 0:
                current_pos += 1
            current_pos += 1

            # Remaining data is the image
            if current_pos < len(frame_data):
                image_data = frame_data[current_pos:]
                format_type = self._determine_image_format(image_data, "")
                processed_image = self._process_image_data(image_data, format_type)
                if processed_image:
                    processed_image["picture_type"] = picture_type
                    return processed_image

        except (IndexError, struct.error) as e:
            logger.warning(f"Error parsing ID3 APIC frame: {e}")

        return None

    def _parse_flac_picture_block(self, data):
        """Parse FLAC PICTURE block according to FLAC specification."""
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

            format_type = self._determine_image_format(picture_data, mime_type)

            # Process the image to validate it and get dimensions
            processed_image = self._process_image_data(picture_data, format_type)
            if processed_image:
                processed_image["picture_type"] = picture_type
                return processed_image

        except (IndexError, struct.error) as e:
            logger.warning(f"Error parsing FLAC picture block: {e}")

        return None

    def _process_image_data(self, image_data, format_type):
        """Process and validate image data."""
        try:
            # First, validate the image data has minimum required bytes
            if len(image_data) < 8:
                return None

            # Try to open with PIL to validate it's a real image
            image = Image.open(io.BytesIO(image_data))

            # Verify the image was loaded correctly by attempting to get its mode
            # This will raise an exception if the image is invalid
            image.load()

            return {
                "data": image_data,
                "format": format_type,
                "width": image.width,
                "height": image.height,
                "size": len(image_data),
            }
        except (OSError, Image.DecompressionBombError) as e:
            logger.warning(f"Error processing image data: {e}")
            return None

    def _determine_image_format(self, image_data, mime_type):
        """Determine image format from magic bytes or MIME type."""
        return determine_image_format(image_data, mime_type)
