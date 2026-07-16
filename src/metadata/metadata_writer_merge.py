"""Applies WriteMode (ADD_ONLY/UPDATE_EXISTING/REPLACE_ALL) semantics when
combining freshly-built tag data with whatever a file already has on disk.
"""

from typing import Dict, List, Tuple

from src.core.logger_config import logger
from src.metadata.metadata_writer_types import WriteMode


def merge_id3_frames(
    existing_frames: List[Tuple[str, bytes]],
    new_frames: List[bytes],
    mode: WriteMode,
) -> List[bytes]:
    """Combine a file's existing ID3 frames with freshly-built ones.

    Args:
        existing_frames: (frame_id, full_frame_bytes) pairs already found
            in the file (frame_bytes already re-headered as v2.3).
        new_frames: Frame bytes just built from database data.
        mode: How to reconcile the two.

    Returns:
        The full list of frame bytes to write.
    """
    logger.debug(
        f"Merging {len(new_frames)} new ID3 frames with "
        f"{len(existing_frames)} existing frames using {mode.name}"
    )
    if mode == WriteMode.REPLACE_ALL:
        return list(new_frames)

    if mode == WriteMode.ADD_ONLY:
        # Existing wins for any frame ID already present; new frames only
        # fill in IDs the file doesn't have at all.
        existing_ids = {frame_id for frame_id, _ in existing_frames}
        added = [
            f
            for f in new_frames
            if f[0:4].decode("ascii", errors="ignore") not in existing_ids
        ]
        preserved = [frame_bytes for _, frame_bytes in existing_frames]
        return preserved + added

    # UPDATE_EXISTING: new values win for any frame ID the app builds;
    # anything else already in the file (e.g. embedded artwork, or a tag
    # this app doesn't map) is carried through unchanged.
    new_frame_ids = {
        f[0:4].decode("ascii", errors="ignore") for f in new_frames if len(f) >= 4
    }
    preserved = [
        frame_bytes
        for frame_id, frame_bytes in existing_frames
        if frame_id not in new_frame_ids
    ]
    return preserved + list(new_frames)


def merge_vorbis_comments(
    existing: Dict[str, object], new: Dict[str, object], mode: WriteMode
) -> Dict[str, object]:
    """Combine a file's existing Vorbis comments with freshly-built ones.

    Args:
        existing: Tag name -> value (or list of values), as read off disk.
        new: Tag name -> value (or list of values), just built from
            database data.
        mode: How to reconcile the two.

    Returns:
        The merged comment dict to serialize and write.
    """
    logger.debug(
        f"Merging {len(new)} new Vorbis comments with "
        f"{len(existing)} existing comments using {mode.name}"
    )
    if mode == WriteMode.REPLACE_ALL:
        return dict(new)

    if mode == WriteMode.ADD_ONLY:
        # Existing wins for any key already present; new only fills in
        # keys the file doesn't have at all.
        return {**new, **existing}

    # UPDATE_EXISTING: new wins for any key it defines; anything else
    # already in the file is carried through unchanged.
    return {**existing, **new}
