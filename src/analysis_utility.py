"""
analysis_utility.py

Three responsibilities, cleanly separated:

  1. AudioCalculations  — pure math on a single audio file.
                          Loads audio, runs every metric, then releases memory.
  2. AnalysisCache      — thin JSON wrapper at config/analysis_cache.json.
                          Tracks which track_ids are fully analysed so the
                          dialog doesn't have to re-inspect every DB field on
                          every open.
  3. BatchAnalysisScheduler — background thread manager.
                          Accepts a list of tracks, processes them with a
                          configurable worker count, saves cache every 25
                          tracks, and fires a progress callback on the main
                          thread via Qt signals.
"""

import json
import queue
import threading
import warnings
from pathlib import Path

import numpy as np
from pydub import AudioSegment
from PySide6.QtCore import QObject, Signal
from scipy import signal

from src.asset_paths import config
from src.logger_config import logger

warnings.filterwarnings("ignore")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CACHE_PATH = Path(config("analysis_cache.json"))
CACHE_SAVE_INTERVAL = 25  # Save the cache file every N completed tracks


# ===========================================================================
# 1. AnalysisCache
# ===========================================================================


class AnalysisCache:
    """
    Keeps a JSON set of track_ids that have been successfully analysed.

    Format of analysis_cache.json:
        { "analysed_ids": [1, 2, 3, ...] }

    Usage:
        cache = AnalysisCache()
        if cache.is_analysed(track_id):
            skip ...
        cache.mark_analysed(track_id)
        cache.save()                    # called automatically every 25 tracks
        cache.remove(track_id)          # right-click "force re-analyse"
    """

    def __init__(self):
        self._ids: set[int] = set()
        self._dirty_count = 0
        self._lock = threading.Lock()
        self._load()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def is_analysed(self, track_id: int) -> bool:
        with self._lock:
            return track_id in self._ids

    def mark_analysed(self, track_id: int):
        """Record a track as done and save every CACHE_SAVE_INTERVAL tracks."""
        with self._lock:
            self._ids.add(track_id)
            self._dirty_count += 1
            if self._dirty_count >= CACHE_SAVE_INTERVAL:
                self._save_locked()
                self._dirty_count = 0

    def remove(self, track_id: int):
        """Remove a track from the cache so it will be re-analysed."""
        with self._lock:
            self._ids.discard(track_id)
            self._save_locked()

    def save(self):
        """Force an immediate save (call this on scheduler stop/finish)."""
        with self._lock:
            self._save_locked()
            self._dirty_count = 0

    def count(self) -> int:
        with self._lock:
            return len(self._ids)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load(self):
        try:
            if CACHE_PATH.exists():
                data = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
                self._ids = set(data.get("analysed_ids", []))
                logger.info(f"AnalysisCache: loaded {len(self._ids)} cached IDs")
            else:
                self._ids = set()
        except Exception as e:
            logger.error(f"AnalysisCache: failed to load cache — {e}")
            self._ids = set()

    def _save_locked(self):
        """Must be called while self._lock is already held."""
        try:
            CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
            data = {"analysed_ids": list(self._ids)}
            CACHE_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception as e:
            logger.error(f"AnalysisCache: failed to save cache — {e}")


# Shared singleton so the dialog and scheduler always reference the same state
analysis_cache = AnalysisCache()


