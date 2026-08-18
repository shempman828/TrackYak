"""
AudioRhythmMixin — tempo, key, and transient-attack analysis.

Expects the host class to provide: self._ensure_loaded(), self._mono(),
self._segment(), self.sr, self.samples.
"""

import numpy as np
from scipy import signal

from src.core.logger_config import logger


class AudioRhythmMixin:
    # ------------------------------------------------------------------
    # BPM  (onset-strength + multi-resolution autocorrelation)
    # ------------------------------------------------------------------

    def calculate_bpm(self) -> tuple[float, float]:
        """
        Estimate tempo using an onset-strength envelope and autocorrelation.

        This is more robust than raw waveform autocorrelation because the
        onset envelope captures rhythmic pulses without being confused by
        low-frequency content or silence.

        Returns (bpm, confidence) where confidence is 0–1.
        """
        if not self._ensure_loaded():
            return 120.0, 0.0

        try:
            mono = self._mono()
            # Use up to 60 s from the centre — long enough for stable tempo
            seg = self._segment(mono, 60.0)

            # --- 1. Compute onset-strength envelope ---
            # High-pass filter to suppress bass rumble
            nyq = self.sr / 2.0
            b, a = signal.butter(3, 80.0 / nyq, btype="high")
            filtered = signal.filtfilt(b, a, seg)

            # Rectified half-wave
            half_wave = np.maximum(filtered, 0.0)

            # Downsample to ~200 Hz for efficiency
            hop = max(1, self.sr // 200)
            frames = len(half_wave) // hop
            envelope = np.abs(half_wave[: frames * hop]).reshape(frames, hop).max(axis=1)
            envelope_sr = self.sr / hop  # sample rate of envelope

            # Smooth to extract rhythmic pulses
            smooth_win = max(3, int(envelope_sr * 0.05))
            envelope = np.convolve(
                envelope,
                np.hanning(smooth_win) / np.sum(np.hanning(smooth_win)),
                mode="same",
            )

            # --- 2. Autocorrelation over BPM range 40–240 ---
            min_lag = int(envelope_sr * 60.0 / 240.0)
            max_lag = int(envelope_sr * 60.0 / 40.0)

            if max_lag >= len(envelope):
                return 120.0, 0.1

            corr = signal.correlate(envelope, envelope, mode="full")
            corr = corr[len(corr) // 2 :]  # positive lags only
            corr_region = corr[min_lag:max_lag]

            if len(corr_region) == 0:
                return 120.0, 0.1

            # Normalise
            corr_region = corr_region / (corr[0] + 1e-8)

            peaks, props = signal.find_peaks(
                corr_region,
                height=0.1,
                distance=max(1, int(envelope_sr * 60.0 / 240.0)),
                prominence=0.05,
            )

            if len(peaks) == 0:
                return 120.0, 0.1

            # Pick the most prominent peak
            best = peaks[np.argmax(props["prominences"])]
            period_frames = best + min_lag
            bpm = 60.0 * envelope_sr / period_frames

            # --- 3. Octave correction (halve / double if outside 60–180 BPM) ---
            while bpm < 60.0:
                bpm *= 2.0
            while bpm > 180.0:
                bpm /= 2.0

            bpm = round(bpm, 1)
            confidence = float(
                min(props["prominences"][np.argmax(props["prominences"])] * 2.0, 1.0)
            )

            return bpm, confidence

        except (ValueError, ZeroDivisionError) as e:
            logger.error(f"BPM calculation failed: {e}")
            return 120.0, 0.0

    # ------------------------------------------------------------------
    # Musical key  (chromagram + Krumhansl-Schmuckler key profiles)
    # ------------------------------------------------------------------

    def calculate_key(self) -> tuple[str, str, float]:
        """
        Detect musical key using a chromagram correlated against the
        Krumhansl-Schmuckler key profiles — the standard musicology approach.

        Uses the centre 30 s to avoid key changes at intros/outros.

        Returns (key_name, mode, confidence).
        """
        if not self._ensure_loaded():
            return "C", "major", 0.0

        # Conventional enharmonic spelling per mode (circle-of-fifths preference),
        # not a fixed sharps-only table — e.g. "Ab major" rather than "G# major".
        MAJOR_KEY_NAMES = [
            "C", "Db", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B",
        ]
        MINOR_KEY_NAMES = [
            "C", "C#", "D", "Eb", "E", "F", "F#", "G", "G#", "A", "Bb", "B",
        ]

        # Krumhansl-Schmuckler profiles (normalised later)
        KS_MAJOR = np.array(
            [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88]
        )
        KS_MINOR = np.array(
            [6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17]
        )
        KS_MAJOR = KS_MAJOR / KS_MAJOR.mean()
        KS_MINOR = KS_MINOR / KS_MINOR.mean()

        try:
            mono = self._mono()
            seg = self._segment(mono, 30.0)

            # Use large FFT window for good frequency resolution
            f, mag = self._stft_magnitude(seg, nperseg=8192)

            A4 = 440.0
            chromagram = np.zeros(12)

            for i in range(12):
                # Centre frequency of this chroma bin across all octaves (C1–C8)
                bin_energy = 0.0
                for octave in range(1, 9):
                    centre = A4 * 2.0 ** ((i - 9) / 12.0 + (octave - 4))
                    lower = centre * 2 ** (-0.5 / 12.0)
                    upper = centre * 2 ** (0.5 / 12.0)
                    mask = (f >= lower) & (f < upper)
                    if np.any(mask):
                        bin_energy += np.mean(mag[mask, :])
                chromagram[i] = bin_energy

            # Normalise
            total = chromagram.sum()
            if total < 1e-8:
                return "C", "major", 0.0
            chromagram /= total

            # Correlate rotated chromagram against both profiles for all 12 keys
            best_corr = -np.inf
            best_key = 0
            best_mode = "major"

            for shift in range(12):
                rotated = np.roll(chromagram, shift)
                # Use Pearson correlation (mean-centred)
                r_maj = np.corrcoef(rotated, KS_MAJOR)[0, 1]
                r_min = np.corrcoef(rotated, KS_MINOR)[0, 1]

                # Note: shift=0 means the chromagram's root is at C
                # rolling by +shift means key = MAJOR_KEY_NAMES[shift] / MINOR_KEY_NAMES[shift]
                if not np.isnan(r_maj) and r_maj > best_corr:
                    best_corr = r_maj
                    best_key = shift
                    best_mode = "major"
                if not np.isnan(r_min) and r_min > best_corr:
                    best_corr = r_min
                    best_key = shift
                    best_mode = "minor"

            confidence = float(np.clip((best_corr + 1.0) / 2.0, 0.0, 1.0))
            names = MAJOR_KEY_NAMES if best_mode == "major" else MINOR_KEY_NAMES
            return names[best_key], best_mode, confidence

        except ValueError as e:
            logger.error(f"Key calculation failed: {e}")
            return "C", "major", 0.0

    # ------------------------------------------------------------------
    # Transient strength
    # ------------------------------------------------------------------

    def calculate_transient_strength(self) -> float:
        """
        Ratio of sharp onset events relative to the overall envelope.
        Range 0–1; percussive/electronic music → high, ambient → low.
        """
        if not self._ensure_loaded():
            return 0.1
        try:
            mono = self._mono()
            seg = self._segment(mono, 30.0)

            # High-pass to focus on attack transients, not sustained bass
            nyq = self.sr / 2.0
            b, a = signal.butter(2, 200.0 / nyq, btype="high")
            filtered = signal.filtfilt(b, a, seg)

            # Analytic envelope via Hilbert
            envelope = np.abs(signal.hilbert(filtered))

            # Downsample to a frame rate matched to attack timescales (~500 Hz),
            # keeping the peak of each hop so sharp attacks survive. A per-sample
            # derivative of the raw envelope structurally caps the normalised
            # rate of change at ~1/window_samples, drowning out genuine
            # transients regardless of source material.
            hop = max(1, int(self.sr / 500))
            n_frames = len(envelope) // hop
            if n_frames < 2:
                return 0.1
            frame_env = envelope[: n_frames * hop].reshape(n_frames, hop).max(axis=1)
            frame_sr = self.sr / hop

            # Light smoothing (~4 ms) to denoise without erasing attacks
            win = max(3, int(frame_sr * 0.004))
            smooth_env = np.convolve(frame_env, np.ones(win) / win, mode="same")

            if smooth_env.max() < 1e-9:
                return 0.0

            # First derivative of smoothed envelope → onset events
            diff = np.diff(smooth_env)
            diff = np.maximum(diff, 0.0)  # only rises

            # How much do the sharpest onsets stand out above the track's
            # *typical* rate of change? Dividing each rise by the envelope
            # level immediately preceding it (as before) rewards tracks with
            # near-silent gaps between hits and structurally deflates
            # anything with continuously loud backing — a live mix, a
            # compressed master, reverb tails — even when its actual attacks
            # are sharp (empirically: live/pop/acoustic tracks all collapsed
            # to ~0.12-0.14 regardless of real transient content, while an
            # isolated funk slap-bass groove with silent gaps scored
            # correctly). A crest-factor-style ratio of the extreme rises to
            # the RMS of all rises is scale-invariant and doesn't depend on
            # what else was playing right before the hit.
            rms_rise = float(np.sqrt(np.mean(diff**2)))
            if rms_rise < 1e-9:
                return 0.0
            peak_rise = float(np.percentile(diff, 99.9))
            crest = peak_rise / rms_rise

            # Calibrated against real library tracks: ambient/pad material
            # sits ~4-5, most percussive/rock/electronic material ~6-8,
            # isolated funk/slap-bass transients (e.g. Hancock's
            # "Chameleon") ~9.
            return float(np.clip((crest - 4.0) / 5.5, 0.0, 1.0))

        except ValueError as e:
            logger.error(f"Transient strength calculation failed: {e}")
            return 0.1
