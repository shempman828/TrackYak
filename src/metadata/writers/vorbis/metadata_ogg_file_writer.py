"""Writes a track's tags into an Ogg Vorbis file's comment header packet."""

from pathlib import Path
import struct

from src.foundation.logger_config import logger
from src.metadata.metadata_ogg_pages import replace_comment_packet
from src.metadata.readers.metadata_raw_tags import RawTagExtractor
from src.metadata.writers.metadata_writer_backup import atomic_write
from src.metadata.writers.metadata_writer_merge import merge_vorbis_comments
from src.metadata.writers.metadata_writer_types import WriteMode
from src.metadata.writers.vorbis.metadata_writer_vorbis import VorbisCommentWriter


class OggFileWriter:
    """Reads/writes the Vorbis comment header packet of an Ogg Vorbis file, keeping tags the app does not manage."""

    def __init__(self):
        """Create the comment writer and the raw tag reader."""
        self.vorbis_writer = VorbisCommentWriter()
        self.raw_tag_extractor = RawTagExtractor()

    def write_tags(self, file_path: str, new_comments: dict, mode: WriteMode) -> bool:
        """Replace the comment packet with new_comments merged into the existing ones per mode."""
        # No backup here: MetadataWriter wraps the FLAC and OGG tag writes in one backup/restore.
        try:
            file_data = Path(file_path).read_bytes()

            existing_comments = self.raw_tag_extractor.extract_raw_tags(file_data, ".ogg")
            merged = merge_vorbis_comments(existing_comments, new_comments, mode)
            comment_block = self.vorbis_writer.build_vorbis_comments(merged)

            # Unlike FLAC's raw block, the Ogg packet needs the "\x03vorbis" prefix and a 0x01 framing bit.
            new_comment_packet = b"\x03vorbis" + comment_block + b"\x01"
            new_file_data = replace_comment_packet(file_data, new_comment_packet)

            if new_file_data is None:
                logger.debug(f"Could not safely rewrite OGG comment header for {file_path} (not Ogg Vorbis, or non-standard page layout)")
                return False

            atomic_write(file_path, new_file_data)

            return True

        except (OSError, AttributeError, struct.error, ValueError) as e:
            logger.debug(f"Error writing OGG metadata: {e}")
            return False