# ===========================================================================
# 2. AudioCalculations
# ===========================================================================


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

        CHROMA_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

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
                # rolling by +shift means key = CHROMA_NAMES[shift]
                if not np.isnan(r_maj) and r_maj > best_corr:
                    best_corr = r_maj
                    best_key = shift
                    best_mode = "major"
                if not np.isnan(r_min) and r_min > best_corr:
                    best_corr = r_min
                    best_key = shift
                    best_mode = "minor"

            confidence = float(np.clip((best_corr + 1.0) / 2.0, 0.0, 1.0))
            return CHROMA_NAMES[best_key], best_mode, confidence

        except Exception as e:
            logger.error(f"Key calculation failed: {e}")
            return "C", "major", 0.0

    # ------------------------------------------------------------------
    # Gain & peak  (full-track, no sampling)
    # ------------------------------------------------------------------

    def calculate_track_gain(self) -> float:
        """
        Integrated RMS loudness across the whole track, in dBFS.
        Uses 400 ms windows (EBU R-128 influenced) averaged logarithmically.
        """
        if not self._ensure_loaded():
            return -20.0
        try:
            mono = self._mono()
            window = int(self.sr * 0.4)
            if window == 0:
                return -20.0

            rms_values = []
            for start in range(0, len(mono) - window, window):
                chunk = mono[start : start + window]
                rms = np.sqrt(np.mean(chunk**2))
                if rms > 1e-9:
                    rms_values.append(20.0 * np.log10(rms))

            if not rms_values:
                return -60.0

            # Return the mean of the top 70% of windows (ignores silent gaps)
            rms_values.sort(reverse=True)
            top = rms_values[: max(1, int(len(rms_values) * 0.7))]
            return float(np.mean(top))

        except Exception as e:
            logger.error(f"Track gain calculation failed: {e}")
            return -20.0

    def calculate_track_peak(self) -> float:
        """True peak amplitude, 0–1 scale."""
        if not self._ensure_loaded():
            return 0.0
        try:
            return float(np.max(np.abs(self.samples)))
        except Exception as e:
            logger.error(f"Track peak calculation failed: {e}")
            return 0.0

    # ------------------------------------------------------------------
    # Spectral features
    # ------------------------------------------------------------------

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
            if self.samples.shape[0] < 2:
                return 0.0

            # Use centre 20 s
            max_s = int(self.sr * 20)
            l = self.samples[0, :max_s]  # noqa: E741
            r = self.samples[1, :max_s]

            mid = (l + r) / 2.0
            side = (l - r) / 2.0

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
            eps = 1e-9
            geo_mean = np.exp(np.mean(np.log(mag + eps), axis=0))
            arith_mean = np.mean(mag, axis=0) + eps
            flatness = np.mean(geo_mean / arith_mean)
            tonality = float(np.clip(1.0 - flatness * 5.0, 0.0, 1.0))

            # 2. Low-frequency energy dominance
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
          - Dynamic range (higher DR = less limiting/compression = better)
          - Spectral extension (is high-frequency content present?)
          - Clipping detection (values at or very near ±1.0)

        Returns 0–1 (1 = excellent, 0 = very compressed/clipped).
        """
        if not self._ensure_loaded():
            return 0.8
        try:
            # Dynamic range score: map DR 6–24 dB → 0–1
            dr = self.calculate_dynamic_range()
            dr_score = float(np.clip((dr - 6.0) / 18.0, 0.0, 1.0))

            # High frequency extension: a 128 kbps MP3 has steep rolloff above 16 kHz;
            # a FLAC will have content to 20 kHz+
            mono = self._mono()
            seg = self._segment(mono, 10.0)
            f, mag = self._stft_magnitude(seg, nperseg=4096)
            total_e = mag.sum() + 1e-9
            hf_mask = f > 14000
            hf_ratio = mag[hf_mask].sum() / total_e if np.any(hf_mask) else 0.0
            hf_score = float(np.clip(hf_ratio * 20.0, 0.0, 1.0))

            # Clipping: fraction of samples at or beyond 99.9 % of full scale
            clipped = np.mean(np.abs(self.samples) >= 0.999)
            clip_penalty = float(np.clip(clipped * 200.0, 0.0, 1.0))
            clip_score = 1.0 - clip_penalty

            return float(
                np.clip(dr_score * 0.50 + hf_score * 0.30 + clip_score * 0.20, 0.0, 1.0)
            )

        except Exception as e:
            logger.error(f"Fidelity score calculation failed: {e}")
            return 0.8

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
            transient_strength = self.calculate_transient_strength()
            energy = self.calculate_energy()
            danceability = self.calculate_danceability()
            acousticness = self.calculate_acousticness()
            liveness = self.calculate_liveness()
            valence = self.calculate_valence()
            fidelity_score = self.calculate_fidelity_score()

            return {
                "bpm": bpm,
                "tempo_confidence": tempo_confidence,
                "key": key,
                "mode": mode,
                "key_confidence": key_confidence,
                "track_gain": track_gain,
                "track_peak": track_peak,
                "spectral_centroid": spectral_centroid,
                "spectral_rolloff": spectral_rolloff,
                "dynamic_range": dynamic_range,
                "stereo_width": stereo_width,
                "transient_strength": transient_strength,
                "energy": energy,
                "danceability": danceability,
                "acousticness": acousticness,
                "liveness": liveness,
                "valence": valence,
                "fidelity_score": fidelity_score,
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
            "track_gain": -20.0,
            "track_peak": 0.0,
            "spectral_centroid": 2000.0,
            "spectral_rolloff": 8000.0,
            "dynamic_range": 12.0,
            "stereo_width": 0.5,
            "transient_strength": 0.1,
            "energy": 0.5,
            "danceability": 0.5,
            "acousticness": 0.5,
            "liveness": 0.2,
            "valence": 0.5,
            "fidelity_score": 0.8,
        }


# ===========================================================================
# 3. BatchAnalysisScheduler
# ===========================================================================


class _SchedulerSignals(QObject):
    """Qt signals for cross-thread communication."""

    track_done = Signal(int, dict)  # track_id, metadata
    batch_done = Signal(int, int)  # completed_so_far, total
    all_done = Signal(int)  # total tracks processed
    error = Signal(int, str)  # track_id, error message


class BatchAnalysisScheduler:
    """
    Manages a pool of worker threads that pull tracks from a queue and run
    AudioCalculations.run_all() on each one.

    Usage
    -----
        scheduler = BatchAnalysisScheduler(controller, num_workers=2)
        scheduler.start(tracks)           # non-blocking
        scheduler.pause()
        scheduler.resume()
        scheduler.stop()                  # graceful stop; saves cache

    Signals (via .signals)
    ----------------------
        track_done(track_id, metadata)
        batch_done(completed, total)
        all_done(total)
        error(track_id, message)
    """

    def __init__(self, controller, num_workers: int = 2):
        self.controller = controller
        self.num_workers = num_workers

        self.signals = _SchedulerSignals()
        self._queue: queue.Queue = queue.Queue()
        self._workers: list[_AnalysisWorker] = []
        self._lock = threading.Lock()

        self._total = 0
        self._completed = 0
        self._running = False
        self._paused = False
        self._pause_event = threading.Event()
        self._pause_event.set()  # not paused initially

    # ------------------------------------------------------------------
    # Public control methods
    # ------------------------------------------------------------------

    def start(self, tracks: list):
        """
        Populate the queue with tracks and spin up worker threads.
        Tracks already in the cache are skipped automatically.
        """
        with self._lock:
            if self._running:
                logger.warning("BatchAnalysisScheduler: already running")
                return

            # Filter out cached tracks
            pending = [t for t in tracks if not analysis_cache.is_analysed(t.track_id)]
            if not pending:
                logger.info("BatchAnalysisScheduler: all tracks already analysed")
                self.signals.all_done.emit(0)
                return

            self._total = len(pending)
            self._completed = 0
            self._running = True
            self._paused = False
            self._pause_event.set()

            for track in pending:
                self._queue.put(track)

            # Sentinel values so workers know when to exit
            for _ in range(self.num_workers):
                self._queue.put(None)

            self._workers = []
            for i in range(self.num_workers):
                w = _AnalysisWorker(
                    worker_id=i,
                    task_queue=self._queue,
                    pause_event=self._pause_event,
                    controller=self.controller,
                    on_done=self._on_track_done,
                    on_error=self._on_track_error,
                )
                w.start()
                self._workers.append(w)

            logger.info(
                f"BatchAnalysisScheduler: started {self.num_workers} workers "
                f"for {self._total} tracks"
            )

    def pause(self):
        with self._lock:
            if self._running and not self._paused:
                self._paused = True
                self._pause_event.clear()
                logger.info("BatchAnalysisScheduler: paused")

    def resume(self):
        with self._lock:
            if self._running and self._paused:
                self._paused = False
                self._pause_event.set()
                logger.info("BatchAnalysisScheduler: resumed")

    def stop(self):
        """
        Signal all workers to stop after their current track finishes,
        then save the cache.
        """
        with self._lock:
            self._running = False
            self._pause_event.set()  # unblock if paused

            for w in self._workers:
                w.stop()

            # Drain the queue so workers can exit
            while not self._queue.empty():
                try:
                    self._queue.get_nowait()
                except queue.Empty:
                    break

        analysis_cache.save()
        logger.info("BatchAnalysisScheduler: stopped and cache saved")

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def is_paused(self) -> bool:
        return self._paused

    @property
    def progress(self) -> tuple[int, int]:
        """Return (completed, total)."""
        return self._completed, self._total

    # ------------------------------------------------------------------
    # Internal callbacks (called from worker threads)
    # ------------------------------------------------------------------

    def _on_track_done(self, track_id: int, metadata: dict):
        with self._lock:
            self._completed += 1
            completed = self._completed
            total = self._total

        analysis_cache.mark_analysed(track_id)
        self.signals.track_done.emit(track_id, metadata)

        if completed % CACHE_SAVE_INTERVAL == 0:
            self.signals.batch_done.emit(completed, total)

        if completed >= total:
            analysis_cache.save()
            self._running = False
            self.signals.all_done.emit(total)
            logger.info(f"BatchAnalysisScheduler: all {total} tracks complete")

    def _on_track_error(self, track_id: int, message: str):
        with self._lock:
            self._completed += 1

        self.signals.error.emit(track_id, message)


# ===========================================================================
# Internal: worker thread
# ===========================================================================


class _AnalysisWorker(threading.Thread):
    """
    Pulls tracks from the shared queue, runs AudioCalculations.run_all(),
    and writes results to the database via the controller.
    """

    def __init__(
        self,
        worker_id: int,
        task_queue: queue.Queue,
        pause_event: threading.Event,
        controller,
        on_done,
        on_error,
    ):
        super().__init__(daemon=True, name=f"AnalysisWorker-{worker_id}")
        self.worker_id = worker_id
        self._queue = task_queue
        self._pause_event = pause_event
        self.controller = controller
        self._on_done = on_done
        self._on_error = on_error
        self._stop_flag = False

    def stop(self):
        self._stop_flag = True

    def run(self):
        logger.debug(f"{self.name}: started")
        while not self._stop_flag:
            self._pause_event.wait()  # blocks if paused

            try:
                track = self._queue.get(timeout=1.0)
            except queue.Empty:
                continue

            if track is None:  # sentinel → no more work
                break

            if self._stop_flag:
                self._queue.task_done()
                break

            try:
                self._process(track)
            finally:
                self._queue.task_done()

        logger.debug(f"{self.name}: exiting")

    def _process(self, track):
        try:
            logger.info(f"{self.name}: analysing {track.track_file_path}")
            calc = AudioCalculations(track.track_file_path)
            metadata = calc.run_all()  # releases memory inside

            self.controller.update.update_entity("Track", track.track_id, **metadata)
            self._on_done(track.track_id, metadata)

        except Exception as e:
            msg = f"Worker error on track {track.track_id}: {e}"
            logger.error(msg, exc_info=True)
            self._on_error(track.track_id, msg)
