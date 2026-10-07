"""Applies WriteMode semantics when combining freshly-built tags with a file's existing tags."""

from src.foundation.logger_config import logger
from src.metadata.metadata_byte_utils import decode_id3_string, parse_id3_lang_frame, split_id3_string
from src.metadata.writers.metadata_writer_types import WriteMode

# Artwork is managed by the artwork writers, so tag writes never remove it, even in REPLACE_ALL.
_ID3_PICTURE_FRAMES = frozenset({"APIC", "PIC"})
_VORBIS_PICTURE_KEYS = frozenset({"METADATA_BLOCK_PICTURE", "COVERART"})


def id3_frame_key(frame: bytes) -> str:
    """Return the identity of a full v2.3-headered frame: its ID, plus description/owner/language where the spec allows repeats."""
    frame_id = frame[0:4].decode("ascii", errors="ignore")
    body = frame[10:]
    if not body:
        return frame_id
    if frame_id == "TXXX":
        description, _ = split_id3_string(body[1:], body[0])
        return f"TXXX:{decode_id3_string(description, body[0])}"
    if frame_id == "UFID":
        owner, _ = split_id3_string(body, 0)
        return f"UFID:{owner.decode('latin-1', errors='ignore')}"
    if frame_id in ("COMM", "USLT"):
        parsed = parse_id3_lang_frame(body)
        if parsed:
            _, language, description, _ = parsed
            return f"{frame_id}:{language}:{description}"
    return frame_id


def merge_id3_frames(existing_frames: list[tuple[str, bytes]], new_frames: list[bytes], mode: WriteMode) -> list[bytes]:
    """Combine a file's existing (frame_id, v2.3 frame bytes) pairs with new frame bytes, per mode."""
    logger.debug(f"Merging {len(new_frames)} new ID3 frames with {len(existing_frames)} existing frames using {mode.name}")
    if mode == WriteMode.REPLACE_ALL:
        pictures = [frame_bytes for frame_id, frame_bytes in existing_frames if frame_id in _ID3_PICTURE_FRAMES]
        return pictures + list(new_frames)

    existing_keys = {id3_frame_key(frame_bytes) for _, frame_bytes in existing_frames}
    preserved = [frame_bytes for _, frame_bytes in existing_frames]

    if mode == WriteMode.ADD_ONLY:
        # Existing wins; new frames only fill keys the file does not have.
        return preserved + [frame for frame in new_frames if id3_frame_key(frame) not in existing_keys]

    # UPDATE_EXISTING: new values win for each key the app builds; everything else carries through.
    new_keys = {id3_frame_key(frame) for frame in new_frames if len(frame) >= 4}
    preserved = [frame_bytes for frame_bytes in preserved if id3_frame_key(frame_bytes) not in new_keys]
    return preserved + list(new_frames)


def merge_vorbis_comments(existing: dict[str, object], new: dict[str, object], mode: WriteMode) -> dict[str, object]:
    """Combine a file's existing Vorbis comments with new ones, per mode."""
    logger.debug(f"Merging {len(new)} new Vorbis comments with {len(existing)} existing comments using {mode.name}")
    if mode == WriteMode.REPLACE_ALL:
        pictures = {key: value for key, value in existing.items() if key in _VORBIS_PICTURE_KEYS}
        return {**pictures, **new}

    if mode == WriteMode.ADD_ONLY:
        return {**new, **existing}

    return {**existing, **new}
