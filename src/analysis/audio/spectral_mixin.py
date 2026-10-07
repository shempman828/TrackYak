"""
AudioSpectralMixin — loudness/dynamics, spectral shape, stereo/coherence,
and derived perceptual metrics (energy, danceability, acousticness,
liveness, valence).

Expects the host class to provide: self._ensure_loaded(), self._mono(),
self._segment(), self._stft_magnitude(), self.sr, self.samples, plus
(for calculate_liveness) self._quiet_frame_headroom_db() from
AudiophileScoreMixin.
"""

import numpy as np
from scipy import signal

from src.foundation.logger_config import logger

# Must match gain_calculator.py's REPLAYGAIN_REFERENCE_LUFS — both sides
# assume track_gain is a ReplayGain-style adjustment relative to this reference.
REFERENCE_LUFS = -18.0


class AudioSpectralMixin:
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

    def calculate_spectral_flux(self, stft: tuple[np.ndarray, np.ndarray] | None = None) -> float:
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
    # Derived perceptual metrics
    # ------------------------------------------------------------------

    def calculate_energy(self, stft: tuple[np.ndarray, np.ndarray] | None = None) -> float:
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
                brightness_factor = float(np.clip(mag[high_mask].sum() / total_e * 2.0, 0.0, 1.0))

            return float(np.clip(loudness_factor * 0.6 + brightness_factor * 0.4, 0.0, 1.0))

        except ValueError as e:
            logger.error(f"Energy calculation failed: {e}")
            return 0.5

    def calculate_danceability(
        self, bpm: float | None = None, stft: tuple[np.ndarray, np.ndarray] | None = None
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
            beat_strength = (np.std(bass_time) / (mean_bass + 1e-8)) if mean_bass > 1e-8 else 0.0
            beat_factor = float(np.clip(beat_strength / 1.5, 0.0, 1.0))

            # Spectral balance
            mid_mask = (f > 200) & (f <= 4000)
            high_mask = f > 4000
            total = np.mean(mag) + 1e-8
            bass_r = np.mean(mag[bass_mask]) / total
            mid_r = np.mean(mag[mid_mask]) / total
            high_r = np.mean(mag[high_mask]) / total
            # Ideal: bass ~0.35, mid ~0.45, high ~0.20
            balance = 1.0 - (abs(bass_r - 0.35) + abs(mid_r - 0.45) + abs(high_r - 0.20))
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
                np.clip(beat_factor * 0.35 + balance_factor * 0.35 + tempo_factor * 0.30, 0.0, 1.0)
            )

        except ValueError as e:
            logger.error(f"Danceability calculation failed: {e}")
            return 0.5

    def calculate_acousticness(
        self, centroid: float | None = None, stft: tuple[np.ndarray, np.ndarray] | None = None
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
                np.clip(tonality * 0.5 + freq_factor * 0.3 + centroid_factor * 0.2, 0.0, 1.0)
            )

        except ValueError as e:
            logger.error(f"Acousticness calculation failed: {e}")
            return 0.5

    def calculate_liveness(self, stft: tuple[np.ndarray, np.ndarray] | None = None) -> float:
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
            # by how much real energy is actually up there. Kept as a minor,
            # secondary signal only: measured against ~20 real library
            # tracks, it runs noisy per-track but is directionally right on
            # average (about 2x higher for live tracks than studio) — the
            # persistence term below is the one that actually discriminates.
            noise_mask = f > 8000
            if np.any(noise_mask):
                hf = mag[noise_mask]
                level_ratio = np.mean(hf) / (np.mean(mag) + eps)
                flatness = self._flatness_from_magnitude(hf)
                noise_factor = float(np.clip(level_ratio * 3.0, 0.0, 1.0)) * flatness
            else:
                noise_factor = 0.0

            # Noise-floor persistence: how close to true silence the
            # track's own quietest moments get, relative to its peak (see
            # _quiet_frame_headroom_db — shared with _noise_floor_gate).
            # Previously this compared the quietest 10% of *this 30s
            # window's* STFT frames against the loudest 10% of the same
            # window — which mostly tracked the arrangement's own dynamics
            # and mastering compression within that window, not genuine
            # ambience: a heavily-compressed dry studio track can have a
            # narrower loud/quiet spread *in that window* than a genuinely
            # live recording whose quiet verse is still much quieter than
            # its loud chorus despite a real crowd underneath. Measuring
            # headroom against the track's true peak over its full length
            # fixes that — confirmed against Peter Frampton's "Show Me the
            # Way (Live)" (~21dB headroom, never gets that quiet) versus
            # three studio comparisons that all reach 29-31dB.
            headroom_db = self._quiet_frame_headroom_db()
            if headroom_db is None:
                persistence_factor = 0.3
            else:
                # Across a broader sample of real live vs. studio tracks,
                # loud/arena live recordings clustered ~19-26dB and studio
                # tracks ~27-34dB, with quiet/hushed live performances
                # (e.g. a polite jazz-club or acoustic-set audience) as a
                # known miss — they can legitimately reach studio-like
                # headroom when the audience isn't audibly present during
                # the sampled passage. Ramp is placed right at that
                # boundary (full credit by 20dB, zero by 30dB) rather than
                # spread across the whole observed range, so a clearly
                # live arena recording scores near the top of the scale
                # and a clearly dry studio recording scores near zero,
                # instead of both landing in a mushy middle.
                persistence_factor = float(np.clip((30.0 - headroom_db) / 10.0, 0.0, 1.0))

            return float(np.clip(noise_factor * 0.15 + persistence_factor * 0.85, 0.0, 1.0))

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
                np.clip(mode_factor * 0.50 + tempo_factor * 0.30 + brightness * 0.20, 0.0, 1.0)
            )

        except (ValueError, TypeError) as e:
            logger.error(f"Valence calculation failed: {e}")
            return 0.5
