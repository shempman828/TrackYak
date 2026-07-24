"""
AudioCalculations — pure math on a single audio file.

Loads audio, runs every metric, then releases memory.
"""

import warnings

import acoustid
import numpy as np
from pydub import AudioSegment
from scipy import signal

from src.core.logger_config import logger

warnings.filterwarnings("ignore")

# Must match player_util.py's REPLAYGAIN_REFERENCE_LUFS — both sides assume
# track_gain is a ReplayGain-style adjustment relative to this reference.
REFERENCE_LUFS = -18.0


class AudioCalculations:
    """
    All audio maths for a single file.

    Design principles
    -----------------
    * Audio is loaded lazily and released explicitly via release() once all
      metrics have been gathered.  This keeps peak memory low when processing
      large queues.
    * Every public calculate_* method is self-contained and handles its own
      exceptions, returning a safe default so a single bad file can't crash
      the whole batch.
    * Segment lengths are chosen per-metric: short for spectral snapshots,
      longer where temporal structure matters (BPM, key).
    * No metric calls another metric that requires a full STFT — intermediate
      results are shared where practical to avoid duplicate transforms.
    """

    def __init__(self, audio_file_path: str):
        self.audio_file_path = audio_file_path
        self._audio: AudioSegment | None = None
        self.samples: np.ndarray | None = None  # shape: (channels, n_samples)
        self.sr: int | None = None
        self._loaded = False

        # Cached intermediate results so we don't recompute full-track STFT
        # more than once.
        self._mono_stft_cache: dict = {}  # nperseg -> (f, magnitude)

    # ------------------------------------------------------------------
    # Load / release
    # ------------------------------------------------------------------

    def _load(self) -> bool:
        if self._loaded:
            return True
        try:
            self._audio = AudioSegment.from_file(self.audio_file_path)
            self.sr = self._audio.frame_rate

            raw = np.array(self._audio.get_array_of_samples())

            if self._audio.channels == 2:
                raw = raw.reshape((-1, 2))
                self.samples = raw.T.astype(np.float32)
            else:
                self.samples = raw.reshape((1, -1)).astype(np.float32)

            # Normalise to [-1.0, 1.0]
            bit_depth = self._audio.sample_width * 8
            self.samples /= float(2 ** (bit_depth - 1))
            self.samples = np.clip(self.samples, -1.0, 1.0)

            self._loaded = True
            logger.debug(
                f"AudioCalculations: loaded SR={self.sr} "
                f"ch={self.samples.shape[0]} "
                f"samples={self.samples.shape[1]}"
            )
            return True
        except Exception as e:
            logger.error(f"AudioCalculations: cannot load {self.audio_file_path} — {e}")
            # Provide silent fallback so callers still get safe defaults
            self.sr = 44100
            self.samples = np.zeros((1, self.sr), dtype=np.float32)
            self._loaded = True
            return False

    def _ensure_loaded(self) -> bool:
        return self._loaded or self._load()

    def release(self):
        """Explicitly free memory after all calculations are done."""
        self._audio = None
        self.samples = None
        self._mono_stft_cache.clear()
        self._loaded = False

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _mono(self) -> np.ndarray:
        """Return a mono float32 array."""
        self._ensure_loaded()
        if self.samples.shape[0] == 1:
            return self.samples[0]
        return np.mean(self.samples, axis=0)

    def _segment(self, audio: np.ndarray, max_seconds: float) -> np.ndarray:
        """Return at most max_seconds worth of samples from the centre of the
        track — avoids relying on intros/outros which can skew key/BPM."""
        max_samples = int(self.sr * max_seconds)
        n = len(audio)
        if n <= max_samples:
            return audio
        start = (n - max_samples) // 2
        return audio[start : start + max_samples]

    def _stft_magnitude(
        self, audio: np.ndarray, nperseg: int = 2048
    ) -> tuple[np.ndarray, np.ndarray]:
        """
        Return (frequencies, magnitude_matrix).
        Results for a given nperseg are cached for the lifetime of this object.
        The key includes a hash of the audio pointer so different segments
        produce different cache entries.
        """
        cache_key = (id(audio), nperseg)
        if cache_key not in self._mono_stft_cache:
            f, _, Zxx = signal.stft(audio, fs=self.sr, window="hann", nperseg=nperseg)
            self._mono_stft_cache[cache_key] = (f, np.abs(Zxx))
        return self._mono_stft_cache[cache_key]

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
            envelope = np.array(
                [
                    np.max(np.abs(half_wave[i * hop : (i + 1) * hop]))
                    for i in range(frames)
                ]
            )
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

        except Exception as e:
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

        except Exception as e:
            logger.error(f"Key calculation failed: {e}")
            return "C", "major", 0.0

    # ------------------------------------------------------------------
    # Gain & peak  (full-track, no sampling)
    # ------------------------------------------------------------------

    def calculate_track_gain(self) -> float:
        """
        ReplayGain-style adjustment (dB) needed to bring the track to
        REFERENCE_LUFS, derived from integrated RMS loudness across the
        whole track (400 ms windows, EBU R-128 influenced, averaged
        logarithmically). Positive = boost a quiet track, negative =
        attenuate a loud one — same sign convention as embedded
        REPLAYGAIN_TRACK_GAIN tags, since both feed the same DB column.
        """
        if not self._ensure_loaded():
            return 0.0
        try:
            mono = self._mono()
            window = int(self.sr * 0.4)
            if window == 0:
                return 0.0

            rms_values = []
            for start in range(0, len(mono) - window, window):
                chunk = mono[start : start + window]
                rms = np.sqrt(np.mean(chunk**2))
                if rms > 1e-9:
                    rms_values.append(20.0 * np.log10(rms))

            if not rms_values:
                return 0.0

            # Mean of the top 70% of windows (ignores silent gaps)
            rms_values.sort(reverse=True)
            top = rms_values[: max(1, int(len(rms_values) * 0.7))]
            integrated_loudness = float(np.mean(top))
            return float(np.clip(REFERENCE_LUFS - integrated_loudness, -20.0, 20.0))

        except Exception as e:
            logger.error(f"Track gain calculation failed: {e}")
            return 0.0

    def calculate_track_peak(self) -> float:
        """True peak amplitude, 0–1 scale."""
        if not self._ensure_loaded():
            return 0.0
        try:
            return float(np.max(np.abs(self.samples)))
        except Exception as e:
            logger.error(f"Track peak calculation failed: {e}")
            return 0.0

    def calculate_crest_factor(self) -> float:
        """
        Peak-to-loudness ratio in dB: 20*log10(true_peak / RMS), computed
        over the full track.  High crest factor = dynamic/uncompressed
        material; low crest factor = heavily limited/compressed material
        sitting close to its average loudness.
        """
        if not self._ensure_loaded():
            return 12.0
        try:
            mono = self._mono()
            rms = np.sqrt(np.mean(mono**2))
            peak = np.max(np.abs(mono))
            if rms < 1e-9 or peak < 1e-9:
                return 0.0

            crest = 20.0 * np.log10(peak / rms)
            return float(np.clip(crest, 0.0, 30.0))

        except Exception as e:
            logger.error(f"Crest factor calculation failed: {e}")
            return 12.0

    # ------------------------------------------------------------------
    # Spectral features
    # ------------------------------------------------------------------

    @staticmethod
    def _flatness_from_magnitude(mag: np.ndarray) -> float:
        """Wiener entropy: ratio of the geometric mean to the arithmetic
        mean of the FFT magnitude, averaged across frames.  0 = purely
        tonal/harmonic, 1 = noise-like/flat spectrum."""
        eps = 1e-9
        geo_mean = np.exp(np.mean(np.log(mag + eps), axis=0))
        arith_mean = np.mean(mag, axis=0) + eps
        return float(np.clip(np.mean(geo_mean / arith_mean), 0.0, 1.0))

    def calculate_spectral_flatness(self) -> float:
        """Spectral flatness (Wiener entropy) of the track, 0 (tonal) – 1
        (noise-like).  See _flatness_from_magnitude() for the formula."""
        if not self._ensure_loaded():
            return 0.2
        try:
            mono = self._mono()
            seg = self._segment(mono, 20.0)
            _, mag = self._stft_magnitude(seg, nperseg=4096)
            return self._flatness_from_magnitude(mag)
        except Exception as e:
            logger.error(f"Spectral flatness calculation failed: {e}")
            return 0.2

    def calculate_spectral_flux(self) -> float:
        """
        Mean frame-to-frame change in the (energy-normalised) magnitude
        spectrum.  Low = static/sustained timbre (drones, pads); high =
        rapidly changing timbre (busy arrangements, percussive material).
        """
        if not self._ensure_loaded():
            return 0.1
        try:
            mono = self._mono()
            seg = self._segment(mono, 30.0)
            _, mag = self._stft_magnitude(seg, nperseg=2048)
            if mag.shape[1] < 2:
                return 0.1

            norms = np.linalg.norm(mag, axis=0, keepdims=True)
            norms[norms < 1e-8] = 1.0
            normalised = mag / norms

            diff = np.diff(normalised, axis=1)
            flux_per_frame = np.sqrt(np.mean(diff**2, axis=0))

            # Empirical scaling to spread typical values across [0, 1],
            # matching the fudge-factor approach used elsewhere in this file.
            return float(np.clip(np.mean(flux_per_frame) * 5.0, 0.0, 1.0))

        except Exception as e:
            logger.error(f"Spectral flux calculation failed: {e}")
            return 0.1

    def calculate_spectral_centroid(self) -> float:
        """
        Frequency-weighted mean of the spectrum — perceptual 'brightness'.
        Median across frames for robustness against transient spikes.
        Returns Hz.
        """
        if not self._ensure_loaded():
            return 2000.0
        try:
            mono = self._mono()
            seg = self._segment(mono, 30.0)
            f, mag = self._stft_magnitude(seg, nperseg=2048)

            total = mag.sum(axis=0)
            nonzero = total > 1e-8
            if not np.any(nonzero):
                return 2000.0

            centroid_frames = (f[:, None] * mag).sum(axis=0)[nonzero] / total[nonzero]
            return float(np.median(centroid_frames))
        except Exception as e:
            logger.error(f"Spectral centroid failed: {e}")
            return 2000.0

    def calculate_spectral_rolloff(self) -> float:
        """
        Frequency below which 85 % of spectral energy is contained.
        Median across frames. Returns Hz.
        """
        if not self._ensure_loaded():
            return 8000.0
        try:
            mono = self._mono()
            seg = self._segment(mono, 30.0)
            f, mag = self._stft_magnitude(seg, nperseg=2048)

            rolloffs = []
            for i in range(mag.shape[1]):
                frame = mag[:, i]
                total = frame.sum()
                if total < 1e-8:
                    continue
                cumsum = np.cumsum(frame)
                idx = np.searchsorted(cumsum, 0.85 * total)
                if idx < len(f):
                    rolloffs.append(f[idx])

            return float(np.median(rolloffs)) if rolloffs else 8000.0
        except Exception as e:
            logger.error(f"Spectral rolloff failed: {e}")
            return 8000.0

    # ------------------------------------------------------------------
    # Dynamic range  (DR14-style: peak vs. RMS per segment)
    # ------------------------------------------------------------------

    def calculate_dynamic_range(self) -> float:
        """
        Estimates dynamic range using short-term RMS blocks similar to the
        DR14 methodology:  DR ≈ 20*log10(peak / RMS_avg)

        Returned value is clamped to [4, 30] dB — the realistic music range.
        Higher is better (less compression).
        """
        if not self._ensure_loaded():
            return 12.0
        try:
            mono = self._mono()
            block = int(self.sr * 3.0)  # 3-second blocks, as per DR14
            n_blocks = len(mono) // block
            if n_blocks < 2:
                return 12.0

            rms_blocks = []
            peak_blocks = []
            for i in range(n_blocks):
                chunk = mono[i * block : (i + 1) * block]
                rms = np.sqrt(np.mean(chunk**2))
                pk = np.max(np.abs(chunk))
                if rms > 1e-9:
                    rms_blocks.append(rms)
                    peak_blocks.append(pk)

            if not rms_blocks:
                return 12.0

            rms_avg = np.mean(rms_blocks)
            peak_max = np.max(peak_blocks)

            dr = 20.0 * np.log10(peak_max / (rms_avg + 1e-9))
            return float(np.clip(dr, 4.0, 30.0))

        except Exception as e:
            logger.error(f"Dynamic range calculation failed: {e}")
            return 12.0

    # ------------------------------------------------------------------
    # Stereo width  (mid/side analysis)
    # ------------------------------------------------------------------

    def _stereo_window(self, seconds: float = 20.0) -> tuple[np.ndarray, np.ndarray] | None:
        """Return (left, right) for a fixed window from the track start, or
        None if the track is mono."""
        if self.samples.shape[0] < 2:
            return None
        max_s = int(self.sr * seconds)
        l = self.samples[0, :max_s]  # noqa: E741
        r = self.samples[1, :max_s]
        return l, r

    def _mid_side(self, seconds: float = 20.0) -> tuple[np.ndarray, np.ndarray] | None:
        """Return (mid, side) signals for the same window used by
        calculate_stereo_width() and calculate_ms_energy_ratio(), or None
        if the track is mono."""
        stereo = self._stereo_window(seconds)
        if stereo is None:
            return None
        l, r = stereo  # noqa: E741
        return (l + r) / 2.0, (l - r) / 2.0

    def calculate_stereo_width(self) -> float:
        """
        Mid/side energy ratio.  Pure mono = 0.0, very wide stereo → 1.0.

        Formula:  width = RMS_side / (RMS_mid + RMS_side)
        Then scaled so mid-dominant stereo mixes land around 0.3–0.5 and
        genuinely wide mixes reach 0.7+.
        """
        if not self._ensure_loaded():
            return 0.5
        try:
            ms = self._mid_side()
            if ms is None:
                return 0.0
            mid, side = ms

            rms_mid = np.sqrt(np.mean(mid**2))
            rms_side = np.sqrt(np.mean(side**2))

            total = rms_mid + rms_side
            if total < 1e-9:
                return 0.0

            width = rms_side / total
            return float(np.clip(width, 0.0, 1.0))

        except Exception as e:
            logger.error(f"Stereo width calculation failed: {e}")
            return 0.5

    def calculate_ms_energy_ratio(self) -> float:
        """
        Raw side/mid RMS energy ratio — the un-normalised counterpart to
        calculate_stereo_width().  0 = mono/mid-only content, ~1 = side and
        mid roughly balanced, higher = side-heavy (largely out-of-phase)
        content.  Unlike stereo_width this is not clamped to [0, 1].
        """
        if not self._ensure_loaded():
            return 0.0
        try:
            ms = self._mid_side()
            if ms is None:
                return 0.0
            mid, side = ms

            rms_mid = np.sqrt(np.mean(mid**2))
            rms_side = np.sqrt(np.mean(side**2))

            if rms_mid < 1e-9:
                return 0.0

            return float(rms_side / rms_mid)

        except Exception as e:
            logger.error(f"MS energy ratio calculation failed: {e}")
            return 0.0

    def calculate_channel_coherence(self) -> float:
        """
        Inter-channel coherence — how similar the left and right channels
        are, via magnitude-squared coherence (Welch's method) averaged
        across the spectrum.  0 = unrelated channels, 1 = identical.
        Mono tracks are trivially self-coherent → 1.0.
        """
        if not self._ensure_loaded():
            return 1.0
        try:
            stereo = self._stereo_window()
            if stereo is None:
                return 1.0
            l, r = stereo  # noqa: E741
            if len(l) < 256:
                return 1.0

            nperseg = min(2048, len(l))
            _, cxy = signal.coherence(l, r, fs=self.sr, nperseg=nperseg)
            return float(np.clip(np.mean(cxy), 0.0, 1.0))

        except Exception as e:
            logger.error(f"Channel coherence calculation failed: {e}")
            return 1.0

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

            # Smooth envelope (10 ms)
            win = max(3, int(self.sr * 0.01))
            smooth_env = np.convolve(envelope, np.ones(win) / win, mode="same")

            # First derivative of smoothed envelope → onset events
            diff = np.diff(smooth_env)
            diff = np.maximum(diff, 0.0)  # only rises

            if smooth_env.max() < 1e-9:
                return 0.0

            # Normalise diff by overall envelope level
            normalised = diff / (smooth_env[:-1] + 1e-9)

            # Mean of the top 5 % of values → captures attack sharpness
            threshold = np.percentile(normalised, 95)
            transient_score = float(np.mean(normalised[normalised >= threshold]))

            # Clip to [0, 1] — values above ~0.5 are uncommon
            return float(np.clip(transient_score * 2.0, 0.0, 1.0))

        except Exception as e:
            logger.error(f"Transient strength calculation failed: {e}")
            return 0.1

    # ------------------------------------------------------------------
    # Derived perceptual metrics
    # ------------------------------------------------------------------

    def calculate_energy(self) -> float:
        """
        Perceptual energy: blend of integrated loudness and spectral brightness.
        0 = quiet/flat, 1 = loud and bright.
        """
        if not self._ensure_loaded():
            return 0.5
        try:
            mono = self._mono()
            seg = self._segment(mono, 30.0)

            rms = np.sqrt(np.mean(seg**2))
            # Map RMS: -40 dBFS → 0, -6 dBFS → 1
            rms_db = 20.0 * np.log10(rms + 1e-9)
            loudness_factor = np.clip((rms_db + 40.0) / 34.0, 0.0, 1.0)

            f, mag = self._stft_magnitude(seg, nperseg=2048)
            total_e = mag.sum()
            if total_e < 1e-8:
                brightness_factor = 0.0
            else:
                high_mask = f > 1000
                brightness_factor = float(
                    np.clip(mag[high_mask].sum() / total_e * 2.0, 0.0, 1.0)
                )

            return float(
                np.clip(loudness_factor * 0.6 + brightness_factor * 0.4, 0.0, 1.0)
            )

        except Exception as e:
            logger.error(f"Energy calculation failed: {e}")
            return 0.5

    def calculate_danceability(self) -> float:
        """
        Danceability based on three independently-normalised factors:
          - Beat strength (variance in sub-200 Hz energy over time)
          - Spectral balance (strong bass + mids, not top-heavy)
          - Tempo proximity to dance range 90–140 BPM
        """
        if not self._ensure_loaded():
            return 0.5
        try:
            mono = self._mono()
            seg = self._segment(mono, 30.0)
            f, mag = self._stft_magnitude(seg, nperseg=2048)

            # Beat strength
            bass_mask = f <= 200
            bass_time = np.mean(mag[bass_mask, :], axis=0)
            mean_bass = np.mean(bass_time)
            beat_strength = (
                (np.std(bass_time) / (mean_bass + 1e-8)) if mean_bass > 1e-8 else 0.0
            )
            beat_factor = float(np.clip(beat_strength / 1.5, 0.0, 1.0))

            # Spectral balance
            mid_mask = (f > 200) & (f <= 4000)
            high_mask = f > 4000
            total = np.mean(mag) + 1e-8
            bass_r = np.mean(mag[bass_mask]) / total
            mid_r = np.mean(mag[mid_mask]) / total
            high_r = np.mean(mag[high_mask]) / total
            # Ideal: bass ~0.35, mid ~0.45, high ~0.20
            balance = 1.0 - (
                abs(bass_r - 0.35) + abs(mid_r - 0.45) + abs(high_r - 0.20)
            )
            balance_factor = float(np.clip(balance, 0.0, 1.0))

            # Tempo
            bpm, _ = self.calculate_bpm()
            if 90 <= bpm <= 140:
                tempo_factor = 1.0
            elif 70 <= bpm < 90 or 140 < bpm <= 160:
                tempo_factor = 0.65
            else:
                tempo_factor = 0.25

            return float(
                np.clip(
                    beat_factor * 0.35 + balance_factor * 0.35 + tempo_factor * 0.30,
                    0.0,
                    1.0,
                )
            )

        except Exception as e:
            logger.error(f"Danceability calculation failed: {e}")
            return 0.5

    def calculate_acousticness(self) -> float:
        """
        Estimates how acoustic (vs. electronic) the recording sounds.

        Acoustic signals tend to have:
          - Strong harmonic content (low spectral flatness)
          - Energy concentrated below 5 kHz
          - Lower spectral centroid
        """
        if not self._ensure_loaded():
            return 0.5
        try:
            mono = self._mono()
            seg = self._segment(mono, 20.0)
            f, mag = self._stft_magnitude(seg, nperseg=4096)

            # 1. Spectral flatness per frame (Wiener entropy) — low = tonal = acoustic
            flatness = self._flatness_from_magnitude(mag)
            tonality = float(np.clip(1.0 - flatness * 5.0, 0.0, 1.0))

            # 2. Low-frequency energy dominance
            eps = 1e-9
            low_mask = f <= 5000
            total_e = mag.sum() + eps
            low_ratio = mag[low_mask].sum() / total_e
            freq_factor = float(np.clip(low_ratio * 1.2, 0.0, 1.0))

            # 3. Spectral centroid
            centroid = self.calculate_spectral_centroid()
            centroid_factor = float(np.clip(1.0 - centroid / 6000.0, 0.0, 1.0))

            return float(
                np.clip(
                    tonality * 0.5 + freq_factor * 0.3 + centroid_factor * 0.2, 0.0, 1.0
                )
            )

        except Exception as e:
            logger.error(f"Acousticness calculation failed: {e}")
            return 0.5

    def calculate_liveness(self) -> float:
        """
        Estimates the probability of a live recording.

        Live recordings tend to have more diffuse high-frequency noise
        (room reflections, crowd) and less perfectly consistent spectral
        distribution across time.

        This is a heuristic — confidence is inherently limited without
        a labelled training set.
        """
        if not self._ensure_loaded():
            return 0.2
        try:
            mono = self._mono()
            seg = self._segment(mono, 30.0)
            f, mag = self._stft_magnitude(seg, nperseg=2048)

            eps = 1e-9
            total = np.mean(mag) + eps

            # High-frequency noise ratio
            noise_mask = f > 8000
            noise_ratio = (
                np.mean(mag[noise_mask]) / total if np.any(noise_mask) else 0.0
            )
            noise_factor = float(np.clip(noise_ratio * 3.0, 0.0, 1.0))

            # Temporal spectral variance (how much the spectrum changes over time)
            frame_means = np.mean(mag, axis=0)
            temporal_var = np.std(frame_means) / (np.mean(frame_means) + eps)
            variance_factor = float(np.clip(temporal_var * 2.0, 0.0, 1.0))

            return float(
                np.clip(noise_factor * 0.55 + variance_factor * 0.45, 0.0, 1.0)
            )

        except Exception as e:
            logger.error(f"Liveness calculation failed: {e}")
            return 0.2

    def calculate_valence(self) -> float:
        """
        Musical 'positiveness'.  This is the hardest metric to calculate
        without ML — we use mode (major/minor) as the primary signal,
        weighted with tempo and brightness.

        Note: the confidence of this metric is inherently limited.
        """
        if not self._ensure_loaded():
            return 0.5
        try:
            bpm, _ = self.calculate_bpm()
            _, mode, key_conf = self.calculate_key()
            centroid = self.calculate_spectral_centroid()

            # Mode: major → positive, minor → negative
            # Weight by key confidence so uncertain detections approach 0.5
            mode_score = 0.65 if mode == "major" else 0.35
            mode_factor = 0.5 + (mode_score - 0.5) * key_conf

            # Tempo: 90–140 BPM correlates with positive/energetic music
            if bpm < 60:
                tempo_factor = 0.25
            elif bpm < 90:
                tempo_factor = 0.45
            elif bpm <= 140:
                tempo_factor = 0.75
            elif bpm <= 170:
                tempo_factor = 0.60
            else:
                tempo_factor = 0.45

            # Brightness: brighter timbres tend to sound happier
            brightness = float(np.clip(centroid / 5000.0, 0.0, 1.0))

            return float(
                np.clip(
                    mode_factor * 0.50 + tempo_factor * 0.30 + brightness * 0.20,
                    0.0,
                    1.0,
                )
            )

        except Exception as e:
            logger.error(f"Valence calculation failed: {e}")
            return 0.5

    def calculate_fidelity_score(self) -> float:
        """
        Heuristic audio fidelity estimate combining:
          - Spectral extension (is full-range high-frequency content present,
            or has it been squashed by a lossy codec / tiny driver?)
          - Clipping / distortion detection (values at or very near ±1.0)
          - Dynamic range (has the track been brickwalled/blown out, or
            does it still have headroom?)

        Returns 0–1 (1 = pure audiophile bliss, 0 = a bluetooth speaker in
        a blender). Unlike most other heuristics in this module, failing to
        load or analyze the audio returns 0.0 rather than an optimistic
        default — a track we can't even read is not a hifi track.
        """
        if not self._ensure_loaded():
            return 0.0
        try:
            # High frequency extension: a 128 kbps MP3 or a cheap Bluetooth
            # codec rolls off steeply above ~16 kHz; a clean lossless source
            # carries real content out past 20 kHz.
            mono = self._mono()
            seg = self._segment(mono, 10.0)
            f, mag = self._stft_magnitude(seg, nperseg=4096)
            total_e = mag.sum() + 1e-9
            hf_mask = f > 14000
            hf_ratio = mag[hf_mask].sum() / total_e if np.any(hf_mask) else 0.0
            hf_score = float(np.clip(hf_ratio * 20.0, 0.0, 1.0))

            # Clipping: fraction of samples at or beyond 99.9 % of full scale —
            # the clearest single signal of harsh, distorted audio.
            clipped = np.mean(np.abs(self.samples) >= 0.999)
            clip_penalty = float(np.clip(clipped * 200.0, 0.0, 1.0))
            clip_score = 1.0 - clip_penalty

            # Dynamic range: only penalize masters that have actually been
            # brickwalled/blown out (DR <= 5 dB). A normally-mastered, even
            # fairly loud, lossless track (DR ~10 dB+) still gets full
            # credit — loudness is a mastering choice, not a fidelity defect.
            dr = self.calculate_dynamic_range()
            dr_score = float(np.clip((dr - 5.0) / 9.0, 0.0, 1.0))

            return float(
                np.clip(hf_score * 0.40 + clip_score * 0.35 + dr_score * 0.25, 0.0, 1.0)
            )

        except Exception as e:
            logger.error(f"Fidelity score calculation failed: {e}")
            return 0.0

    def calculate_fingerprint(self) -> tuple:
        """
        Compute a chromaprint/AcoustID audio fingerprint for duplicate
        detection by audio content rather than tags.

        Decodes the file itself via chromaprint's own path (fpcalc/audioread)
        instead of reusing self.samples — chromaprint needs a specific PCM
        format and this avoids any risk of a mismatch with the pydub-decoded
        buffer used by the other metrics. Capped at the first 120s of audio,
        matching AcoustID convention and keeping comparisons fast.

        Returns (fingerprint: str | None, duration: int | None). A failure
        here (corrupt file, missing native chromaprint library) is not fatal
        to the rest of the analysis — it just leaves fingerprint fields
        unset, same as any other calculate_* method's safe-default handling.
        """
        try:
            duration, fingerprint = acoustid.fingerprint_file(
                self.audio_file_path, maxlength=120
            )
            return fingerprint.decode() if isinstance(fingerprint, bytes) else fingerprint, duration
        except Exception as e:
            logger.warning(
                f"Fingerprint calculation failed for {self.audio_file_path}: {e}"
            )
            return None, None

    # ------------------------------------------------------------------
    # Run all metrics in one call
    # ------------------------------------------------------------------

    def run_all(self) -> dict:
        """
        Execute every calculation and return a metadata dict ready to be
        written to the database.  Audio is released from memory afterwards.
        """
        if not self._ensure_loaded():
            return self._safe_defaults()

        try:
            # --- Metrics that share intermediate STFT results ---
            bpm, tempo_confidence = self.calculate_bpm()
            key, mode, key_confidence = self.calculate_key()
            track_gain = self.calculate_track_gain()
            track_peak = self.calculate_track_peak()
            spectral_centroid = self.calculate_spectral_centroid()
            spectral_rolloff = self.calculate_spectral_rolloff()
            dynamic_range = self.calculate_dynamic_range()
            stereo_width = self.calculate_stereo_width()
            ms_energy_ratio = self.calculate_ms_energy_ratio()
            channel_coherence = self.calculate_channel_coherence()
            crest_factor = self.calculate_crest_factor()
            spectral_flatness = self.calculate_spectral_flatness()
            spectral_flux = self.calculate_spectral_flux()
            transient_strength = self.calculate_transient_strength()
            energy = self.calculate_energy()
            danceability = self.calculate_danceability()
            acousticness = self.calculate_acousticness()
            liveness = self.calculate_liveness()
            valence = self.calculate_valence()
            fidelity_score = self.calculate_fidelity_score()
            acoustid_fingerprint, acoustid_fingerprint_duration = (
                self.calculate_fingerprint()
            )

            return {
                "bpm": bpm,
                "tempo_confidence": tempo_confidence,
                "key": key,
                "mode": mode,
                "key_confidence": key_confidence,
                "track_gain": track_gain,
                "track_peak": track_peak,
                "crest_factor": crest_factor,
                "spectral_centroid": spectral_centroid,
                "spectral_rolloff": spectral_rolloff,
                "spectral_flatness": spectral_flatness,
                "spectral_flux": spectral_flux,
                "dynamic_range": dynamic_range,
                "stereo_width": stereo_width,
                "ms_energy_ratio": ms_energy_ratio,
                "channel_coherence": channel_coherence,
                "transient_strength": transient_strength,
                "energy": energy,
                "danceability": danceability,
                "acousticness": acousticness,
                "liveness": liveness,
                "valence": valence,
                "fidelity_score": fidelity_score,
                "acoustid_fingerprint": acoustid_fingerprint,
                "acoustid_fingerprint_duration": acoustid_fingerprint_duration,
            }
        except Exception as e:
            logger.error(
                f"run_all failed for {self.audio_file_path}: {e}", exc_info=True
            )
            return self._safe_defaults()
        finally:
            self.release()

    @staticmethod
    def _safe_defaults() -> dict:
        return {
            "bpm": 120.0,
            "tempo_confidence": 0.0,
            "key": "C",
            "mode": "major",
            "key_confidence": 0.0,
            "track_gain": 0.0,
            "track_peak": 0.0,
            "crest_factor": 12.0,
            "spectral_centroid": 2000.0,
            "spectral_rolloff": 8000.0,
            "spectral_flatness": 0.2,
            "spectral_flux": 0.1,
            "dynamic_range": 12.0,
            "stereo_width": 0.5,
            "ms_energy_ratio": 0.0,
            "channel_coherence": 1.0,
            "transient_strength": 0.1,
            "energy": 0.5,
            "danceability": 0.5,
            "acousticness": 0.5,
            "liveness": 0.2,
            "valence": 0.5,
            "fidelity_score": 0.0,
            "acoustid_fingerprint": None,
            "acoustid_fingerprint_duration": None,
        }
