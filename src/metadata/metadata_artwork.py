import io
import struct

from PIL import Image

from src.core.logger_config import logger
from src.metadata.metadata_image_utils import determine_image_format


class ArtworkExtractor:
    """Dedicated album art extraction separate from text metadata."""

    # MusicBrainz/ID3-APIC picture-type convention used to assign a role to
    # each embedded picture. Picard uses type 5 ("Leaflet page") for
    # liner/booklet art.
    PICTURE_TYPE_ROLES = {3: "front", 4: "rear", 5: "liner"}

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

    def extract_artwork(self, file_path, file_ext):
        """
        Extract artwork from audio file.

        Args:
            file_path: Path to the audio file
            file_ext: File extension (.mp3, .flac, etc.)

        Returns:
            Dictionary with artwork data or None if no artwork found
        """
        try:
            logger.debug(f"Attempting to extract artwork from {file_path}")

            handler = self.format_handlers.get(file_ext.lower())
            if not handler:
                logger.debug(f"No artwork handler for format: {file_ext}")
                return None

            with open(file_path, "rb") as f:
                data = f.read()

            artwork = handler(data)

            if artwork:
                logger.debug(
                    f"Successfully extracted artwork: {len(artwork.get('data', []))} bytes"
                )
            else:
                logger.debug(f"No artwork found in {file_path}")

            return artwork

        except Exception as e:
            logger.warning(f"Error extracting artwork from {file_path}: {e}")
            return None

    def _extract_mp3_artwork(self, data):
        """Extract artwork from MP3 files (ID3v2 APIC frames)."""
        try:
            if len(data) < 10 or data[0:3] != b"ID3":
                return None

            version_major = data[3]
            size = self._syncsafe_to_int(data[6:10])
            pos = 10
            end_pos = min(pos + size, len(data))

            while pos < end_pos - 10:
                if version_major == 2:  # ID3v2.2
                    frame_id = data[pos : pos + 3].decode("ascii", errors="ignore")
                    frame_size = struct.unpack(">I", b"\x00" + data[pos + 3 : pos + 6])[
                        0
                    ]
                    frame_start = pos + 6
                else:  # ID3v2.3/2.4
                    frame_id = data[pos : pos + 4].decode("ascii", errors="ignore")
                    frame_size = (
                        self._syncsafe_to_int(data[pos + 4 : pos + 8])
                        if version_major == 4
                        else struct.unpack(">I", data[pos + 4 : pos + 8])[0]
                    )
                    frame_start = pos + 10

                if frame_size == 0:
                    break

                if frame_id in ["APIC", "PIC"]:
                    return self._parse_id3_apic_frame(
                        data[frame_start : frame_start + frame_size], version_major
                    )

                pos = frame_start + frame_size

        except Exception as e:
            logger.warning(f"Error extracting MP3 artwork: {e}")

        return None

    def _extract_mp3_artwork_all(self, data):
        """Extract every APIC/PIC frame from an MP3's ID3 tag, keyed by raw picture type."""
        pictures = {}
        try:
            if len(data) < 10 or data[0:3] != b"ID3":
                return pictures

            version_major = data[3]
            size = self._syncsafe_to_int(data[6:10])
            pos = 10
            end_pos = min(pos + size, len(data))

            while pos < end_pos - 10:
                if version_major == 2:  # ID3v2.2
                    frame_id = data[pos : pos + 3].decode("ascii", errors="ignore")
                    frame_size = struct.unpack(">I", b"\x00" + data[pos + 3 : pos + 6])[
                        0
                    ]
                    frame_start = pos + 6
                else:  # ID3v2.3/2.4
                    frame_id = data[pos : pos + 4].decode("ascii", errors="ignore")
                    frame_size = (
                        self._syncsafe_to_int(data[pos + 4 : pos + 8])
                        if version_major == 4
                        else struct.unpack(">I", data[pos + 4 : pos + 8])[0]
                    )
                    frame_start = pos + 10

                if frame_size == 0:
                    break

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

                pos = frame_start + frame_size

        except Exception as e:
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
            id3_end = 10 + self._syncsafe_to_int(data[6:10])
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

        except Exception as e:
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

        except Exception as e:
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
        except Exception as e:
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
        """Extract artwork from ALAC/M4A files (covr atom)."""
        try:
            pos = 0
            while pos < len(data) - 8:
                atom_size = struct.unpack(">I", data[pos : pos + 4])[0]
                atom_type = data[pos + 4 : pos + 8]

                if atom_size < 8 or pos + atom_size > len(data):
                    break

                if atom_type == b"covr":
                    return self._parse_covr_atom(data[pos + 8 : pos + atom_size])
                elif atom_type == b"moov":
                    cover_data = self._find_alac_coverart(
                        data[pos + 8 : pos + atom_size]
                    )
                    if cover_data:
                        return self._process_image_data(cover_data, "JPEG")

                pos += atom_size

        except Exception as e:
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

        except Exception as e:
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

        except Exception as e:
            logger.warning(f"Error parsing FLAC picture block: {e}")

        return None

    def _parse_covr_atom(self, covr_data):
        """Parse ALAC covr atom."""
        try:
            # Look for image magic bytes
            if covr_data.startswith(b"\xff\xd8"):
                jpeg_end = self._find_jpeg_end(covr_data)
                if jpeg_end:
                    return self._process_image_data(covr_data[:jpeg_end], "JPEG")
            elif covr_data.startswith(b"\x89PNG\r\n\x1a\n"):
                png_end = self._find_png_end(covr_data)
                if png_end:
                    return self._process_image_data(covr_data[:png_end], "PNG")

        except Exception as e:
            logger.warning(f"Error parsing covr atom: {e}")

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
        except Exception as e:
            logger.warning(f"Error processing image data: {e}")
            return None

    def _determine_image_format(self, image_data, mime_type):
        """Determine image format from magic bytes or MIME type."""
        return determine_image_format(image_data, mime_type)

    def _find_jpeg_end(self, data):
        """Find JPEG end marker."""
        pos = 0
        while pos < len(data) - 1:
            if data[pos : pos + 2] == b"\xff\xd9":
                return pos + 2
            pos += 1
        return None

    def _find_png_end(self, data):
        """Find PNG IEND chunk."""
        pos = 0
        while pos < len(data) - 8:
            if data[pos : pos + 8] == b"IEND\xae\x42\x60\x82":
                return pos + 8
            pos += 1
        return None

    def _syncsafe_to_int(self, data):
        """Convert syncsafe integer to normal integer."""
        result = 0
        for byte in data:
            result = (result << 7) | (byte & 0x7F)
        return result

    def _find_alac_coverart(self, data):
        """Find cover art in ALAC metadata (helper for complex structures)."""
        # Implementation similar to your existing _find_alac_coverart method
        # but focused solely on artwork extraction
        pass
