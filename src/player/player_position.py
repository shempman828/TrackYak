"""
player_position.py — position tracking and play-count recording for
MusicPlayer, ticked by the position timer.

Expects the host class to provide: self.playing, self.paused, self._sf_reader,
self._frames_played, self.current_sample_rate, self.position_changed signal,
self._duration, self.current_file, self.controller, and
self._flush_callback_diagnostics (see PlayerCallbackMixin).
"""

from datetime import datetime
import threading

from sqlalchemy.exc import SQLAlchemyError

from src.foundation.logger_config import logger

PLAY_COUNT_THRESHOLD = 0.90
POSITION_INTERVAL_MS = 50  # UI position update interval (20 fps).


class PlayerPositionMixin:
    """Fired by the position timer every POSITION_INTERVAL_MS to update the
    UI's playback position and record a play count once far enough in."""

    def _update_position(self):
        """Fired by the position timer every POSITION_INTERVAL_MS."""
        if not self.playing or self.paused or self._sf_reader is None:
            return
        self._position = int(self._frames_played / self.current_sample_rate * 1000)
        self.position_changed.emit(self._position)

        self._flush_callback_diagnostics()

        if (
            self._duration > 0
            and not self._play_count_recorded
            and (self._position / self._duration) >= PLAY_COUNT_THRESHOLD
            and not self._has_reached_threshold
        ):
            self._has_reached_threshold = True
            self._increment_play_count()

    def _increment_play_count(self):
        """Write an incremented play count to DB in background thread."""
        if not self.current_file or self._play_count_recorded:
            return

        # Store current file path locally for thread safety
        current_path = self.current_file

        def _update_db():
            try:
                track = self.controller.get.get_entity_object(
                    "Track", track_file_path=str(current_path)
                )
                if track and getattr(track, "track_id", None):
                    new_count = (getattr(track, "play_count", 0) or 0) + 1
                    self.controller.update.update_entity(
                        "Track",
                        track.track_id,
                        play_count=new_count,
                        last_listened_date=datetime.now(),
                    )
                    # Emit signal back to main thread
                    self.play_count_updated.emit(current_path, new_count)
                    logger.info(f"Play count → {new_count}: {current_path.name}")
            except (SQLAlchemyError, RuntimeError) as exc:
                logger.error(f"Play count update error: {exc}")

        self._play_count_recorded = True  # Mark as recorded immediately
        threading.Thread(target=_update_db, daemon=True, name="PlayCountUpdate").start()
