import os
from datetime import datetime

from src.metadata.metadata_artwork import ArtworkExtractor
from src.metadata.metadata_properties import AudioPropertiesExtractor
from src.metadata.metadata_raw_tags import RawTagExtractor
from src.metadata.metadata_text import TextMetadataExtractor, flatten_text_metadata
from src.core.logger_config import logger


class MetadataExtractor:
    """Reads one audio file and returns a single flat metadata dict, by
    reading the file once and handing that buffer to three specialized,
    per-format extractors:

    - RawTagExtractor:          on-disk tag key/value pairs (ID3 frames,
                                 Vorbis comments, MP4 atoms, RIFF INFO
                                 chunks — format-specific, meaning-agnostic)
    - TextMetadataExtractor:    maps those raw keys to named Track/Album/
                                 Artist/etc. fields
    - AudioPropertiesExtractor: duration/bitrate/sample-rate/channels
    - ArtworkExtractor:         embedded cover art

    This class only orchestrates; it has no format-specific parsing of its
    own.
    """

    def __init__(self):
        self.raw_tag_extractor = RawTagExtractor()
        self.artwork_extractor = ArtworkExtractor()
        self.audio_properties_extractor = AudioPropertiesExtractor()

    def extract_metadata(self, file_path):
        """
        Extract metadata from an audio file.

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
            logger.debug(f"Processing file: {file_path}")

            # Read the file once; every extractor below works off this same
            # buffer instead of each re-opening and re-reading the file.
            with open(file_path, "rb") as f:
                data = f.read()

            metadata = {
                "track_file_path": file_path,
                "file_size": len(data),
                "file_extension": file_ext.lstrip("."),
                "date_added": datetime.now(),
            }

            raw_tags = self.raw_tag_extractor.extract_raw_tags(data, file_ext)

            text_extractor = TextMetadataExtractor(
                file_path, file_ext.lstrip("."), raw_tags
            )
            text_metadata = text_extractor.extract_metadata()
            metadata.update(flatten_text_metadata(text_metadata))

            artwork = self.artwork_extractor.extract_artwork(data, file_ext)
            if artwork:
                metadata["album_art_data"] = artwork

            audio_properties = self.audio_properties_extractor.extract_audio_properties(
                data, file_ext
            )
            metadata.update(audio_properties)

            logger.debug(f"Successfully extracted metadata from {file_path}")
            logger.debug(
                f"Metadata keys: {list(self._safe_for_logging(metadata).keys())}"
            )

            return metadata

        except Exception as e:
            # Intentional broad boundary catch: this runs once per file inside
            # bulk library-import/rescan loops (see library_import.py,
            # library_rescan_repair.py) and delegates to several independently
            # broad sub-extractors (raw tags, text metadata, audio properties,
            # artwork) — one malformed file must not abort the whole batch.
            logger.exception(
                f"Critical error extracting metadata from {file_path}: {str(e)[:500]}"
            )
            return self._get_basic_file_info(file_path)

    def _get_basic_file_info(self, file_path):
        """Fallback metadata (file system properties only) used when full
        extraction fails."""
        stat = os.stat(file_path)
        file_extension = os.path.splitext(file_path)[1].lower().lstrip(".")

        return {
            "track_file_path": file_path,
            "file_size": stat.st_size,
            "file_extension": file_extension,
            "date_added": datetime.now(),
        }

    def _safe_for_logging(self, metadata):
        """Replace binary values with a size placeholder so debug logs
        don't dump raw image bytes."""
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
