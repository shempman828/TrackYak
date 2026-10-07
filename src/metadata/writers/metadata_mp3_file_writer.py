"""Low-level MP3/ID3 file surgery for tag and artwork writes; no database access."""

import os
from pathlib import Path
import struct
from typing import Any

from src.foundation.logger_config import logger
from src.metadata.metadata_byte_utils import id3_frames_offset, id3_tag_end, is_valid_frame_id, syncsafe_to_int
from src.metadata.metadata_image_utils import find_picture_indices_for_role
from src.metadata.writers.metadata_id3_writer import ID3TagWriter
from src.metadata.writers.metadata_writer_backup import atomic_write, backup_file, discard_backup, restore_backup, write_artwork_with_backup
from src.metadata.writers.metadata_writer_id3_picture import Id3PictureWriter
from src.metadata.writers.metadata_writer_merge import id3_frame_key, merge_id3_frames
from src.metadata.writers.metadata_writer_types import WriteMode


class MP3FileWriter:
    """Reads/writes ID3v2.3/2.4 tags and artwork in an MP3 file, keeping frames the app does not manage."""

    def __init__(self):
        """Create the tag and picture frame writers."""
        self.id3_writer = ID3TagWriter()
        self.id3_picture_writer = Id3PictureWriter()

    def write_tags(self, file_path: str, new_frames: list[bytes], mode: WriteMode) -> bool:
        """Write new_frames to file_path's ID3 tag, merged with the existing frames per mode."""
        backup_path = None
        try:
            backup_path = backup_file(file_path)
            file_data = Path(file_path).read_bytes()

            all_frames = merge_id3_frames(self._existing_frames(file_data), new_frames, mode)
            # A v2.2 source tag is not parsed for merge, so its frames are replaced by the new v2.3 tag.
            atomic_write(file_path, self.id3_writer.build_id3_tag(all_frames) + file_data[id3_tag_end(file_data) :])

            discard_backup(backup_path)
            return True

        except (OSError, struct.error, TypeError) as e:
            logger.debug(f"Error writing ID3 metadata: {e}")
            if backup_path and Path(backup_path).exists():
                restore_backup(file_path, backup_path)
            return False

    def get_existing_frame_map(self, file_path: str) -> dict[str, bytes]:
        """Return {frame key: v2.3-headered frame bytes} for the file's current frames (last repeat wins)."""
        try:
            file_data = Path(file_path).read_bytes()
        except OSError as e:
            logger.debug(f"Error reading existing ID3 frames for {file_path}: {e}")
            return {}
        return {id3_frame_key(frame_bytes): frame_bytes for _, frame_bytes in self._existing_frames(file_data)}

    def write_artwork(self, file_path: str, role: str, image_bytes: Any) -> bool:
        """Add/replace (image_bytes given) or remove (None) the APIC frame for role; other frames carry through."""
        if role not in Id3PictureWriter.ROLE_TO_TYPE:
            raise ValueError(f"Unknown artwork role: {role}")

        if not Path(file_path).exists():
            logger.debug(f"Skipping artwork write - file not found: {file_path}")
            return False

        if not os.access(file_path, os.W_OK):
            logger.debug(f"Skipping artwork write - not writable: {file_path}")
            return False

        with Path(file_path).open("rb") as f:
            header = f.read(10)
        if header[0:3] == b"ID3" and header[3] not in (3, 4):
            logger.debug(f"ID3 tag version {header[3]} is not writable, cannot write artwork: {file_path}")
            return False
        # A file with no ID3 tag gets a new v2.3 tag.
        version_major = header[3] if header[0:3] == b"ID3" else 3

        def mutate() -> bool:
            """Rewrite the file's tag with role's APIC frames replaced."""
            file_data = Path(file_path).read_bytes()
            raw_frames = self._existing_frames(file_data)

            target_indices = set(self._find_picture_indices_for_role(raw_frames, role, version_major))
            new_frames = [frame_bytes for idx, (_, frame_bytes) in enumerate(raw_frames) if idx not in target_indices]

            if image_bytes is not None:
                new_frames.append(self.id3_picture_writer.build_apic_frame(role, image_bytes))

            atomic_write(file_path, self.id3_writer.build_id3_tag(new_frames) + file_data[id3_tag_end(file_data) :])
            return True

        return write_artwork_with_backup(file_path, role, image_bytes, Id3PictureWriter.ROLE_TO_TYPE, mutate, "MP3 artwork")

    def _existing_frames(self, file_data: bytes) -> list[tuple[str, bytes]]:
        """Return (frame_id, v2.3-headered frame bytes) for each frame of a v2.3/2.4 tag in file_data."""
        return [(frame_id, self._rewrap_frame_as_v3(frame_id, file_data[pos + 10 : pos + size])) for frame_id, pos, size in self._parse_frames(file_data)]

    def _parse_frames(self, file_data: bytes) -> list[tuple[str, int, int]]:
        """Return (frame_id, start, whole-frame size) for each v2.3/2.4 frame; [] for no tag or a v2.2 tag."""
        frames = []
        if len(file_data) < 10 or file_data[0:3] != b"ID3":
            return frames

        version_major = file_data[3]
        if version_major not in (3, 4):
            logger.debug(f"Unsupported ID3 version {version_major} for frame-level writes")
            return frames

        tag_end = min(10 + syncsafe_to_int(file_data[6:10]), len(file_data))
        pos = id3_frames_offset(file_data)
        while pos < tag_end - 10:
            frame_id = file_data[pos : pos + 4]
            if not is_valid_frame_id(frame_id):
                break  # padding, or a corrupt header that would desync every later frame
            size_bytes = file_data[pos + 4 : pos + 8]
            frame_size = syncsafe_to_int(size_bytes) if version_major == 4 else struct.unpack(">I", size_bytes)[0]
            if frame_size == 0 or pos + 10 + frame_size > tag_end:
                break
            frames.append((frame_id.decode("ascii"), pos, 10 + frame_size))
            pos += 10 + frame_size

        return frames

    def _rewrap_frame_as_v3(self, frame_id: str, frame_body: bytes) -> bytes:
        """Rebuild a frame header with v2.3 plain sizing, since build_id3_tag always writes a v2.3 tag."""
        # A v2.4 syncsafe size kept as-is would desync every later frame boundary.
        return frame_id.encode("ascii") + struct.pack(">I", len(frame_body)) + b"\x00\x00" + frame_body

    def _peek_picture_type(self, frame_body: bytes, version_major: int):
        """Read only the picture-type byte of an APIC/PIC frame body, or None."""
        try:
            if len(frame_body) < 2:
                return None
            pos = 1  # skip encoding byte
            if version_major == 2:
                pos += 3  # v2.2 PIC: fixed 3-byte image format code
            else:
                while pos < len(frame_body) and frame_body[pos] != 0:
                    pos += 1
                pos += 1  # skip the NUL-terminated MIME string
            if pos >= len(frame_body):
                return None
            return frame_body[pos]
        except IndexError:
            return None

    def _find_picture_indices_for_role(self, raw_frames: list[tuple[str, bytes]], role: str, version_major: int):
        """Return the indices of every APIC/PIC frame that represents role (duplicates included)."""

        def picture_type_for_frame(item: tuple[str, bytes]):
            """Return an APIC/PIC frame's picture type, or None for other frames."""
            frame_id, frame_bytes = item
            if frame_id not in ("APIC", "PIC"):
                return None
            return self._peek_picture_type(frame_bytes[10:], version_major)

        return find_picture_indices_for_role(raw_frames, role, picture_type_for_frame)
