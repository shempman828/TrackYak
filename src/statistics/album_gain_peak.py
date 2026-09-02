"""
Album-level ReplayGain aggregation.

album_gain/album_peak are no longer user-editable (see ALBUM_FIELDS in
db_mapping_albums.py) — they're derived from the per-track track_gain/
track_peak values that AudioCalculations.run_all() already computes
(src/statistics/audio/calculations.py), the same way a real ReplayGain
tagger derives "album" values from a batch of "track" values.
"""

from sqlalchemy.exc import SQLAlchemyError

from src.foundation.logger_config import logger


def compute_album_gain_peak(tracks) -> tuple[float | None, float | None]:
    """Aggregate track_gain/track_peak across an album's tracks.

    Album peak is the loudest true peak of any track, since that's the
    sample that would clip if the whole album were played back at a single
    album-relative volume. Album gain is duration-weighted so a handful of
    long tracks aren't outvoted by a pile of short ones (skits, interludes).
    Tracks missing a value (not yet analyzed) are simply excluded.
    """
    peaks = [t.track_peak for t in tracks if t.track_peak is not None]
    album_peak = max(peaks) if peaks else None

    weighted = [(t.track_gain, t.duration or 1.0) for t in tracks if t.track_gain is not None]
    if weighted:
        total_weight = sum(w for _, w in weighted)
        album_gain = (
            sum(g * w for g, w in weighted) / total_weight
            if total_weight
            else sum(g for g, _ in weighted) / len(weighted)
        )
    else:
        album_gain = None

    return album_gain, album_peak


def recompute_album_gain_peak(controller, album_id: int) -> None:
    """Recompute and persist album_gain/album_peak for one album."""
    tracks = controller.get.get_all_entities("Track", album_id=album_id)
    if not tracks:
        return

    album_gain, album_peak = compute_album_gain_peak(tracks)
    try:
        controller.update.update_entity(
            "Album", album_id, album_gain=album_gain, album_peak=album_peak
        )
    except SQLAlchemyError as e:
        logger.error(f"Failed to update album_gain/album_peak for album {album_id}: {e}")
