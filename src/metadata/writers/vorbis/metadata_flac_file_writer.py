"""Low-level FLAC file surgery for tag and artwork writes; no database access."""

from pathlib import Path
import struct
from typing import Any

from src.foundation.logger_config import logger
from src.metadata.metadata_byte_utils import syncsafe_to_int
from src.metadata.metadata_image_utils import find_picture_indices_for_role
from src.metadata.readers.metadata_raw_tags import RawTagExtractor
from src.metadata.writers.metadata_writer_backup import atomic_write, write_artwork_with_backup
from src.metadata.writers.metadata_writer_merge import merge_vorbis_comments
from src.metadata.writers.metadata_writer_types import WriteMode
from src.metadata.writers.vorbis.metadata_writer_flac_picture import FlacPictureWriter
from src.metadata.writers.vorbis.metadata_writer_vorbis import VorbisCommentWriter


class FlacFileWriter:
    """Reads/writes the Vorbis comment block and artwork in a FLAC file, keeping blocks the app does not manage."""

    def __init__(self):
        """Create the comment and picture builders and the raw tag reader."""
        self.vorbis_writer = VorbisCommentWriter()
        self.flac_picture_writer = FlacPictureWriter()
        self.raw_tag_extractor = RawTagExtractor()

    def write_tags(self, file_path: str, new_comments: dict, mode: WriteMode) -> bool:
        """Replace the Vorbis comment block with new_comments merged into the existing ones per mode."""
        # No backup here: MetadataWriter wraps the FLAC and OGG tag writes in one backup/restore.
        try:
            file_data = Path(file_path).read_bytes()

            existing_comments = self.raw_tag_extractor.extract_raw_tags(file_data, ".flac")
            merged = merge_vorbis_comments(existing_comments, new_comments, mode)
            new_comment_block = self.vorbis_writer.build_vorbis_comments(merged)

            return self._replace_comment_block(file_path, file_data, new_comment_block)

        except (OSError, AttributeError, struct.error, ValueError) as e:
            logger.debug(f"Error writing FLAC metadata: {e}")
            return False

    def _replace_comment_block(self, file_path: str, file_data: bytes, new_comment_block: bytes) -> bool:
        """Replace the VORBIS_COMMENT block (type 4); other blocks and the audio pass through unchanged."""
        prefix_length = self._prefix_length(file_data)
        blocks = self._parse_metadata_blocks(file_data, prefix_length)
        if not blocks:
            return False

        ordered_blocks = [(block_type, file_data[pos : pos + size]) for block_type, pos, size in blocks if block_type != 4]
        if new_comment_block:
            ordered_blocks.append((4, new_comment_block))

        atomic_write(file_path, self._serialize_blocks(ordered_blocks, self._audio_tail(file_data, blocks), file_data[:prefix_length]))
        return True

    def write_artwork(self, file_path: str, role: str, image_bytes: Any) -> bool:
        """Add/replace (image_bytes given) or remove (None) the PICTURE block for role; other blocks carry through."""

        def mutate() -> bool:
            """Rewrite the file with role's PICTURE blocks replaced; False if it has no metadata blocks."""
            file_data = Path(file_path).read_bytes()
            prefix_length = self._prefix_length(file_data)
            blocks = self._parse_metadata_blocks(file_data, prefix_length)
            if not blocks:
                return False

            # Offsets are only valid against the original bytes, so slice every payload first.
            raw_blocks = [(block_type, file_data[pos : pos + size]) for block_type, pos, size in blocks]
            target_indices = set(self._find_picture_indices_for_role(raw_blocks, role))
            new_blocks = [(block_type, payload) for idx, (block_type, payload) in enumerate(raw_blocks) if idx not in target_indices]

            if image_bytes is not None:
                new_blocks.append((6, self.flac_picture_writer.build_picture_block(role, image_bytes)))

            atomic_write(file_path, self._serialize_blocks(new_blocks, self._audio_tail(file_data, blocks), file_data[:prefix_length]))
            return True

        return write_artwork_with_backup(file_path, role, image_bytes, FlacPictureWriter.ROLE_TO_TYPE, mutate, "artwork")

    def _prefix_length(self, file_data: bytes) -> int:
        """Return the byte count before the "fLaC" marker (a leading ID3v2 tag), or -1 if there is no marker."""
        if file_data[0:4] == b"fLaC":
            return 0
        if file_data[0:3] == b"ID3" and len(file_data) >= 10:
            # Non-standard, but real files carry a leading ID3 tag that must survive a rewrite.
            id3_end = 10 + syncsafe_to_int(file_data[6:10])
            if file_data[id3_end : id3_end + 4] == b"fLaC":
                return id3_end
        return -1

    def _parse_metadata_blocks(self, file_data: bytes, prefix_length: int) -> list[tuple[int, int, int]]:
        """Return (block_type, payload_start, payload_size) for each metadata block."""
        blocks = []
        if prefix_length < 0:
            return blocks

        pos = prefix_length + 4
        while pos + 4 <= len(file_data):
            header = file_data[pos : pos + 4]
            is_last = header[0] & 0x80
            block_type = header[0] & 0x7F
            block_size = struct.unpack(">I", b"\x00" + header[1:4])[0]
            if pos + 4 + block_size > len(file_data):
                logger.debug("FLAC metadata block overruns the file; stopping block scan")
                break
            blocks.append((block_type, pos + 4, block_size))
            pos += 4 + block_size
            if is_last:
                break

        return blocks

    def _audio_tail(self, file_data: bytes, blocks: list[tuple[int, int, int]]) -> bytes:
        """Return the bytes after the last metadata block (the audio frames)."""
        if not blocks:
            return b""
        _, last_pos, last_size = blocks[-1]
        return file_data[last_pos + last_size :]

    def _serialize_blocks(self, ordered_blocks: list[tuple[int, bytes]], audio_tail: bytes, prefix: bytes = b"") -> bytes:
        """Serialize prefix + "fLaC" + blocks (is_last set on the final one) + audio_tail."""
        out = bytearray(prefix)
        out += b"fLaC"
        for i, (block_type, payload) in enumerate(ordered_blocks):
            if len(payload) >= 1 << 24:
                # The block size field is 24 bits; a larger payload would corrupt the stream.
                raise ValueError(f"FLAC metadata block of {len(payload)} bytes exceeds the 16 MiB limit")
            is_last = 1 if i == len(ordered_blocks) - 1 else 0
            out += struct.pack(">B", (is_last << 7) | (block_type & 0x7F))
            out += struct.pack(">I", len(payload))[1:]  # 3-byte size
            out += payload
        out += audio_tail
        return bytes(out)

    def _find_picture_indices_for_role(self, raw_blocks: list[tuple[int, bytes]], role: str):
        """Return the indices of every PICTURE block that represents role (duplicates included)."""

        def picture_type_for_block(item: tuple[int, bytes]):
            """Return a PICTURE block's picture type, or None for other blocks."""
            block_type, payload = item
            if block_type != 6 or len(payload) < 4:  # PICTURE block, has a type field
                return None
            return struct.unpack(">I", payload[:4])[0]

        return find_picture_indices_for_role(raw_blocks, role, picture_type_for_block)
