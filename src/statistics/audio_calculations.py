"""
AudioCalculations — pure math on a single audio file.

Loads audio, runs every metric, then releases memory.
"""

import warnings

import numpy as np
from pydub import AudioSegment
from pydub.exceptions import PydubException
from scipy import signal

from src.core.logger_config import logger
from src.statistics.audio_audiophile_mixin import AudiophileScoreMixin
from src.statistics.audio_rhythm_mixin import AudioRhythmMixin
from src.statistics.audio_spectral_mixin import AudioSpectralMixin

warnings.filterwarnings("ignore")


class AudioCalculations(AudioRhythmMixin, AudioSpectralMixin, AudiophileScoreMixin):
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
    * The metrics themselves live in three mixins split by concern
      (AudioRhythmMixin, AudioSpectralMixin, AudiophileScoreMixin) — they
      share this class's `self`, so cross-mixin calls (e.g. calculate_liveness
      calling _quiet_frame_headroom_db) work exactly as if everything were
      still one file.
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
            primary_time_signature, time_signature_confidence = (
                self.calculate_time_signature(bpm=bpm, tempo_confidence=tempo_confidence)
            )
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
                "primary_time_signature": primary_time_signature,
                "time_signature_confidence": time_signature_confidence,
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
            "primary_time_signature": None,
            "time_signature_confidence": 0.0,
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
