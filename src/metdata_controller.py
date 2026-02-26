import os
import struct
from datetime import datetime
from typing import Any, Dict, List

from src.metadata_artwork import ArtworkExtractor
from src.metadata_properties import AudioPropertiesExtractor
from src.metadata_text import TextMetadataExtractor
from src.logger_config import logger


class ExtractMetadata:
    """Main metadata extraction class using specialized extractors."""

    def __init__(self):
        self.artwork_extractor = ArtworkExtractor()
        self.audio_properties_extractor = AudioPropertiesExtractor()

    def extract_metadata(self, file_path):
        """
        Extract metadata from audio file using specialized extractors.

        Args:
            file_path: Path to the audio file

        Returns:
            Dictionary with complete metadata
        """
        if not os.path.exists(file_path):
            logger.error(f"File not found: {file_path}")
            return {}

        file_ext = os.path.splitext(file_path)[1].lower()

        try:
            # Get basic file info first
            metadata = self._get_basic_file_info(file_path)

            logger.debug(f"Processing file: {file_path}")

            # Extract raw tags for text metadata
            raw_tags = self._extract_raw_tags(file_path, file_ext)

            # Use specialized extractors for different concerns
            file_extension = file_ext.lstrip(".")
            text_extractor = TextMetadataExtractor(file_path, file_extension, raw_tags)
            text_metadata = text_extractor.extract_metadata()

            # Convert the grouped text metadata to flat structure for compatibility
            flattened_text_metadata = self._flatten_text_metadata(text_metadata)
            metadata.update(flattened_text_metadata)

            artwork = self.artwork_extractor.extract_artwork(file_path, file_ext)
            if artwork:
                metadata["album_art_data"] = artwork

            audio_properties = self.audio_properties_extractor.extract_audio_properties(
                file_path, file_ext
            )
            metadata.update(audio_properties)

            # Log successful extraction
            safe_metadata = self._create_safe_logging_metadata(metadata)
            logger.debug(f"Successfully extracted metadata from {file_path}")
            logger.debug(f"Metadata keys: {list(safe_metadata.keys())}")

            return metadata

        except Exception as e:
            logger.error(
                f"Critical error extracting metadata from {file_path}: {str(e)[:500]}"
            )
            return self._get_basic_file_info(file_path)

    def _flatten_text_metadata(
        self, text_metadata: Dict[str, List[Dict[str, Any]]]
    ) -> Dict[str, Any]:
        """
        Convert the grouped text metadata structure to a flat dictionary
        for compatibility with existing code.
        """
        flattened = {}

        for entity_type, fields_list in text_metadata.items():
            for field_data in fields_list:
                field_name = field_data["field"]
                entity = field_data["entity"]
                value = field_data["value"]

                # Handle different entity types with proper list management
                if entity == "Track":
                    # For track fields, use lists for multi-value capable fields
                    multi_value_track_fields = {
                        "comment",
                        "lyrics",
                    }  # Add others as needed
                    if field_name in multi_value_track_fields:
                        if field_name not in flattened:
                            flattened[field_name] = []
                        if not isinstance(flattened[field_name], list):
                            flattened[field_name] = [flattened[field_name]]
                        flattened[field_name].append(value)
                    else:
                        flattened[field_name] = (
                            value  # Single value for most track fields
                        )

                elif entity == "Album":
                    # For album fields, use lists for multi-value capable fields
                    multi_value_album_fields = {
                        "album_description"
                    }  # Add others as needed
                    album_field_name = f"album_{field_name}"
                    if field_name in multi_value_album_fields:
                        if album_field_name not in flattened:
                            flattened[album_field_name] = []
                        if not isinstance(flattened[album_field_name], list):
                            flattened[album_field_name] = [flattened[album_field_name]]
                        flattened[album_field_name].append(value)
                    else:
                        flattened[album_field_name] = (
                            value  # Single value for most album fields
                        )

                elif entity == "Artist":
                    # For artists, handle roles properly - ALREADY CORRECT!
                    role = field_data.get("role", "primary").lower().replace(" ", "_")
                    role_key = f"artist_{role}"

                    if role_key not in flattened:
                        flattened[role_key] = []

                    # Ensure we're working with a list
                    if not isinstance(flattened[role_key], list):
                        flattened[role_key] = [flattened[role_key]]

                    flattened[role_key].append(value)

                    # Also populate the main artist_name for primary artists
                    if role == "primary_artist":
                        if "artist_name" not in flattened:
                            flattened["artist_name"] = []
                        if not isinstance(flattened["artist_name"], list):
                            flattened["artist_name"] = [flattened["artist_name"]]
                        flattened["artist_name"].append(value)

                elif entity == "Genre":
                    if "genre_name" not in flattened:
                        flattened["genre_name"] = []
                    if not isinstance(flattened["genre_name"], list):
                        flattened["genre_name"] = [flattened["genre_name"]]
                    flattened["genre_name"].append(value)

                elif entity == "Publisher":
                    # Handle multiple publishers
                    if "publisher_name" not in flattened:
                        flattened["publisher_name"] = []
                    if not isinstance(flattened["publisher_name"], list):
                        flattened["publisher_name"] = [flattened["publisher_name"]]
                    flattened["publisher_name"].append(value)

                elif entity == "Disc":
                    flattened[field_name] = value  # Usually single values

                elif entity == "Mood":
                    if "mood_name" not in flattened:
                        flattened["mood_name"] = []
                    if not isinstance(flattened["mood_name"], list):
                        flattened["mood_name"] = [flattened["mood_name"]]
                    flattened["mood_name"].append(value)

                elif entity == "Place":
                    if "place_name" not in flattened:
                        flattened["place_name"] = []
                    if not isinstance(flattened["place_name"], list):
                        flattened["place_name"] = [flattened["place_name"]]
                    flattened["place_name"].append(value)

                else:
                    # For any other entity, use the field name directly
                    flattened[field_name] = value

        # Ensure critical fields exist and are lists where expected
        list_fields = [
            "artist_name",
            "album_artist_name",
            "genre_name",
            "mood_name",
            "place_name",
            "publisher_name",  # ADDED
        ]

        # Also add any artist role fields that should be lists
        artist_role_fields = [
            key for key in flattened.keys() if key.startswith("artist_")
        ]
        list_fields.extend(artist_role_fields)

        for field in list_fields:
            if field in flattened and not isinstance(flattened[field], list):
                flattened[field] = [flattened[field]]
            elif field not in flattened:
                flattened[field] = []

        return flattened

    def _get_basic_file_info(self, file_path):
        """Extract basic file system properties."""
        stat = os.stat(file_path)
        file_size = stat.st_size
        file_extension = os.path.splitext(file_path)[1].lower().lstrip(".")

        return {
            "track_file_path": file_path,
            "file_size": file_size,
            "file_extension": file_extension,
            "date_added": datetime.now(),
        }

    def _extract_raw_tags(self, file_path, file_ext):
        """
        Extract raw tags from file without mapping.
        This replaces the old format-specific extraction methods.
        """
        raw_tags = {}

        try:
            with open(file_path, "rb") as f:
                data = f.read()

            if file_ext == ".mp3":
                raw_tags.update(self._extract_raw_id3_tags(data))
            elif file_ext in [".flac", ".fla"]:
                raw_tags.update(self._extract_raw_flac_tags(data))
            elif file_ext in [".m4a", ".mp4"]:
                raw_tags.update(self._extract_raw_alac_tags(data))
            elif file_ext == ".wav":
                raw_tags.update(self._extract_raw_wav_tags(data))
            elif file_ext == ".aiff":
                raw_tags.update(self._extract_raw_aiff_tags(data))
            else:
                logger.warning(
                    f"Unsupported file format for raw tag extraction: {file_ext}"
                )

        except Exception as e:
            logger.warning(f"Error extracting raw tags from {file_path}: {e}")

        return raw_tags

    def _extract_raw_id3_tags(self, data):
        """Extract raw ID3 tags without mapping."""
        raw_tags = {}

        try:
            # ID3v2 extraction
            if len(data) >= 10 and data[0:3] == b"ID3":
                version_major = data[3]
                size = self._syncsafe_to_int(data[6:10])
                frame_data = data[10 : 10 + size]

                if version_major == 2:
                    raw_tags.update(self._parse_raw_id3v2_2_frames(frame_data))
                elif version_major in [3, 4]:
                    raw_tags.update(
                        self._parse_raw_id3v2_3_4_frames(frame_data, version_major)
                    )

            # ID3v1 extraction (fallback)
            if len(data) >= 128 and data[-128:-125] == b"TAG":
                raw_tags.update(self._parse_raw_id3v1_tags(data[-128:]))

        except Exception as e:
            logger.warning(f"Error extracting raw ID3 tags: {e}")

        return raw_tags

    def _extract_raw_flac_tags(self, data):
        """Extract raw FLAC tags without mapping."""
        raw_tags = {}

        try:
            if data[0:4] == b"fLaC":
                pos = 4
                while pos < len(data) - 4:
                    header = struct.unpack(">I", data[pos : pos + 4])[0]
                    pos += 4

                    is_last = (header >> 31) & 1
                    block_type = (header >> 24) & 0x7F
                    block_size = header & 0xFFFFFF

                    if block_type == 4:  # VORBIS_COMMENT
                        raw_tags.update(
                            self._parse_raw_vorbis_comments(
                                data[pos : pos + block_size]
                            )
                        )

                    if is_last:
                        break
                    pos += block_size

            # ADD DEBUG LOGGING
            logger.debug(f"Raw FLAC tags extracted: {raw_tags}")

        except Exception as e:
            logger.warning(f"Error extracting raw FLAC tags: {e}")

        return raw_tags

    def _extract_raw_alac_tags(self, data):
        """Extract raw ALAC/M4A tags without mapping."""
        raw_tags = {}

        try:
            # Simplified MP4 container parsing for metadata
            pos = 0
            while pos < len(data) - 8:
                box_size = struct.unpack(">I", data[pos : pos + 4])[0]
                box_type = data[pos + 4 : pos + 8]

                if box_size < 8 or pos + box_size > len(data):
                    break

                if box_type == b"moov":
                    raw_tags.update(
                        self._parse_raw_moov_box(data[pos + 8 : pos + box_size])
                    )

                pos += box_size

        except Exception as e:
            logger.warning(f"Error extracting raw ALAC tags: {e}")

        return raw_tags

    def _extract_raw_wav_tags(self, data):
        """Extract raw WAV tags without mapping."""
        raw_tags = {}

        try:
            if data[0:4] == b"RIFF" and data[8:12] == b"WAVE":
                pos = 12
                while pos < len(data) - 8:
                    chunk_id = data[pos : pos + 4]
                    chunk_size = struct.unpack("<I", data[pos + 4 : pos + 8])[0]

                    if chunk_id == b"LIST" and pos + 12 <= len(data):
                        list_type = data[pos + 8 : pos + 12]
                        if list_type == b"INFO":
                            raw_tags.update(
                                self._parse_raw_info_chunk(
                                    data[pos + 12 : pos + 8 + chunk_size]
                                )
                            )

                    pos += 8 + chunk_size

        except Exception as e:
            logger.warning(f"Error extracting raw WAV tags: {e}")

        return raw_tags

    def _extract_raw_aiff_tags(self, data):
        """Extract raw AIFF tags without mapping."""
        raw_tags = {}

        try:
            if data[0:4] == b"FORM" and data[8:12] in [b"AIFF", b"AIFC"]:
                pos = 12
                while pos < len(data) - 8:
                    chunk_id = data[pos : pos + 4]
                    chunk_size = struct.unpack(">I", data[pos + 4 : pos + 8])[0]

                    # Extract basic AIFF chunks as raw tags
                    if chunk_id in [b"NAME", b"AUTH", b"(c) ", b"ANNO"]:
                        tag_name = chunk_id.decode("ascii", errors="ignore").strip()
                        tag_value = (
                            data[pos + 8 : pos + 8 + chunk_size]
                            .decode("ascii", errors="ignore")
                            .strip("\x00")
                        )
                        raw_tags[tag_name] = tag_value

                    pos += 8 + chunk_size

        except Exception as e:
            logger.warning(f"Error extracting raw AIFF tags: {e}")

        return raw_tags

    def _parse_raw_id3v2_2_frames(self, frame_data):
        """Parse raw ID3v2.2 frames."""
        raw_tags = {}
        pos = 0

        while pos < len(frame_data) - 6:
            frame_id = frame_data[pos : pos + 3].decode("ascii", errors="ignore")
            frame_size = struct.unpack(">I", b"\x00" + frame_data[pos + 3 : pos + 6])[0]

            if frame_size == 0:
                break

            frame_content = frame_data[pos + 6 : pos + 6 + frame_size]
            raw_tags[frame_id] = self._decode_id3_text(frame_content)

            pos += 6 + frame_size

        return raw_tags

    def _parse_raw_id3v2_3_4_frames(self, frame_data, version):
        """Parse raw ID3v2.3/2.4 frames."""
        raw_tags = {}
        pos = 0

        while pos < len(frame_data) - 10:
            frame_id = frame_data[pos : pos + 4].decode("ascii", errors="ignore")

            if b"\x00" in frame_id.encode("ascii"):
                break

            if version == 3:
                frame_size = struct.unpack(">I", frame_data[pos + 4 : pos + 8])[0]
            else:
                frame_size = self._syncsafe_to_int(frame_data[pos + 4 : pos + 8])

            if frame_size == 0:
                break

            frame_content = frame_data[pos + 10 : pos + 10 + frame_size]

            # ── NEW: handle TXXX frames specially ──────────────────────
            if frame_id == "TXXX":
                # TXXX structure: encoding(1) + description(variable) + \x00[\x00] + value
                # We need to extract the description to build the storage key.
                try:
                    encoding = frame_content[0] if frame_content else 0
                    rest = frame_content[1:]  # everything after the encoding byte

                    if encoding in (0x01, 0x02):
                        # UTF-16: null terminator is \x00\x00
                        sep = rest.find(b"\x00\x00")
                        if sep == -1:
                            sep = len(rest)
                        raw_desc = rest[:sep]
                        raw_val = rest[sep + 2 :]  # skip the 2-byte null terminator
                        description = raw_desc.decode(
                            "utf-16be", errors="ignore"
                        ).strip("\x00")
                        value = raw_val.decode("utf-16be", errors="ignore").strip(
                            "\x00"
                        )
                    else:
                        # ISO-8859-1 or UTF-8: null terminator is \x00
                        sep = rest.find(b"\x00")
                        if sep == -1:
                            sep = len(rest)
                        description = (
                            rest[:sep].decode("latin-1", errors="ignore").strip()
                        )
                        value = (
                            rest[sep + 1 :]
                            .decode(
                                "utf-8" if encoding == 0x03 else "latin-1",
                                errors="ignore",
                            )
                            .strip("\x00")
                        )

                    # Store under "TXXX:description" so TXXX:PLAYLIST is preserved
                    storage_key = f"TXXX:{description}" if description else "TXXX"
                    if storage_key not in raw_tags:
                        raw_tags[storage_key] = []
                    raw_tags[storage_key].append(value)

                except Exception as e:
                    logger.debug(f"Error parsing TXXX frame: {e}")
            # ── END new TXXX handling ───────────────────────────────────
            else:
                value = self._decode_id3_text(frame_content)

                if frame_id not in raw_tags:
                    raw_tags[frame_id] = []

                raw_tags[frame_id].append(value)

            pos += 10 + frame_size

        return raw_tags

    def _parse_raw_id3v1_tags(self, tag_data):
        """Parse raw ID3v1 tags."""
        return {
            "TIT2": self._strip_null(tag_data[3:33].decode("latin-1", errors="ignore")),
            "TPE1": self._strip_null(
                tag_data[33:63].decode("latin-1", errors="ignore")
            ),
            "TALB": self._strip_null(
                tag_data[63:93].decode("latin-1", errors="ignore")
            ),
            "TYER": self._strip_null(
                tag_data[93:97].decode("latin-1", errors="ignore")
            ),
            "COMM": self._strip_null(
                tag_data[97:127].decode("latin-1", errors="ignore")
            ),
        }

    def _parse_raw_vorbis_comments(self, data):
        """Parse raw Vorbis comments."""
        raw_tags = {}
        pos = 0

        try:
            # Skip vendor string
            vendor_len = struct.unpack("<I", data[pos : pos + 4])[0]
            pos += 4 + vendor_len

            # Comment count
            comment_count = struct.unpack("<I", data[pos : pos + 4])[0]
            pos += 4

            for _ in range(comment_count):
                comment_len = struct.unpack("<I", data[pos : pos + 4])[0]
                pos += 4

                comment = data[pos : pos + comment_len].decode("utf-8", errors="ignore")
                pos += comment_len

                if "=" in comment:
                    key, value = comment.split("=", 1)
                    key_upper = key.upper()

                    # FIX: Collect all values for the same tag name
                    if key_upper not in raw_tags:
                        raw_tags[key_upper] = []

                    raw_tags[key_upper].append(value)

        except Exception as e:
            logger.warning(f"Error parsing raw Vorbis comments: {e}")

        return raw_tags

    def _parse_raw_moov_box(self, data):
        """Parse raw moov box for ALAC tags."""
        # Simplified implementation - would contain your existing ALAC parsing logic
        # but returning raw tag names/values instead of mapped fields
        return {}

    def _parse_raw_info_chunk(self, data):
        """Parse raw WAV INFO chunk."""
        raw_tags = {}
        pos = 0

        while pos < len(data) - 8:
            chunk_id = data[pos : pos + 4].decode("ascii", errors="ignore")
            chunk_size = struct.unpack("<I", data[pos + 4 : pos + 8])[0]

            if chunk_size > 0 and pos + 8 + chunk_size <= len(data):
                chunk_data = data[pos + 8 : pos + 8 + chunk_size]
                value = chunk_data.decode("utf-8", errors="ignore").strip("\x00")

                # FIX: Collect all values for the same chunk ID
                if chunk_id not in raw_tags:
                    raw_tags[chunk_id] = []

                raw_tags[chunk_id].append(value)

            pos += 8 + chunk_size

        return raw_tags

    def _create_safe_logging_metadata(self, metadata):
        """Create a safe version of metadata for logging (no binary data)."""
        safe_metadata = {}
        for key, value in metadata.items():
            if key == "album_art_data":
                art_data = value.get("data", []) if isinstance(value, dict) else []
                safe_metadata[key] = f"<binary_data:{len(art_data)}_bytes>"
            elif isinstance(value, (bytes, bytearray)):
                safe_metadata[key] = f"<binary_data:{len(value)}_bytes>"
            else:
                safe_metadata[key] = value
        return safe_metadata

    def _syncsafe_to_int(self, data):
        result = 0
        for byte in data:
            result = (result << 7) | (byte & 0x7F)
        return result

    def _decode_id3_text(self, data):
        if not data:
            return ""
        try:
            encoding = data[0]
            text_data = data[1:]
            if encoding == 0:  # ISO-8859-1
                return text_data.decode("latin-1", errors="ignore").strip("\x00")
            elif encoding == 1:  # UTF-16 with BOM
                return text_data.decode("utf-16", errors="ignore").strip("\x00")
            elif encoding == 3:  # UTF-8
                return text_data.decode("utf-8", errors="ignore").strip("\x00")
            else:
                return text_data.decode("latin-1", errors="ignore").strip("\x00")
        except:  # noqa: E722
            return data.decode("latin-1", errors="ignore").strip("\x00")

    def _strip_null(self, text):
        return text.strip("\x00")
