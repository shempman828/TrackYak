"""Low-level FLAC file surgery: finding, preserving, and replacing
metadata blocks in an existing file, for both tag and artwork writes.
Works entirely on file paths and byte blobs - no database access.
"""

import os
import struct
from typing import Any, Dict, List, Tuple

from src.metadata.metadata_byte_utils import syncsafe_to_int
from src.metadata.metadata_raw_tags import RawTagExtractor
from src.metadata.metadata_writer_backup import (
    backup_file,
    discard_backup,
    restore_backup,
    verify_artwork_write,
)
from src.metadata.metadata_writer_flac_picture import FlacPictureWriter
from src.metadata.metadata_writer_merge import merge_vorbis_comments
from src.metadata.metadata_writer_types import WriteMode
from src.metadata.metadata_writer_vorbis import VorbisCommentWriter
from src.core.logger_config import logger


class FlacFileWriter:
    """Reads/writes the Vorbis comment block and artwork directly against
    a FLAC file, preserving whatever the app doesn't itself manage."""

    def __init__(self):
        self.vorbis_writer = VorbisCommentWriter()
        self.flac_picture_writer = FlacPictureWriter()
        self.raw_tag_extractor = RawTagExtractor()

    def write_tags(self, file_path: str, new_comments: dict, mode: WriteMode) -> bool:
        """Replace file_path's Vorbis comment block with a merge of
        new_comments and whatever's already there, per WriteMode.

        No backup handling here - the caller (MetadataWriter) wraps both
        the FLAC and OGG tag-write paths in one shared backup/restore,
        the same way the original single-file implementation did.
        """
        try:
            with open(file_path, "rb") as f:
                file_data = f.read()

            existing_comments = self.raw_tag_extractor.extract_raw_tags(
                file_data, ".flac"
            )
            merged = merge_vorbis_comments(existing_comments, new_comments, mode)
            new_comment_block = self.vorbis_writer.build_vorbis_comments(merged)

            return self._replace_comment_block(file_path, new_comment_block)

        except Exception as e:
            logger.debug(f"Error writing FLAC metadata: {e}")
            return False

    def _replace_comment_block(self, file_path: str, new_comment_block: bytes) -> bool:
        """Replace the Vorbis comment block (type 4); every other block,
        and the audio frames, pass through byte-for-byte unchanged."""
        try:
            blocks = self._find_metadata_blocks(file_path)
            if not blocks:
                return False

            with open(file_path, "rb") as f:
                file_data = f.read()

            audio_tail = self._audio_tail(file_data, blocks)
            prefix = file_data[: self._prefix_length(file_path)]

            # Keep every block except the existing Vorbis comment (type 4),
            # then append the new comment block last.
            ordered_blocks = []
            for block_type, pos, size in blocks:
                if block_type == 4:  # VORBIS_COMMENT - replaced below
                    continue
                ordered_blocks.append((block_type, file_data[pos : pos + size]))

            if new_comment_block:
                ordered_blocks.append((4, new_comment_block))

            new_data = self._serialize_blocks(ordered_blocks, audio_tail, prefix)

            with open(file_path, "wb") as f:
                f.write(new_data)

            return True

        except Exception as e:
            logger.debug(f"Error writing FLAC metadata: {e}")
            return False

    def write_artwork(self, file_path: str, role: str, image_bytes: Any) -> bool:
        """
        Add/replace (image_bytes given) or remove (image_bytes=None) the
        PICTURE block for `role` ("front"/"rear"/"liner") in a FLAC file.
        PICTURE blocks for other roles, and all non-PICTURE blocks, pass
        through byte-for-byte unchanged.
        """
        if role not in FlacPictureWriter.ROLE_TO_TYPE:
            raise ValueError(f"Unknown artwork role: {role}")

        if not os.access(file_path, os.W_OK):
            logger.debug(f"Skipping artwork write - not writable: {file_path}")
            return False

        backup_path = None
        try:
            backup_path = backup_file(file_path)

            blocks = self._find_metadata_blocks(file_path)
            if not blocks:
                discard_backup(backup_path)
                return False

            with open(file_path, "rb") as f:
                file_data = f.read()

            audio_tail = self._audio_tail(file_data, blocks)
            prefix = file_data[: self._prefix_length(file_path)]

            # Offsets are only valid against the original file, so slice out
            # every block's payload up front.
            raw_blocks = [
                (block_type, file_data[pos : pos + size])
                for block_type, pos, size in blocks
            ]

            target_idx = self._find_picture_index_for_role(raw_blocks, role)

            new_blocks = [
                (block_type, payload)
                for idx, (block_type, payload) in enumerate(raw_blocks)
                if idx != target_idx
            ]

            if image_bytes is not None:
                new_picture_payload = self.flac_picture_writer.build_picture_block(
                    role, image_bytes
                )
                new_blocks.append((6, new_picture_payload))

            new_data = self._serialize_blocks(new_blocks, audio_tail, prefix)

            with open(file_path, "wb") as f:
                f.write(new_data)

            if not verify_artwork_write(file_path, role, image_bytes):
                restore_backup(file_path, backup_path)
                logger.error(
                    f"Artwork write verification failed for {file_path} "
                    f"(role={role}); attempted to restore backup"
                )
                return False

            discard_backup(backup_path)
            return True

        except Exception as e:
            logger.debug(f"Error writing artwork to {file_path}: {e}")
            if backup_path and os.path.exists(backup_path):
                restore_backup(file_path, backup_path)
            return False

    def _prefix_length(self, file_path: str) -> int:
        """
        Bytes before the "fLaC" marker that must be preserved as-is on
        write - 0 normally, or the length of a leading ID3v2 tag some
        (non-standard, but real) FLAC files have. Returns -1 if no "fLaC"
        marker can be found at all.
        """
        try:
            with open(file_path, "rb") as f:
                header = f.read(10)
                if header[0:4] == b"fLaC":
                    return 0
                if header[0:3] == b"ID3" and len(header) == 10:
                    id3_end = 10 + syncsafe_to_int(header[6:10])
                    f.seek(id3_end)
                    if f.read(4) == b"fLaC":
                        return id3_end
        except Exception as e:
            logger.debug(f"Error checking FLAC prefix for {file_path}: {e}")
        return -1

    def _find_metadata_blocks(self, file_path: str) -> List[Tuple[int, int, int]]:
        """Find FLAC metadata blocks and their positions."""
        blocks = []
        try:
            prefix_length = self._prefix_length(file_path)
            if prefix_length < 0:
                return blocks

            with open(file_path, "rb") as f:
                f.seek(prefix_length + 4)

                # Read metadata blocks
                while True:
                    header = f.read(4)
                    if len(header) < 4:
                        break

                    is_last = (header[0] & 0x80) >> 7
                    block_type = header[0] & 0x7F
                    block_size = struct.unpack(">I", b"\x00" + header[1:4])[0]

                    current_pos = f.tell()
                    blocks.append((block_type, current_pos, block_size))

                    # Skip block data
                    f.seek(block_size, 1)

                    if is_last:
                        break

        except Exception as e:
            logger.debug(f"Error finding FLAC metadata blocks: {e}")

        return blocks

    def _audio_tail(
        self, file_data: bytes, blocks: List[Tuple[int, int, int]]
    ) -> bytes:
        """Bytes after the last metadata block - the actual audio frames,
        which must always be carried through untouched on any FLAC write."""
        if not blocks:
            return b""
        last_type, last_pos, last_size = blocks[-1]
        return file_data[last_pos + last_size :]

    def _serialize_blocks(
        self,
        ordered_blocks: List[Tuple[int, bytes]],
        audio_tail: bytes,
        prefix: bytes = b"",
    ) -> bytes:
        """
        Given an ordered list of (block_type, payload_bytes) - not including
        the "fLaC" magic - serialize a complete FLAC metadata-block stream
        followed by audio_tail (the untouched audio frame bytes). Recomputes
        the is_last bit on the final metadata block only; every block's own
        payload bytes are written through unchanged. `prefix` is carried
        through untouched before the "fLaC" magic - normally empty, but a
        leading ID3v2 tag on non-standard (but real) FLAC files must survive
        a rewrite exactly as it was.
        """
        out = bytearray(prefix)
        out += b"fLaC"
        for i, (block_type, payload) in enumerate(ordered_blocks):
            is_last = 1 if i == len(ordered_blocks) - 1 else 0
            block_header = struct.pack(">B", (is_last << 7) | (block_type & 0x7F))
            block_header += struct.pack(">I", len(payload))[1:]  # 3-byte size
            out += block_header
            out += payload
        out += audio_tail
        return bytes(out)

    def _find_picture_index_for_role(
        self, raw_blocks: List[Tuple[int, bytes]], role: str
    ):
        """
        Find the index of the existing PICTURE block that currently
        represents `role`, using the same typed + untyped-fallback-to-front
        rule as ArtworkExtractor.extract_artwork_by_role, so the writer and
        reader agree on which picture "is" the front/rear/liner cover.
        """
        typed_indices: Dict[str, int] = {}
        untyped_indices = []

        for idx, (block_type, payload) in enumerate(raw_blocks):
            if block_type != 6 or len(payload) < 4:  # PICTURE block, has a type field
                continue
            picture_type = struct.unpack(">I", payload[:4])[0]
            mapped_role = FlacPictureWriter.TYPE_TO_ROLE.get(picture_type)
            if mapped_role:
                typed_indices.setdefault(mapped_role, idx)
            else:
                untyped_indices.append(idx)

        if role in typed_indices:
            return typed_indices[role]

        if role == "front" and "front" not in typed_indices and len(untyped_indices) == 1:
            return untyped_indices[0]

        return None
