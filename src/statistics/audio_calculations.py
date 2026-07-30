"""
AudioCalculations — pure math on a single audio file.

Loads audio, runs every metric, then releases memory.
"""

import warnings

import acoustid
import numpy as np
from pydub import AudioSegment
from pydub.exceptions import PydubException
from scipy import signal
from scipy.ndimage import uniform_filter1d

from src.core.logger_config import logger

warnings.filterwarnings("ignore")

# Must match gain_calculator.py's REPLAYGAIN_REFERENCE_LUFS — both sides
# assume track_gain is a ReplayGain-style adjustment relative to this reference.
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
    * No metric calls another metric that requires a full STFT — run_all()
      computes each distinct (segment, nperseg) STFT once and passes it into
      every calculate_* method that needs it via an optional `stft` param
      (same pattern as the existing bpm=/centroid=/mode= passthroughs).
      Each method still computes its own if called standalone.
    """

    def __init__(self, audio_file_path: str):
        self.audio_file_path = audio_file_path
        self._audio: AudioSegment | None = None
        self.samples: np.ndarray | None = None  # shape: (channels, n_samples)
        self.sr: int | None = None
        self._loaded = False
        self._mono_cache: np.ndarray | None = None

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
        except (OSError, ValueError, PydubException) as e:
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
        self._mono_cache = None
        self._loaded = False

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _mono(self) -> np.ndarray:
        """Return a mono float32 array. Computed once per file and reused —
        this is called by nearly every metric below."""
        self._ensure_loaded()
        if self._mono_cache is None:
            if self.samples.shape[0] == 1:
                self._mono_cache = self.samples[0]
            else:
                self._mono_cache = np.mean(self.samples, axis=0)
        return self._mono_cache

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
        """Return (frequencies, magnitude_matrix) for one STFT call.

        Callers that need the same (segment, nperseg) STFT in more than one
        place should compute it once and pass it through explicitly (see the
        `stft=` params below and how run_all() wires them up) rather than
        relying on this method to cache — a cache keyed on id(audio) can't
        actually hit across calls since _segment() returns a fresh array view
        each time.
        """
        f, _, Zxx = signal.stft(audio, fs=self.sr, window="hann", nperseg=nperseg)
        return f, np.abs(Zxx)

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

            # Same windows as range(0, len(mono) - window, window) — one
            # reshape + per-row RMS instead of a Python loop.
            n_windows = len(range(0, len(mono) - window, window))
            if n_windows == 0:
                return 0.0
            chunks = mono[: n_windows * window].reshape(n_windows, window)
            rms_per_window = np.sqrt(np.mean(chunks**2, axis=1))
            rms_per_window = rms_per_window[rms_per_window > 1e-9]

            if len(rms_per_window) == 0:
                return 0.0

            rms_values = (20.0 * np.log10(rms_per_window)).tolist()

            # Mean of the top 70% of windows (ignores silent gaps)
            rms_values.sort(reverse=True)
            top = rms_values[: max(1, int(len(rms_values) * 0.7))]
            integrated_loudness = float(np.mean(top))
            return float(np.clip(REFERENCE_LUFS - integrated_loudness, -20.0, 20.0))

        except ValueError as e:
            logger.error(f"Track gain calculation failed: {e}")
            return 0.0

    def calculate_track_peak(self) -> float:
        """True peak amplitude, 0–1 scale."""
        if not self._ensure_loaded():
            return 0.0
        try:
            return float(np.max(np.abs(self.samples)))
        except ValueError as e:
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

        except ValueError as e:
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

    def calculate_spectral_flatness(
        self, stft: tuple[np.ndarray, np.ndarray] | None = None
    ) -> float:
        """Spectral flatness (Wiener entropy) of the track, 0 (tonal) – 1
        (noise-like).  See _flatness_from_magnitude() for the formula.

        `stft` may be passed in by a caller that already computed the 20s/
        nperseg=4096 STFT (e.g. run_all(), shared with calculate_acousticness)
        to avoid redoing it.
        """
        if not self._ensure_loaded():
            return 0.2
        try:
            if stft is not None:
                _, mag = stft
            else:
                mono = self._mono()
                seg = self._segment(mono, 20.0)
                _, mag = self._stft_magnitude(seg, nperseg=4096)
            return self._flatness_from_magnitude(mag)
        except ValueError as e:
            logger.error(f"Spectral flatness calculation failed: {e}")
            return 0.2

    def calculate_spectral_flux(
        self, stft: tuple[np.ndarray, np.ndarray] | None = None
    ) -> float:
        """
        Mean frame-to-frame change in the (energy-normalised) magnitude
        spectrum.  Low = static/sustained timbre (drones, pads); high =
        rapidly changing timbre (busy arrangements, percussive material).

        `stft` may be passed in by a caller that already computed the 30s/
        nperseg=2048 STFT (e.g. run_all()) to avoid redoing it.
        """
        if not self._ensure_loaded():
            return 0.1
        try:
            if stft is not None:
                _, mag = stft
            else:
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

        except ValueError as e:
            logger.error(f"Spectral flux calculation failed: {e}")
            return 0.1

    def calculate_spectral_centroid(
        self, stft: tuple[np.ndarray, np.ndarray] | None = None
    ) -> float:
        """
        Frequency-weighted mean of the spectrum — perceptual 'brightness'.
        Median across frames for robustness against transient spikes.
        Returns Hz.

        `stft` may be passed in by a caller that already computed the 30s/
        nperseg=2048 STFT (e.g. run_all()) to avoid redoing it.
        """
        if not self._ensure_loaded():
            return 2000.0
        try:
            if stft is not None:
                f, mag = stft
            else:
                mono = self._mono()
                seg = self._segment(mono, 30.0)
                f, mag = self._stft_magnitude(seg, nperseg=2048)

            total = mag.sum(axis=0)
            nonzero = total > 1e-8
            if not np.any(nonzero):
                return 2000.0

            centroid_frames = (f[:, None] * mag).sum(axis=0)[nonzero] / total[nonzero]
            return float(np.median(centroid_frames))
        except ValueError as e:
            logger.error(f"Spectral centroid failed: {e}")
            return 2000.0

    def calculate_spectral_rolloff(
        self, stft: tuple[np.ndarray, np.ndarray] | None = None
    ) -> float:
        """
        Frequency below which 85 % of spectral energy is contained.
        Median across frames. Returns Hz.

        `stft` may be passed in by a caller that already computed the 30s/
        nperseg=2048 STFT (e.g. run_all()) to avoid redoing it.
        """
        if not self._ensure_loaded():
            return 8000.0
        try:
            if stft is not None:
                f, mag = stft
            else:
                mono = self._mono()
                seg = self._segment(mono, 30.0)
                f, mag = self._stft_magnitude(seg, nperseg=2048)

            totals = mag.sum(axis=0)
            valid = totals >= 1e-8
            if not np.any(valid):
                return 8000.0

            # Same per-frame result as np.searchsorted(cumsum, 0.85*total)
            # (leftmost index where cumsum reaches the threshold), but done
            # as one vectorised pass across all frames instead of a Python
            # loop over each one.
            cumsum = np.cumsum(mag, axis=0)
            thresholds = 0.85 * totals
            idx = np.argmax(cumsum >= thresholds[None, :], axis=0)
            rolloffs = f[idx[valid]]

            return float(np.median(rolloffs)) if len(rolloffs) else 8000.0
        except ValueError as e:
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

            chunks = mono[: n_blocks * block].reshape(n_blocks, block)
            rms_per_block = np.sqrt(np.mean(chunks**2, axis=1))
            peak_per_block = np.max(np.abs(chunks), axis=1)
            valid = rms_per_block > 1e-9
            rms_blocks = rms_per_block[valid]
            peak_blocks = peak_per_block[valid]

            if len(rms_blocks) == 0:
                return 12.0

            rms_avg = np.mean(rms_blocks)
            peak_max = np.max(peak_blocks)

            dr = 20.0 * np.log10(peak_max / (rms_avg + 1e-9))
            return float(np.clip(dr, 4.0, 30.0))

        except ValueError as e:
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

        except ValueError as e:
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

        except ValueError as e:
            logger.error(f"MS energy ratio calculation failed: {e}")
            return 0.0

    def calculate_channel_coherence(self) -> float:
        """
        Inter-channel coherence — how similar the left and right channels
        are, via magnitude-squared coherence (Welch's method), weighted by
        where the track's energy actually lives.  0 = unrelated channels,
        1 = identical.  Mono tracks are trivially self-coherent → 1.0.
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

            # An unweighted mean over frequency bins gives equal say to the
            # near-silent bins between spectral peaks/formants — those are
            # dominated by uncorrelated dither/quantisation noise floor and
            # are trivially incoherent, which drags the average down even
            # for tracks whose actual musical content is near-identical
            # between channels. Weight each bin by the power present there
            # so the program material (not the noise floor) sets the score.
            _, pxx = signal.welch(l, fs=self.sr, nperseg=nperseg)
            _, pyy = signal.welch(r, fs=self.sr, nperseg=nperseg)
            weight = pxx + pyy
            if weight.sum() < 1e-12:
                return 1.0

            weighted_coherence = float(np.sum(cxy * weight) / weight.sum())
            return float(np.clip(weighted_coherence, 0.0, 1.0))

        except ValueError as e:
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

            # Normalise diff by local envelope level
            normalised = diff / (smooth_env[:-1] + 1e-9)

            # Find actual onset peaks — real transients are rare events, not
            # the top 5 % of all frames, so a blanket percentile mostly picks
            # up noise instead of attacks.
            peaks, props = signal.find_peaks(
                normalised, height=0.05, distance=max(1, int(frame_sr * 0.05))
            )
            if len(peaks) == 0:
                return 0.0

            transient_score = float(np.mean(props["peak_heights"]))

            # Clip to [0, 1] — values above ~2.0 are uncommon
            return float(np.clip(transient_score * 0.5, 0.0, 1.0))

        except ValueError as e:
            logger.error(f"Transient strength calculation failed: {e}")
            return 0.1

    # ------------------------------------------------------------------
    # Derived perceptual metrics
    # ------------------------------------------------------------------

    def calculate_energy(
        self, stft: tuple[np.ndarray, np.ndarray] | None = None
    ) -> float:
        """
        Perceptual energy: blend of integrated loudness and spectral brightness.
        0 = quiet/flat, 1 = loud and bright.

        `stft` may be passed in by a caller that already computed the 30s/
        nperseg=2048 STFT (e.g. run_all()) to avoid redoing it.
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

            f, mag = stft if stft is not None else self._stft_magnitude(seg, nperseg=2048)
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

        except ValueError as e:
            logger.error(f"Energy calculation failed: {e}")
            return 0.5

    def calculate_danceability(
        self,
        bpm: float | None = None,
        stft: tuple[np.ndarray, np.ndarray] | None = None,
    ) -> float:
        """
        Danceability based on three independently-normalised factors:
          - Beat strength (variance in sub-200 Hz energy over time)
          - Spectral balance (strong bass + mids, not top-heavy)
          - Tempo proximity to dance range 90–140 BPM

        `bpm` may be passed in by a caller that already computed it (e.g.
        run_all()) to avoid redoing the onset-envelope/autocorrelation work.
        `stft` likewise skips redoing the 30s/nperseg=2048 STFT.
        """
        if not self._ensure_loaded():
            return 0.5
        try:
            if stft is not None:
                f, mag = stft
            else:
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
            if bpm is None:
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

        except ValueError as e:
            logger.error(f"Danceability calculation failed: {e}")
            return 0.5

    def calculate_acousticness(
        self,
        centroid: float | None = None,
        stft: tuple[np.ndarray, np.ndarray] | None = None,
    ) -> float:
        """
        Estimates how acoustic (vs. electronic) the recording sounds.

        Acoustic signals tend to have:
          - Strong harmonic content (low spectral flatness)
          - Energy concentrated below 5 kHz
          - Lower spectral centroid

        `centroid` may be passed in by a caller that already computed it
        (e.g. run_all()) to avoid redoing the STFT. `stft` likewise skips
        redoing the 20s/nperseg=4096 STFT (shared with
        calculate_spectral_flatness in run_all()).
        """
        if not self._ensure_loaded():
            return 0.5
        try:
            if stft is not None:
                f, mag = stft
            else:
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
            if centroid is None:
                centroid = self.calculate_spectral_centroid()
            centroid_factor = float(np.clip(1.0 - centroid / 6000.0, 0.0, 1.0))

            return float(
                np.clip(
                    tonality * 0.5 + freq_factor * 0.3 + centroid_factor * 0.2, 0.0, 1.0
                )
            )

        except ValueError as e:
            logger.error(f"Acousticness calculation failed: {e}")
            return 0.5

    def calculate_liveness(
        self, stft: tuple[np.ndarray, np.ndarray] | None = None
    ) -> float:
        """
        Estimates the probability of a live recording.

        Live recordings tend to have a persistent, diffuse (noise-like)
        room/crowd presence that never fully drops away, even in quiet
        passages — whereas studio recordings can reach much closer to true
        silence between notes.

        This is a heuristic — confidence is inherently limited without
        a labelled training set.

        `stft` may be passed in by a caller that already computed the 30s/
        nperseg=2048 STFT (e.g. run_all()) to avoid redoing it.
        """
        if not self._ensure_loaded():
            return 0.2
        try:
            if stft is not None:
                f, mag = stft
            else:
                mono = self._mono()
                seg = self._segment(mono, 30.0)
                f, mag = self._stft_magnitude(seg, nperseg=2048)

            eps = 1e-9

            # High-frequency *noise-like-ness*, not raw level. Bright, tonal
            # content (cymbals, synth shimmer, sibilance) is common in
            # polished studio mixes and was previously mistaken for diffuse
            # room/crowd noise just because it sits above 8 kHz. Spectral
            # flatness tells true broadband noise (high flatness) apart from
            # tonal energy (low flatness) in that band — but flatness alone
            # is degenerate when the band is near-silent (a uniform noise
            # floor of all-eps values is technically "flat" too), so gate it
            # by how much real energy is actually up there.
            noise_mask = f > 8000
            if np.any(noise_mask):
                hf = mag[noise_mask]
                level_ratio = np.mean(hf) / (np.mean(mag) + eps)
                flatness = self._flatness_from_magnitude(hf)
                noise_factor = float(np.clip(level_ratio * 3.0, 0.0, 1.0)) * flatness
            else:
                noise_factor = 0.0

            # Noise-floor persistence: how elevated the quietest frames are
            # relative to the loudest. A live recording's crowd/room tone
            # never truly drops out, so quiet passages stay close to loud
            # ones; a studio recording can fall toward true silence between
            # phrases. (Previously this measured overall loudness variance,
            # which just tracked the song's arrangement/dynamics — a *good*,
            # dynamic studio mix scored as more "live" than a constant wall
            # of crowd noise.)
            frame_totals = np.mean(mag, axis=0)
            order = np.argsort(frame_totals)
            tenth = max(1, len(order) // 10)
            quiet = frame_totals[order[:tenth]]
            loud = frame_totals[order[-tenth:]]
            floor_ratio = np.mean(quiet) / (np.mean(loud) + eps)
            variance_factor = float(np.clip(floor_ratio * 3.0, 0.0, 1.0))

            return float(
                np.clip(noise_factor * 0.55 + variance_factor * 0.45, 0.0, 1.0)
            )

        except ValueError as e:
            logger.error(f"Liveness calculation failed: {e}")
            return 0.2

    def calculate_valence(
        self,
        bpm: float | None = None,
        mode: str | None = None,
        key_confidence: float | None = None,
        centroid: float | None = None,
    ) -> float:
        """
        Musical 'positiveness'.  This is the hardest metric to calculate
        without ML — we use mode (major/minor) as the primary signal,
        weighted with tempo and brightness.

        `bpm`, `mode`, `key_confidence` and `centroid` may be passed in by a
        caller that already computed them (e.g. run_all()) to avoid redoing
        the BPM/key/centroid analysis.

        Note: the confidence of this metric is inherently limited.
        """
        if not self._ensure_loaded():
            return 0.5
        try:
            if bpm is None:
                bpm, _ = self.calculate_bpm()
            if mode is None or key_confidence is None:
                _, mode, key_confidence = self.calculate_key()
            if centroid is None:
                centroid = self.calculate_spectral_centroid()

            # Mode: major → positive, minor → negative
            # Weight by key confidence so uncertain detections approach 0.5
            mode_score = 0.65 if mode == "major" else 0.35
            mode_factor = 0.5 + (mode_score - 0.5) * key_confidence

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

        except (ValueError, TypeError) as e:
            logger.error(f"Valence calculation failed: {e}")
            return 0.5

    def _soundstage_stability_score(self) -> float:
        """
        How much the L/R coherence changes over the course of the track —
        a proxy for whether the stereo image is actually alive (instruments
        entering/leaving the field, natural mic bleed shifting as the
        arrangement changes) or static (one blanket pan/widening decision
        applied uniformly throughout, as in a collapsed or artificially
        "fake-stereo" mono source).

        Deliberately not a target value for coherence *itself* — a fixed
        "ideal" coherence number conflates mixing style (some genuinely
        great, tightly-centered mixes are highly mono-compatible) with
        fidelity. Whether the image moves over time is a more
        genre-independent tell than any single coherence value.
        """
        stereo = self._stereo_window(seconds=60.0)
        if stereo is None:
            return 0.5
        l, r = stereo  # noqa: E741
        win = int(self.sr * 2.0)
        n_windows = len(l) // win
        if n_windows < 6:
            return 0.5

        coherences = []
        for i in range(n_windows):
            lw = l[i * win : (i + 1) * win]
            rw = r[i * win : (i + 1) * win]
            if np.sqrt(np.mean(lw**2)) < 1e-4:
                continue  # near-silent window carries no imaging information
            nperseg = min(1024, len(lw))
            _, cxy = signal.coherence(lw, rw, fs=self.sr, nperseg=nperseg)
            coherences.append(float(np.mean(cxy)))

        if len(coherences) < 6:
            return 0.5

        # A mean coherence this close to 1.0 means the channels are
        # essentially the same signal — a mono source stored in a stereo
        # container, not a real (if narrow or dated) stereo recording. That
        # has no image to be static *or* alive, so it shouldn't be scored
        # as a collapsed one; treat it the same as a declared-mono file.
        # This can't accidentally exempt a genuinely bad static-image track:
        # a coherence sequence bounded in [0, 1] needs real spread (std of
        # at least ~0.15) to earn full soundstage credit below, and a mean
        # this high leaves no room for that much spread to exist.
        mean_coherence = float(np.mean(coherences))
        if mean_coherence >= 0.97:
            return 0.5

        variability = float(np.std(coherences))
        # Calibrated against 14 hand-labelled reference tracks: a genuinely
        # static/collapsed image measured 0.02-0.07 variability; an audibly
        # alive one measured 0.10-0.29.
        return float(np.clip((variability - 0.03) / 0.12, 0.0, 1.0))

    def _dynamic_range_gate(self) -> float:
        """
        Only penalizes masters that have actually been brickwalled/blown
        out (DR <= 5 dB); a loud-but-not-crushed master still gets full
        credit — loudness is a mastering choice, not a fidelity defect.
        """
        dr = self.calculate_dynamic_range()
        return float(np.clip((dr - 5.0) / 5.0, 0.0, 1.0))

    def _noise_floor_gate(self) -> float:
        """
        How far below the track's own peak the quietest real moments sit.
        A clean digital source can reach near-total digital silence in
        gaps; tape hiss, generation loss, or vinyl surface noise puts a
        floor under how quiet "quiet" can get. Normalised to the track's
        own peak — not an absolute dB level — so this doesn't just
        re-measure overall mastering loudness (that's what dynamic range
        is for); it isolates whether the *quiet moments specifically* are
        genuinely quiet.
        """
        mono = self._mono()
        frame = max(1, int(self.sr * 0.075))  # ~75ms frames
        n_frames = len(mono) // frame
        if n_frames < 20:
            return 0.5
        chunks = mono[: n_frames * frame].reshape(n_frames, frame)
        frame_rms = np.sqrt(np.mean(chunks**2, axis=1))
        # Exclude digital-silence padding (leading/trailing zeros, encoder
        # gaps) — that's not the recording's noise floor, it's silence.
        active = frame_rms[frame_rms > 1e-6]
        if len(active) < 20:
            return 0.5

        quiet_threshold = np.percentile(active, 10)
        quiet = active[active <= quiet_threshold]
        noise_floor_db = 20.0 * np.log10(np.mean(quiet) + 1e-9)

        peak = np.max(np.abs(mono))
        peak_db = 20.0 * np.log10(peak + 1e-9)
        headroom_db = peak_db - noise_floor_db

        # This is a gate, not a graded axis: it should give full credit to
        # the typical/default case and only punish tracks that actually
        # have a noise problem, not spread every track across the whole
        # 0-1 range. Across the accumulated reference set, headroom ran
        # ~19-45dB; the tracks with real, audible noise-floor problems
        # (loudness-war-compressed masters) clustered distinctly under
        # 22dB, while everything else — including tracks with only
        # middling headroom — sounded clean. So the ramp is narrow and
        # placed at the bottom: full credit by ~26dB, zero by 18dB. Known
        # limitation: this measures headroom *relative to peak*, not
        # persistence — a source with real but quiet hiss (e.g. a 1930s
        # recording) can still clear this gate since the hiss itself isn't
        # loud, just constant.
        return float(np.clip((headroom_db - 18.0) / 8.0, 0.0, 1.0))

    def _long_term_average_spectrum(
        self, seconds: float = 90.0
    ) -> tuple[np.ndarray, np.ndarray] | tuple[None, None]:
        """
        Long-term average magnitude spectrum in dB, over a large
        representative segment. Shared by the lossy-cutoff gate and the
        spectral-shape axis so this relatively expensive large-window STFT
        only runs once per track.
        """
        try:
            mono = self._mono()
            seg = self._segment(mono, seconds)
            f, mag = self._stft_magnitude(seg, nperseg=8192)
            ltas = np.mean(mag, axis=1)
            ltas_db = 20.0 * np.log10(ltas + 1e-9)
            return f, ltas_db
        except ValueError as e:
            logger.error(f"Long-term average spectrum calculation failed: {e}")
            return None, None

    def _lossy_cutoff_gate(
        self, ltas: tuple[np.ndarray, np.ndarray] | None = None
    ) -> float:
        """
        Detects a lossy-codec-style hard frequency cutoff — a common,
        easy-to-miss defect in a library assembled from mixed sources over
        time (a FLAC can be an upsampled transcode of a lossy source).

        Two-part test, not just "where does content end": (1) find the
        lowest frequency where the spectrum drops to and stays at the
        noise floor, then (2) measure how abrupt that specific transition
        is. A codec cutoff is a near-brick-wall filter over a few hundred
        Hz; a naturally band-limited source (an instrument/voice with
        little real energy up there) fades out gradually over several kHz
        even if it ends around a similar frequency. Only a cutoff that is
        both suspiciously low *and* abrupt counts against a track — an
        earlier attempt that only looked at "steepest slope anywhere" was
        triggered by ordinary mastering EQ and had to be discarded.

        `ltas` may be passed in by a caller that already computed it (e.g.
        calculate_audiophile_score(), which shares it with
        _spectral_shape_score()) to avoid redoing the large-window STFT.
        """
        f, ltas_db = ltas if ltas is not None else self._long_term_average_spectrum()
        if f is None:
            return 0.5

        nyquist = self.sr / 2.0
        search_mask = (f >= 3000) & (f <= nyquist - 200)
        if not np.any(search_mask):
            return 1.0
        fs = f[search_mask]
        sb = ltas_db[search_mask]

        smooth = uniform_filter1d(sb, size=9)
        tail = smooth[-max(5, len(smooth) // 10) :]
        noise_floor = float(np.percentile(tail, 25))

        above_floor = smooth > (noise_floor + 6.0)
        if not np.any(above_floor):
            return 1.0
        last_content_idx = int(np.where(above_floor)[0][-1])
        cutoff_freq = float(fs[last_content_idx])

        bin_hz = float(fs[1] - fs[0]) if len(fs) > 1 else 1.0
        win_bins = max(1, int(1000.0 / bin_hz))
        start_idx = max(0, last_content_idx - win_bins)
        transition_drop = float(smooth[start_idx] - smooth[last_content_idx])

        # Only penalize a cutoff that is both low (well below Nyquist) and
        # abrupt (a steep, brick-wall-like transition into it). Provisional
        # thresholds, pending validation.
        lowness = float(np.clip((nyquist - 2000.0 - cutoff_freq) / 6000.0, 0.0, 1.0))
        abruptness = float(np.clip((transition_drop - 8.0) / 12.0, 0.0, 1.0))
        penalty = lowness * abruptness
        return float(np.clip(1.0 - penalty, 0.0, 1.0))

    def _spectral_shape_score(
        self, ltas: tuple[np.ndarray, np.ndarray] | None = None
    ) -> float:
        """
        Reuses the long-term average spectrum computed for the lossy-cutoff
        gate. Smooths it heavily to get the track's overall spectral
        envelope, then scores how large the residual (actual minus smooth
        trend) is: a small residual means a smooth, natural-looking
        envelope; a large one means resonant peaks/notches — bad EQ, phase
        cancellation from a mono-summing issue, room modes.

        The most speculative construct in this file — unlike the gates
        above, it isn't grounded in an established engineering technique,
        it's a new hypothesis about what a "well-shaped" spectrum looks
        like. Needs validation against the reference track set before its
        weight in the final score is trusted.

        `ltas` may be passed in by a caller that already computed it (e.g.
        calculate_audiophile_score(), which shares it with
        _lossy_cutoff_gate()) to avoid redoing the large-window STFT.
        """
        f, ltas_db = ltas if ltas is not None else self._long_term_average_spectrum()
        if f is None:
            return 0.5

        band_mask = (f >= 60) & (f <= self.sr / 2.0 - 500)
        if not np.any(band_mask):
            return 0.5
        band = ltas_db[band_mask]

        trend = uniform_filter1d(band, size=max(3, len(band) // 20))
        residual = band - trend
        roughness = float(np.std(residual))

        # Provisional, pending validation against the reference set.
        return float(np.clip(1.0 - (roughness - 1.5) / 3.5, 0.0, 1.0))

    def calculate_audiophile_score(self) -> float:
        """
        Heuristic listening-pleasure estimate built from two kinds of
        signal, combined multiplicatively:

        Quality axes — genuine positive achievement, weighted:
          - Soundstage stability (60%): does the stereo image move over
            time, or is it static/collapsed? See _soundstage_stability_score.
          - Spectral shape (40%): does the overall frequency envelope look
            smooth and natural, or lumpy/resonant (bad EQ, phase
            cancellation, room modes)? See _spectral_shape_score. The most
            speculative construct here — flagged for validation there.

        Gates — presuppositions. No credit for merely not failing; each
        only ever multiplies the score down when a real defect is present:
          - Clipping: fraction of samples at or beyond digital full scale —
            the clearest single signal of harsh, distorted audio.
          - Dynamic range: only penalizes masters actually brickwalled/
            blown out (DR <= 5 dB). See _dynamic_range_gate.
          - Noise floor: do the quietest moments reach real digital
            silence, or is there persistent hiss/generation-loss noise
            even below the peak? See _noise_floor_gate.
          - Lossy cutoff: an abrupt, suspiciously-low frequency cutoff —
            the signature of a lossy-compressed source, even inside a
            lossless container ("fake FLAC"). See _lossy_cutoff_gate.

        Returns 0–1 (1 = pure audiophile bliss, 0 = a bluetooth speaker in
        a blender). Unlike most other heuristics in this module, failing to
        load or analyze the audio returns 0.0 rather than an optimistic
        default — a track we can't even read is not a hifi track.

        v3 note: v2's "spectral naturalness" term (HF-energy level and
        temporal persistence) was retired after eight separate attempts to
        fix or replace it failed to find a logically sound footing — it
        variously penalized legitimately bright, well-regarded productions
        (Steely Dan's "Aja", Michael Jackson's "Billie Jean") and missed an
        intentional, static, well-produced texture (Pink Floyd's "Money"
        cash-register loop) for the same underlying reason: it measured a
        proxy that happened to correlate with "sounds bad" on one small
        reference set, not a real acoustic cause. This version separates
        the score into presuppositions (gates, penalty-only) and genuine
        achievement axes (weighted, quality-only) instead of blending
        everything into one flat weighted pool. An inter-sample ("true
        peak") clipping check was tried alongside the sample-level one and
        dropped again — it reliably measured how hot a track was mastered,
        but that turned out not to track perceived quality on the
        reference set at all (it penalized "Money for Nothing", one of the
        reference paragons, as hard as a known-loud "bad" master), so it
        wasn't earning its real compute cost (4x oversampling was the
        single most expensive step in this entire score).
        """
        if not self._ensure_loaded():
            return 0.0
        try:
            FLOOR = 0.05

            # LTAS is shared between the lossy-cutoff gate and the
            # spectral-shape axis — compute it once, it's the most
            # expensive step here (a large-window STFT).
            ltas = self._long_term_average_spectrum()

            # --- Quality axes ---
            soundstage_score = self._soundstage_stability_score()
            spectral_shape_score = self._spectral_shape_score(ltas=ltas)
            quality = (
                max(soundstage_score, FLOOR) ** 0.6
                * max(spectral_shape_score, FLOOR) ** 0.4
            )

            # --- Gates ---
            # Clipping: fraction of samples at or beyond 99.9% of full
            # scale — the clearest single signal of harsh, distorted audio.
            clipped_fraction = np.mean(np.abs(self.samples) >= 0.999)
            clip_penalty = float(np.clip(clipped_fraction * 200.0, 0.0, 1.0))
            clip_gate = 1.0 - clip_penalty

            dr_gate = self._dynamic_range_gate()
            noise_floor_gate = self._noise_floor_gate()
            lossy_cutoff_gate = self._lossy_cutoff_gate(ltas=ltas)

            gates = (
                max(clip_gate, FLOOR)
                * max(dr_gate, FLOOR)
                * max(noise_floor_gate, FLOOR)
                * max(lossy_cutoff_gate, FLOOR)
            )

            return float(np.clip(quality * gates, 0.0, 1.0))

        except (ValueError, ZeroDivisionError) as e:
            logger.error(f"Audiophile score calculation failed: {e}")
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
        except acoustid.AcoustidError as e:
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
            # Six methods below independently need the same centre-30s/
            # nperseg=2048 STFT, and two more need the same 20s/nperseg=4096
            # STFT — compute each exactly once here and pass it through
            # instead of letting every method redo its own full STFT.
            mono = self._mono()
            stft_30_2048 = self._stft_magnitude(self._segment(mono, 30.0), nperseg=2048)
            stft_20_4096 = self._stft_magnitude(self._segment(mono, 20.0), nperseg=4096)

            bpm, tempo_confidence = self.calculate_bpm()
            key, mode, key_confidence = self.calculate_key()
            track_gain = self.calculate_track_gain()
            track_peak = self.calculate_track_peak()
            spectral_centroid = self.calculate_spectral_centroid(stft=stft_30_2048)
            spectral_rolloff = self.calculate_spectral_rolloff(stft=stft_30_2048)
            dynamic_range = self.calculate_dynamic_range()
            stereo_width = self.calculate_stereo_width()
            ms_energy_ratio = self.calculate_ms_energy_ratio()
            channel_coherence = self.calculate_channel_coherence()
            crest_factor = self.calculate_crest_factor()
            spectral_flatness = self.calculate_spectral_flatness(stft=stft_20_4096)
            spectral_flux = self.calculate_spectral_flux(stft=stft_30_2048)
            transient_strength = self.calculate_transient_strength()
            energy = self.calculate_energy(stft=stft_30_2048)
            danceability = self.calculate_danceability(bpm=bpm, stft=stft_30_2048)
            acousticness = self.calculate_acousticness(
                centroid=spectral_centroid, stft=stft_20_4096
            )
            liveness = self.calculate_liveness(stft=stft_30_2048)
            valence = self.calculate_valence(
                bpm=bpm,
                mode=mode,
                key_confidence=key_confidence,
                centroid=spectral_centroid,
            )
            audiophile_score = self.calculate_audiophile_score()
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
                "audiophile_score": audiophile_score,
                "acoustid_fingerprint": acoustid_fingerprint,
                "acoustid_fingerprint_duration": acoustid_fingerprint_duration,
            }
        except Exception:
            # Intentional broad boundary catch: run_all() is the batch-processing
            # entry point for one file (see class docstring) — every calculate_*
            # method already narrows and self-defaults, so this only exists to
            # guarantee a single bad file can never crash the whole batch.
            logger.exception(f"run_all failed for {self.audio_file_path}")
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
            "audiophile_score": 0.0,
            "acoustid_fingerprint": None,
            "acoustid_fingerprint_duration": None,
        }
