"""Low-level MP3/ID3 file surgery: finding, preserving, and replacing
frames in an existing file, for both tag and artwork writes. Works
entirely on file paths and byte blobs - no database access.
"""

import os
import struct
from typing import Any

from src.core.logger_config import logger
from src.metadata.metadata_byte_utils import syncsafe_to_int
from src.metadata.metadata_id3_writer import ID3TagWriter
from src.metadata.metadata_image_utils import find_picture_index_for_role
from src.metadata.metadata_writer_backup import (
    atomic_write,
    backup_file,
    discard_backup,
    restore_backup,
    write_artwork_with_backup,
)
from src.metadata.metadata_writer_id3_picture import Id3PictureWriter
from src.metadata.metadata_writer_merge import merge_id3_frames
from src.metadata.metadata_writer_types import WriteMode


class MP3FileWriter:
    """Reads/writes ID3v2.3/2.4 tags and artwork directly against an MP3
    file, preserving whatever the app doesn't itself manage."""

    def __init__(self):
        self.id3_writer = ID3TagWriter()
        self.id3_picture_writer = Id3PictureWriter()

    def write_tags(self, file_path: str, new_frames: list[bytes], mode: WriteMode) -> bool:
        """Write new_frames to file_path's ID3 tag, per WriteMode.

        UPDATE_EXISTING preserves any existing frame whose ID isn't one of
        the ones being rebuilt (e.g. an embedded APIC frame, or any frame
        this app doesn't itself construct) rather than wiping the whole
        tag - only applies to v2.3/2.4 source tags, same as the artwork
        writer.
        """
        backup_path = None
        try:
            backup_path = backup_file(file_path)

            existing_frames = self._find_frames(file_path)
            with open(file_path, "rb") as f:
                file_data = f.read()

            existing_frame_bytes = [
                (frame_id, self._rewrap_frame_as_v3(frame_id, file_data[pos + 10 : pos + size]))
                for frame_id, pos, size in existing_frames
            ]

            all_frames = merge_id3_frames(existing_frame_bytes, new_frames, mode)
            new_tag = self.id3_writer.build_id3_tag(all_frames)

            audio_start = self._find_audio_start(file_path)
            with open(file_path, "rb") as f:
                f.seek(audio_start)
                audio_data = f.read()

            atomic_write(file_path, new_tag + audio_data)

            discard_backup(backup_path)
            return True

        except (OSError, struct.error, TypeError) as e:
            logger.debug(f"Error writing ID3 metadata: {e}")
            if backup_path and os.path.exists(backup_path):
                restore_backup(file_path, backup_path)
            return False

    def get_existing_frame_map(self, file_path: str) -> dict[str, bytes]:
        """Return {frame_id: full_frame_bytes} for the file's current ID3
        frames, re-headered as v2.3 so they're byte-comparable against
        frames freshly built by ID3FrameBuilder (same header format,
        see _rewrap_frame_as_v3). Frame IDs that repeat keep only their
        last occurrence, matching merge_id3_frames' one-frame-per-ID
        handling of app-managed tags.
        """
        try:
            existing_frames = self._find_frames(file_path)
            with open(file_path, "rb") as f:
                file_data = f.read()

            return {
                frame_id: self._rewrap_frame_as_v3(frame_id, file_data[pos + 10 : pos + size])
                for frame_id, pos, size in existing_frames
            }
        except (OSError, struct.error) as e:
            logger.debug(f"Error reading existing ID3 frames for {file_path}: {e}")
            return {}

    def write_artwork(self, file_path: str, role: str, image_bytes: Any) -> bool:
        """
        Add/replace (image_bytes given) or remove (image_bytes=None) the
        APIC frame for `role` in an MP3 file's ID3v2.3/2.4 tag. Every other
        frame's body - including APIC frames for other roles - passes
        through unchanged (only its header is re-serialized as v2.3-style,
        since the whole tag is always rewritten as v2.3, matching
        ID3TagWriter.build_id3_tag).
        """
        if role not in Id3PictureWriter.ROLE_TO_TYPE:
            raise ValueError(f"Unknown artwork role: {role}")

        if not os.access(file_path, os.W_OK):
            logger.debug(f"Skipping artwork write - not writable: {file_path}")
            return False

        with open(file_path, "rb") as f:
            header = f.read(10)
        if len(header) < 10 or header[0:3] != b"ID3" or header[3] not in (3, 4):
            logger.debug(f"No writable ID3v2.3/2.4 tag found, cannot write artwork: {file_path}")
            return False
        version_major = header[3]

        def mutate() -> bool:
            existing_frames = self._find_frames(file_path)

            with open(file_path, "rb") as f:
                file_data = f.read()

            raw_frames = [
                (frame_id, self._rewrap_frame_as_v3(frame_id, file_data[pos + 10 : pos + size]))
                for frame_id, pos, size in existing_frames
            ]

            target_idx = self._find_picture_index_for_role(raw_frames, role, version_major)

            new_frames = [
                frame_bytes
                for idx, (frame_id, frame_bytes) in enumerate(raw_frames)
                if idx != target_idx
            ]

            if image_bytes is not None:
                new_frames.append(self.id3_picture_writer.build_apic_frame(role, image_bytes))

            new_tag = self.id3_writer.build_id3_tag(new_frames)

            audio_start = self._find_audio_start(file_path)
            with open(file_path, "rb") as f:
                f.seek(audio_start)
                audio_data = f.read()

            atomic_write(file_path, new_tag + audio_data)

            return True

        return write_artwork_with_backup(
            file_path, role, image_bytes, Id3PictureWriter.ROLE_TO_TYPE, mutate, "MP3 artwork"
        )

    def _find_audio_start(self, file_path: str) -> int:
        """Find the start of MP3 audio data (after ID3 tag)."""
        try:
            with open(file_path, "rb") as f:
                header = f.read(10)
                if header.startswith(b"ID3"):
                    size = syncsafe_to_int(header[6:10])
                    return 10 + size
                return 0
        except OSError:
            return 0

    def _find_frames(self, file_path: str) -> list[tuple[str, int, int]]:
        """
        Find ID3v2 frames and their byte spans (whole frame, header
        included). Returns [] if there's no ID3 tag, or the tag is v2.2
        (3-char frame IDs, incompatible header layout - reads still work
        via ArtworkExtractor, but this writer only rewrites v2.3/2.4 tags).
        """
        frames = []
        try:
            with open(file_path, "rb") as f:
                header = f.read(10)
                if len(header) < 10 or header[0:3] != b"ID3":
                    return frames

                version_major = header[3]
                if version_major not in (3, 4):
                    logger.debug(
                        f"Unsupported ID3 version {version_major} for "
                        f"frame-level writes: {file_path}"
                    )
                    return frames

                tag_size = syncsafe_to_int(header[6:10])
                tag_end = 10 + tag_size

                pos = 10
                while pos < tag_end - 10:
                    f.seek(pos)
                    frame_header = f.read(10)
                    if len(frame_header) < 10:
                        break

                    frame_id = frame_header[0:4]
                    if version_major == 4:
                        frame_size = syncsafe_to_int(frame_header[4:8])
                    else:
                        frame_size = struct.unpack(">I", frame_header[4:8])[0]

                    if frame_size == 0:
                        break

                    frames.append((frame_id.decode("ascii", errors="ignore"), pos, 10 + frame_size))
                    pos += 10 + frame_size

        except OSError as e:
            logger.debug(f"Error finding ID3 frames for {file_path}: {e}")

        return frames

    def _rewrap_frame_as_v3(self, frame_id: str, frame_body: bytes) -> bytes:
        """
        Rebuild a frame's 10-byte header using ID3v2.3-style plain
        (non-syncsafe) frame sizing, regardless of what version the frame
        originally came from. ID3TagWriter.build_id3_tag always writes the
        tag as v2.3, so every preserved frame must be re-headered to match -
        otherwise a frame carried over unchanged from a v2.4 source tag
        (syncsafe size) desyncs every frame boundary that follows it.
        """
        return (
            frame_id.encode("ascii") + struct.pack(">I", len(frame_body)) + b"\x00\x00" + frame_body
        )

    def _peek_picture_type(self, frame_body: bytes, version_major: int):
        """Read just the picture-type byte out of an APIC/PIC frame body,
        without doing the full parse ArtworkExtractor does."""
        try:
            if len(frame_body) < 2:
                return None
            pos = 1  # skip encoding byte
            if version_major == 2:
                pos += 3  # v2.2 PIC: fixed 3-byte image format code
            else:
                while pos < len(frame_body) and frame_body[pos] != 0:
                    pos += 1
                pos += 1  # skip past the null-terminated MIME string
            if pos >= len(frame_body):
                return None
            return frame_body[pos]
        except IndexError:
            return None

    def _find_picture_index_for_role(
        self, raw_frames: list[tuple[str, bytes]], role: str, version_major: int
    ):
        """
        Find the index of the existing APIC/PIC frame that currently
        represents `role`, using the same typed + untyped-fallback-to-front
        rule as ArtworkExtractor.extract_artwork_by_role.
        """

        def picture_type_for_frame(item: tuple[str, bytes]):
            frame_id, frame_bytes = item
            if frame_id not in ("APIC", "PIC"):
                return None
            return self._peek_picture_type(frame_bytes[10:], version_major)

        return find_picture_index_for_role(raw_frames, role, picture_type_for_frame)
