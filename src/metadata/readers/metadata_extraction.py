"""Reads one audio file and returns a single flat metadata dict."""

from datetime import UTC, datetime
from pathlib import Path

from src.foundation.logger_config import logger
from src.metadata.readers.metadata_artwork import ArtworkExtractor
from src.metadata.readers.metadata_properties import AudioPropertiesExtractor
from src.metadata.readers.metadata_raw_tags import RawTagExtractor
from src.metadata.readers.metadata_text import TextMetadataExtractor, flatten_text_metadata


class MetadataExtractor:
    """Reads a file once and combines the raw-tag, text, artwork and audio-property extractors."""

    def __init__(self):
        """Create the per-concern extractors."""
        self.raw_tag_extractor = RawTagExtractor()
        self.artwork_extractor = ArtworkExtractor()
        self.audio_properties_extractor = AudioPropertiesExtractor()

    def extract_metadata(self, file_path):
        """Return the complete metadata dict for an audio file, or {} if it does not exist."""
        if not Path(file_path).exists():
            logger.error(f"File not found: {file_path}")
            return {}

        file_ext = Path(file_path).suffix.lower()

        try:
            logger.debug(f"Processing file: {file_path}")

            # One read; every extractor works off this buffer.
            data = Path(file_path).read_bytes()

            metadata = {"track_file_path": file_path, "file_size": len(data), "file_extension": file_ext.lstrip("."), "date_added": datetime.now(UTC)}

            raw_tags = self.raw_tag_extractor.extract_raw_tags(data, file_ext)

            text_extractor = TextMetadataExtractor(file_path, file_ext.lstrip("."), raw_tags)
            text_metadata = text_extractor.extract_metadata()
            metadata.update(flatten_text_metadata(text_metadata))

            artwork = self.artwork_extractor.extract_artwork(data, file_ext)
            if artwork:
                metadata["album_art_data"] = artwork

            audio_properties = self.audio_properties_extractor.extract_audio_properties(data, file_ext)
            metadata.update(audio_properties)

            logger.debug(f"Successfully extracted metadata from {file_path}")
            logger.debug(f"Metadata keys: {list(self._safe_for_logging(metadata).keys())}")

            return metadata

        except Exception as e:
            # Intentional boundary catch: one unreadable file must not abort a library import.
            logger.exception(f"Critical error extracting metadata from {file_path}: {str(e)[:500]}")
            return self._get_basic_file_info(file_path)

    def _get_basic_file_info(self, file_path):
        """Return file-system-only metadata, used when full extraction fails."""
        try:
            file_size = Path(file_path).stat().st_size
        except OSError:
            file_size = None  # the file vanished or became unreadable mid-import

        return {"track_file_path": file_path, "file_size": file_size, "file_extension": Path(file_path).suffix.lower().lstrip("."), "date_added": datetime.now(UTC)}

    def _safe_for_logging(self, metadata):
        """Replace binary values with a size placeholder so debug logs do not dump image bytes."""
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
