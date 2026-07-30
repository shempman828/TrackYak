"""
ReplayGain-based playback gain calculation.

Extracted from MusicPlayer (player_util.py) so this pure lookup-and-math
logic is decoupled from the real-time audio engine — it touches the DB,
not the reader thread/buffer/stream machinery.
"""

from sqlalchemy.exc import SQLAlchemyError

from src.core.logger_config import logger

# Must match audio_calculations.py's REFERENCE_LUFS — both sides assume
# track_gain is a ReplayGain-style adjustment relative to this reference.
REPLAYGAIN_REFERENCE_LUFS = -18.0


def get_track_gain_from_db(controller, current_file):
    """Fetch track_gain and track_peak from the DB for current_file."""
    try:
        if current_file is None:
            return None, None
        track = controller.get.get_entity_object(
            "Track", track_file_path=str(current_file)
        )
        if track:
            gain = getattr(track, "track_gain", None)
            peak = getattr(track, "track_peak", None)
            if gain is not None and peak is not None:
                return float(gain), float(peak)
    except (SQLAlchemyError, ValueError, TypeError) as exc:
        logger.error(f"DB gain lookup error: {exc}")
    return None, None


def calculate_gain_factor(
    controller, current_file, normalization_enabled: bool, normalization_target: float
) -> float:
    """
    Returns the multiplier applied to every audio chunk.
    Uses ReplayGain from the DB when available; falls back to 1.0.
    """
    if not normalization_enabled or current_file is None:
        return 1.0

    try:
        track_gain, track_peak = get_track_gain_from_db(controller, current_file)

        if track_gain is not None:
            # ReplayGain stores the adjustment needed to reach reference loudness.
            # We then shift that reference to our target (default -14 LUFS).
            # Reference loudness for ReplayGain is -18 LUFS (older standard) or
            # -23 LUFS (EBU R128). We offset from -18 as a safe middle ground.
            target_offset = normalization_target - REPLAYGAIN_REFERENCE_LUFS
            gain_db = track_gain + target_offset
            gain_factor = 10.0 ** (gain_db / 20.0)

            # Peak limiter: only clamp if the boosted signal would clip.
            # This runs AFTER gain is set so quiet tracks still get lifted.
            if track_peak and track_peak > 0:
                max_output = gain_factor * float(track_peak)
                if max_output > 0.99:
                    gain_factor = 0.99 / float(track_peak)

            logger.debug(
                f"Gain factor (ReplayGain): {gain_factor:.4f}  "
                f"(track_gain={track_gain:.2f} dB, target={normalization_target} LUFS)"
            )
            return float(gain_factor)

    except (ValueError, TypeError) as exc:
        logger.error(f"Gain calculation error: {exc}")

    return 1.0  # Safe default
