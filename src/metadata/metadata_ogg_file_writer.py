"""OGG file writer: combines the low-level page/packet machinery in
metadata_ogg_pages.py with WriteMode-aware comment merging to write a
track's tags into an OGG Vorbis file.
"""

import struct

from src.metadata.metadata_ogg_pages import replace_comment_packet
from src.metadata.metadata_raw_tags import RawTagExtractor
from src.metadata.metadata_writer_backup import atomic_write
from src.metadata.metadata_writer_merge import merge_vorbis_comments
from src.metadata.metadata_writer_types import WriteMode
from src.metadata.metadata_writer_vorbis import VorbisCommentWriter
from src.core.logger_config import logger


class OggFileWriter:
    """Reads/writes the Vorbis comment header packet directly against an
    OGG file, preserving whatever the app doesn't itself manage."""

    def __init__(self):
        self.vorbis_writer = VorbisCommentWriter()
        self.raw_tag_extractor = RawTagExtractor()

    def write_tags(self, file_path: str, new_comments: dict, mode: WriteMode) -> bool:
        """Replace the Vorbis comment header packet in an OGG file with a
        merge of new_comments and whatever's already there, per WriteMode.

        Delegates the actual page-level rewrite to replace_comment_packet,
        which rewrites only the page(s) holding the 3 header packets and
        renumbers/rechecksums any pages after them - every audio page's
        payload is carried through byte-for-byte. A naive byte-splice
        can't do this correctly: OGG's page framing (segment table sizes)
        and checksums are position/length-dependent.

        No backup handling here - the caller (MetadataWriter) wraps both
        the FLAC and OGG tag-write paths in one shared backup/restore.
        """
        try:
            with open(file_path, "rb") as f:
                file_data = f.read()

            existing_comments = self.raw_tag_extractor.extract_raw_tags(
                file_data, ".ogg"
            )
            merged = merge_vorbis_comments(existing_comments, new_comments, mode)
            comment_block = self.vorbis_writer.build_vorbis_comments(merged)

            # An Ogg comment header *packet* needs framing FLAC's raw
            # VORBIS_COMMENT block doesn't: the "\x03vorbis" packet-type
            # prefix, and a trailing framing bit (a single 0x01 byte) the
            # Vorbis I spec requires decoders to validate.
            new_comment_packet = b"\x03vorbis" + comment_block + b"\x01"
            new_file_data = replace_comment_packet(file_data, new_comment_packet)

            if new_file_data is None:
                logger.debug(
                    f"Could not safely rewrite OGG comment header for {file_path} "
                    "(non-standard page layout)"
                )
                return False

            atomic_write(file_path, new_file_data)

            return True

        except (OSError, AttributeError, struct.error, ValueError) as e:
            logger.debug(f"Error writing OGG metadata: {e}")
            return False
