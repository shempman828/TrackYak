"""Modern High-Performance Equalizer with Scipy SOS Filters"""

from typing import Dict

import numpy as np
from PySide6.QtCore import QObject, Signal
from scipy import signal

from config_setup import Config
from logger_config import logger


class EqualizerUtility(QObject):
    """Modern equalizer using scipy's second-order sections for optimal performance."""

    equalizer_changed = Signal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.enabled = False
        self.sample_rate = 44100

        # 10-band professional EQ with meaningful frequency distribution
        self.bands = [
            {
                "freq": 32,
                "label": "Sub Bass",
                "gain": 0.0,
                "range": (-12.0, 12.0),
                "Q": 1.0,
            },
            {
                "freq": 64,
                "label": "Bass",
                "gain": 0.0,
                "range": (-12.0, 12.0),
                "Q": 1.0,
            },
            {
                "freq": 125,
                "label": "Low Mid",
                "gain": 0.0,
                "range": (-12.0, 12.0),
                "Q": 1.0,
            },
            {
                "freq": 250,
                "label": "Mid",
                "gain": 0.0,
                "range": (-12.0, 12.0),
                "Q": 1.0,
            },
            {
                "freq": 500,
                "label": "Mid",
                "gain": 0.0,
                "range": (-12.0, 12.0),
                "Q": 1.0,
            },
            {
                "freq": 1000,
                "label": "Upper Mid",
                "gain": 0.0,
                "range": (-12.0, 12.0),
                "Q": 1.0,
            },
            {
                "freq": 2000,
                "label": "Presence",
                "gain": 0.0,
                "range": (-12.0, 12.0),
                "Q": 1.0,
            },
            {
                "freq": 4000,
                "label": "Brilliance",
                "gain": 0.0,
                "range": (-12.0, 12.0),
                "Q": 1.0,
            },
            {
                "freq": 8000,
                "label": "High",
                "gain": 0.0,
                "range": (-12.0, 12.0),
                "Q": 1.0,
            },
            {
                "freq": 16000,
                "label": "Air",
                "gain": 0.0,
                "range": (-12.0, 12.0),
                "Q": 1.0,
            },
        ]

        # Professional presets
        self.presets = {
            "Flat": [0.0] * 10,
            "Bass Boost": [6.0, 4.0, 2.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
            "Treble Boost": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 2.0, 4.0, 6.0, 6.0],
            "Rock": [4.0, 3.0, 2.0, 0.0, 1.0, 2.0, 3.0, 2.0, 1.0, 0.0],
            "Pop": [3.0, 2.0, 1.0, 0.0, 2.0, 3.0, 2.0, 1.0, 0.0, 0.0],
            "Jazz": [2.0, 2.0, 1.0, 0.0, 0.0, 1.0, 2.0, 3.0, 2.0, 1.0],
            "Classical": [0.0, 0.0, 0.0, 0.0, 1.0, 2.0, 3.0, 4.0, 3.0, 2.0],
            "Electronic": [6.0, 4.0, 2.0, 0.0, 1.0, 2.0, 1.0, 0.0, 2.0, 3.0],
            "Hip Hop": [6.0, 5.0, 3.0, 0.0, -1.0, 0.0, 1.0, 2.0, 3.0, 4.0],
            "Acoustic": [0.0, 1.0, 2.0, 2.0, 1.0, 0.0, -1.0, -1.0, 0.0, 0.0],
            "Vocal Boost": [0.0, 0.0, -1.0, 0.0, 2.0, 4.0, 4.0, 3.0, 1.0, 0.0],
            "Dance": [5.0, 4.0, 2.0, 0.0, 0.0, 1.0, 2.0, 3.0, 4.0, 5.0],
        }

        self.preset_sos_cache: Dict[str, np.ndarray] = {}
        # Combined SOS filter for optimal performance
        self.combined_sos = None
        self.filter_zi = None  # Filter initial conditions
        self._precompute_preset_sos()
        self.update_combined_filter()

    def _precompute_preset_sos(self):
        """Compute SOS filters for all presets and store in cache."""
        for preset_name, gains in self.presets.items():
            sos_list = []
            for i, gain_db in enumerate(gains):
                if abs(gain_db) < 0.1:
                    continue
                band = self.bands[i]
                freq, Q = band["freq"], band["Q"]
                A = 10 ** (gain_db / 40)
                w0 = 2 * np.pi * freq / self.sample_rate
                alpha = np.sin(w0) / (2 * Q)
                cos_w0 = np.cos(w0)

                b0 = 1 + alpha * A
                b1 = -2 * cos_w0
                b2 = 1 - alpha * A
                a0 = 1 + alpha / A
                a1 = -2 * cos_w0
                a2 = 1 - alpha / A

                sos = signal.tf2sos([b0, b1, b2], [a0, a1, a2])
                sos_list.append(sos)

            if sos_list:
                self.preset_sos_cache[preset_name] = np.vstack(sos_list)
            else:
                self.preset_sos_cache[preset_name] = np.array(
                    [[1.0, 0.0, 0.0, 1.0, 0.0, 0.0]]
                )

    def set_enabled(self, enabled: bool):
        """Enable or disable the equalizer."""
        old_state = self.enabled
        self.enabled = enabled

        if enabled and not old_state:
            self.reset_filter_states()
            logger.info("Equalizer enabled")
        elif not enabled and old_state:
            logger.info("Equalizer disabled")

        self.equalizer_changed.emit(self.get_settings())

    def set_sample_rate(self, sample_rate: int):
        """Set the sample rate and update filters."""
        if self.sample_rate != sample_rate:
            self.sample_rate = sample_rate
            self.update_combined_filter()
            self.reset_filter_states()
            logger.debug(f"Equalizer sample rate set to {sample_rate}Hz")

    def set_band_gain(self, band_index: int, gain: float):
        """Set gain for a specific band."""
        if 0 <= band_index < len(self.bands):
            min_gain, max_gain = self.bands[band_index]["range"]
            self.bands[band_index]["gain"] = max(min_gain, min(gain, max_gain))
            self.update_combined_filter()
            self.reset_filter_states()
            self.equalizer_changed.emit(self.get_settings())

    def update_combined_filter(self):
        """Create a single combined SOS filter from all bands for maximum performance."""
        try:
            all_sos = []

            for band in self.bands:
                gain_db = band["gain"]
                if abs(gain_db) < 0.1:
                    continue  # skip near-zero gain

                freq = band["freq"]
                Q = band["Q"]

                # Peaking EQ coefficients
                A = 10 ** (gain_db / 40)  # amplitude factor
                w0 = 2 * np.pi * freq / self.sample_rate
                alpha = np.sin(w0) / (2 * Q)
                cos_w0 = np.cos(w0)

                b0 = 1 + alpha * A
                b1 = -2 * cos_w0
                b2 = 1 - alpha * A
                a0 = 1 + alpha / A
                a1 = -2 * cos_w0
                a2 = 1 - alpha / A

                # Convert to SOS (second-order section)
                sos = signal.tf2sos([b0, b1, b2], [a0, a1, a2])
                all_sos.append(sos)

            if all_sos:
                # Combine all SOS filters into one array
                self.combined_sos = np.vstack(all_sos)
            else:
                # Identity filter if all gains are zero
                self.combined_sos = np.array([[1.0, 0.0, 0.0, 1.0, 0.0, 0.0]])

            # Reset filter states
            self.reset_filter_states()

        except Exception as e:
            logger.error(f"Error updating combined filter: {e}")
            # Fallback to identity filter
            self.combined_sos = np.array([[1.0, 0.0, 0.0, 1.0, 0.0, 0.0]])
            self.reset_filter_states()

    def reset_filter_states(self):
        """Reset filter states for new processing."""
        self.filter_zi = None

    def process_audio(self, audio_data: np.ndarray) -> np.ndarray:
        """High-performance audio processing using combined SOS filter."""
        if not self.enabled or audio_data is None or len(audio_data) == 0:
            return audio_data

        # Bypass for very small buffers to prevent underflow
        if len(audio_data) < 32:
            return audio_data

        try:
            # Ensure proper data type
            if audio_data.dtype != np.float32:
                input_data = audio_data.astype(np.float32)
            else:
                input_data = audio_data.copy()

            # Apply the combined filter
            if self.combined_sos is not None:
                if self.filter_zi is not None:
                    # Use sosfilt with initial conditions
                    processed_data, self.filter_zi = signal.sosfilt(
                        self.combined_sos, input_data, axis=0, zi=self.filter_zi
                    )
                else:
                    # First time - initialize filter states
                    self.filter_zi = signal.sosfilt_zi(self.combined_sos)
                    # For multi-channel audio, we need to expand zi to match channels
                    if len(input_data.shape) > 1:
                        n_channels = input_data.shape[1]
                        self.filter_zi = np.tile(
                            self.filter_zi[:, :, np.newaxis], (1, 1, n_channels)
                        )
                    processed_data, self.filter_zi = signal.sosfilt(
                        self.combined_sos, input_data, axis=0, zi=self.filter_zi
                    )
            else:
                processed_data = input_data

            return processed_data

        except Exception as e:
            logger.error(f"Equalizer processing error: {e}")
            # Return original audio to prevent playback issues
            return audio_data

    def set_preset(self, preset_name: str):
        """Apply a preset using cached SOS if available."""
        if preset_name not in self.presets:
            logger.warning(f"Preset '{preset_name}' not found")
            return

        gains = self.presets[preset_name]
        for i, gain in enumerate(gains):
            if i < len(self.bands):
                self.bands[i]["gain"] = gain

        # Use cached SOS filter
        if preset_name in self.preset_sos_cache:
            self.combined_sos = self.preset_sos_cache[preset_name]
        else:
            # Fallback: compute on the fly
            self.update_combined_filter()

        self.reset_filter_states()
        self.equalizer_changed.emit(self.get_settings())
        logger.info(f"Applied equalizer preset: {preset_name}")

    def reset(self):
        """Reset all bands to zero gain (flat)."""
        for band in self.bands:
            band["gain"] = 0.0
        self.update_combined_filter()
        self.reset_filter_states()
        self.equalizer_changed.emit(self.get_settings())
        logger.info("Equalizer reset to flat")

    def get_settings(self) -> Dict:
        """Get current equalizer settings."""
        return {
            "enabled": self.enabled,
            "bands": [band.copy() for band in self.bands],
            "presets": list(self.presets.keys()),
        }

    def get_band_gain(self, band_index: int) -> float:
        """Get gain for a specific band."""
        if 0 <= band_index < len(self.bands):
            return self.bands[band_index]["gain"]
        return 0.0

    def is_enabled(self) -> bool:
        """Check if equalizer is enabled."""
        return self.enabled

    def save_to_config(self, config: Config, preset_name: str = "Custom"):
        """Save current EQ settings to config"""
        band_gains = [band["gain"] for band in self.bands]
        config.save_equalizer_settings(self.enabled, band_gains, preset_name)
        logger.info(f"Equalizer settings saved to config: {preset_name}")

    def load_from_config(self, config: Config):
        """Load EQ settings from config"""
        enabled = config.get_equalizer_enabled()
        band_gains = config.get_equalizer_band_gains()

        # Apply settings
        self.set_enabled(enabled)
        for i, gain in enumerate(band_gains):
            if i < len(self.bands):
                self.set_band_gain(i, gain)

        # Update combined filter
        self.update_combined_filter()
        logger.info("Equalizer settings loaded from config")

    def get_band_gains(self):
        """Get all band gains as a list"""
        return [band["gain"] for band in self.bands]
