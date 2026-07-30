"""
player_gain.py — volume and ReplayGain-based normalization for MusicPlayer.

Expects the host class to provide: self.volume_level, self.volume_changed
signal, self._volume_save_timer, self.normalization_enabled,
self.normalization_target, self.controller, self.current_file.
"""

from src.core.config_setup import app_config
from src.core.logger_config import logger
from src.player.gain_calculator import calculate_gain_factor


class PlayerGainMixin:
    """Volume control plus ReplayGain/normalization gain calculation."""

    def set_volume(self, value: int):
        new_val = max(0, min(100, value))
        if new_val != self.volume_level:
            self.volume_level = new_val
            self.volume_changed.emit(self.volume_level)
            self._volume_save_timer.start(500)

    def increase_volume(self):
        self.set_volume(self.volume_level + 5)

    def decrease_volume(self):
        self.set_volume(self.volume_level - 5)

    def enable_normalization(self, enabled: bool):
        self.normalization_enabled = enabled
        # Recalculate gain for current track
        self._gain_factor = self._calculate_gain_factor()
        logger.info(f"Normalization {'on' if enabled else 'off'}")

    def set_normalization_target(self, target_lufs: float):
        self.normalization_target = max(-50.0, min(-5.0, target_lufs))
        self._gain_factor = self._calculate_gain_factor()
        logger.info(f"Normalization target: {self.normalization_target} LUFS")

    def _calculate_gain_factor(self) -> float:
        """
        Returns the multiplier applied to every audio chunk.
        Uses ReplayGain from the DB when available; falls back to 1.0.
        """
        return calculate_gain_factor(
            self.controller,
            self.current_file,
            self.normalization_enabled,
            self.normalization_target,
        )

    def _save_volume_to_config(self):
        try:
            app_config.set_volume(self.volume_level)
            app_config.save()
        except OSError as exc:
            logger.error(f"Volume save error: {exc}")
