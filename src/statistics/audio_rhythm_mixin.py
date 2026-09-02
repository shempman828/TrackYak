"""
AudioRhythmMixin — tempo, key, and transient-attack analysis.

Expects the host class to provide: self._ensure_loaded(), self._mono(),
self._segment(), self.sr, self.samples.
"""

import numpy as np
from scipy import signal

from src.foundation.logger_config import logger

# ----------------------------------------------------------------------
# Time signature candidates: (label, pulses_per_measure, subdivision).
# subdivision is "simple" (duple, e.g. 4/4), "compound" (triple, e.g. 6/8),
# or "direct" — additive/irregular meters (7/8, 11/8) that don't have a
# uniform per-pulse subdivision the way 6/8 does, scored directly off the
# finer sub-beat pulse envelope instead of via grouping x subdivision-fit.
# Extension point for later additions (e.g. 5/8, 15/8) — not exhaustive.
_METER_CANDIDATES: list[tuple[str, int, str]] = [
    ("4/4", 4, "simple"),
    ("3/4", 3, "simple"),
    ("2/4", 2, "simple"),
    ("6/8", 2, "compound"),
    ("9/8", 3, "compound"),
    ("12/8", 4, "compound"),
    ("5/4", 5, "simple"),
    ("7/4", 7, "simple"),
    ("7/8", 7, "direct"),
    ("11/8", 11, "direct"),
]
# 2/4 is grouped with 4/4/3/4 as "common," not treated as a rare claim
# needing to clear MARGIN — a strong kick/snare backbeat is genuinely
# period-2 at the beat level, and "2/4 feel" vs. "4/4 written as two 2/4
# bars" is a benign, well-known notational ambiguity in real music, not a
# meter an algorithm should need special justification to land on (real-file
# testing showed plenty of ordinary backbeat-driven tracks scoring 2/4
# highest, which is a reasonable reading, not a false positive to suppress).
_COMMON_METERS = ("4/4", "3/4", "2/4")

# Calibration targets — tune against real library files (see
# calculate_transient_strength's crest-factor clip and calculate_liveness's
# headroom ramp for precedent on how these get tightened from real data).
# MARGIN: how much an uncommon meter's raw grouping/subdivision fit must
# beat the best common-meter (4/4 or 3/4) candidate by before it's allowed
# to win the "which label would we report" decision at all.
# FLOOR: minimum bpm-confidence-discounted score required for the *final*
# chosen label -- common or uncommon -- to actually be reported; below this,
# calculate_time_signature returns None rather than a guess. Deliberately
# strict: a live-library sample showed real tempo_confidence rarely clears
# ~0.15-0.2 (median ~0.10), and even the "common baseline" (4/4/3/4) path
# mislabeled known tracks (a 7/4 track, two waltzes) when left ungated in
# testing -- raising this bar to also gate the common path, not just
# uncommon-meter claims, is what actually fixed that.
_METER_MARGIN = 0.15
_METER_FLOOR = 0.15
_MIN_BEATS_FOR_METER = 8
# How much a multiple-of-a-smaller-candidate's grouping score must exceed
# that smaller candidate by before it's trusted as genuinely stronger,
# rather than an inflated harmonic of the true (smaller) period.
_HARMONIC_TOLERANCE = 0.05


class AudioRhythmMixin:
    # ------------------------------------------------------------------
    # BPM  (onset-strength + multi-resolution autocorrelation)
    # ------------------------------------------------------------------

    def _onset_envelope(self, seg: np.ndarray) -> tuple[np.ndarray, float]:
        """
        High-pass-filtered, rectified, ~200 Hz onset-strength envelope for a
        given segment. Shared by calculate_bpm and calculate_time_signature —
        both need the same rhythmic-pulse envelope, just autocorrelated over
        different lag ranges/candidates.

        Returns (envelope, envelope_sr).
        """
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
            envelope, np.hanning(smooth_win) / np.sum(np.hanning(smooth_win)), mode="same"
        )
        return envelope, envelope_sr

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
            envelope, envelope_sr = self._onset_envelope(seg)

            # --- Autocorrelation over BPM range 40–240 ---
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
    # Time signature  (beat-grouping + subdivision autocorrelation, gated
    # by a common-meter margin and discounted by BPM confidence)
    # ------------------------------------------------------------------

    @staticmethod
    def _normalized_autocorr(x: np.ndarray) -> np.ndarray | None:
        """Zero-mean, zero-lag-normalised autocorrelation, non-negative lags
        only. Returns None if the signal is too flat (silence, no rhythmic
        pattern) for periodicity to mean anything."""
        x = x - x.mean()
        if np.std(x) < 1e-9:
            return None
        ac = signal.correlate(x, x, mode="full")
        ac = ac[len(ac) // 2 :]
        return ac / (ac[0] + 1e-12)

    def calculate_time_signature(
        self, bpm: float, tempo_confidence: float
    ) -> tuple[str | None, float]:
        """
        Classify the track's primary time signature from the same
        onset-strength envelope calculate_bpm uses, by scoring a fixed set of
        meter candidates (_METER_CANDIDATES) on two axes — how many main
        pulses repeat per measure, and whether each pulse subdivides in 2
        (simple) or 3 (compound) — then picking the best-fitting candidate,
        gated so an uncommon meter only wins over 4/4 or 3/4 if it clears
        both a margin over them AND a bpm-confidence-discounted floor (a
        shaky beat grid vetoes an odd-meter call even if the raw pattern
        looks clean, since everything here is built on that same beat grid).

        Real-file testing (see the spec) showed onset-envelope-based meter
        detection is unreliable on full-mix audio even for 4/4-vs-3/4 — and
        real tempo_confidence rarely exceeds ~0.15-0.2 (median ~0.10 across
        a live-library sample), since calculate_bpm's own beat detection is
        itself none too sure on complex material. Rather than confidently
        mislabel most of the library, the label itself is gated by the same
        floor as the margin check: below it, this returns None (no guess),
        not a guessed default — deliberately preferring an empty field over
        a wrong one, even for the "common" candidates.

        Returns (label, confidence). label is None when nothing cleared the
        confidence floor; confidence is always a real 0-1 number (never
        faked to 0.0 just because label is None) reflecting how close the
        best candidate came.
        """
        if not self._ensure_loaded() or tempo_confidence <= 0.0 or bpm <= 0.0:
            return None, 0.0

        try:
            mono = self._mono()
            seg = self._segment(mono, 60.0)
            envelope, envelope_sr = self._onset_envelope(seg)

            beat_period = envelope_sr * 60.0 / bpm
            if beat_period < 2.0:
                return None, 0.0

            n_beats = int(len(envelope) / beat_period)
            if n_beats < _MIN_BEATS_FOR_METER:
                return None, 0.0

            # Bin edges from the cumulative (fractional) beat position, not a
            # fixed rounded-integer width — a fixed width's sub-frame
            # rounding error compounds linearly across dozens of beats,
            # aliasing into a spurious low-frequency amplitude modulation
            # that can rival (or beat) the real per-measure accent pattern.
            bin_edges = np.round(np.arange(n_beats + 1) * beat_period).astype(int)
            accent = np.array(
                [
                    envelope[bin_edges[i] : bin_edges[i + 1]].max()
                    if bin_edges[i + 1] > bin_edges[i]
                    else 0.0
                    for i in range(n_beats)
                ]
            )

            beat_ac = self._normalized_autocorr(accent)
            fine_ac = self._normalized_autocorr(envelope)
            if beat_ac is None or fine_ac is None:
                return None, 0.0

            def grouping_fit(pulses: int) -> float:
                if pulses >= len(beat_ac):
                    return 0.0
                return float(np.clip(beat_ac[pulses], 0.0, 1.0))

            def energy_at(divisor: float) -> float:
                lag = round(beat_period / divisor)
                if not (0 < lag < len(fine_ac)):
                    return 0.0
                return float(np.clip(fine_ac[lag], 0.0, 1.0))

            # Subdivision is a single simple-vs-compound *comparison*, not a
            # per-candidate multiplier: a plain beat-level pulse train with no
            # audible sub-beat content at all (e.g. a bare click track, or
            # sparse minimalist music) has near-zero energy at *both* the
            # half-beat and third-beat lag — that must default to "simple"
            # (the overwhelmingly common case), not zero out every simple
            # candidate uniformly just because subdivision evidence happens
            # to be absent. Compound only wins when its evidence is stronger.
            is_compound = energy_at(3.0) > energy_at(2.0)

            # "direct" candidates (7/8, 11/8) are scored on an assumed
            # eighth-note-ish pulse (half the detected beat period) rather
            # than the beat-grouping/subdivision two-step, since additive
            # meters don't have a uniform per-beat subdivision to measure.
            pulse_period = max(1, round(beat_period / 2.0))

            def direct_fit(pulses: int) -> float:
                lag = pulses * pulse_period
                if not (0 < lag < len(fine_ac)):
                    return 0.0
                return float(np.clip(fine_ac[lag], 0.0, 1.0))

            raw_scores: dict[str, float] = {}
            for label, pulses, kind in _METER_CANDIDATES:
                if kind == "direct":
                    raw_scores[label] = direct_fit(pulses)
                elif kind == "compound":
                    raw_scores[label] = grouping_fit(pulses) if is_compound else 0.0
                else:  # simple
                    raw_scores[label] = 0.0 if is_compound else grouping_fit(pulses)

            # A genuinely period-P accent pattern's autocorrelation scores
            # just as high (sometimes marginally higher, from noise/edge
            # truncation) at any exact multiple of P — the same octave
            # ambiguity calculate_bpm already corrects for by preferring a
            # canonical range. Here, deflate a multiple-of-P candidate back
            # to 0 when it isn't clearly ahead of the smaller candidate it's
            # built on, so e.g. a real 2-pulse pattern doesn't spuriously
            # surface as its own 4-pulse harmonic (6/8 vs 12/8, 2/4 vs 4/4).
            for kind_name in ("simple", "compound"):
                members = [
                    (label, pulses)
                    for label, pulses, kind in _METER_CANDIDATES
                    if kind == kind_name
                ]
                for label_b, pulses_b in members:
                    for label_a, pulses_a in members:
                        if (
                            pulses_a < pulses_b
                            and pulses_b % pulses_a == 0
                            and raw_scores[label_b] <= raw_scores[label_a] + _HARMONIC_TOLERANCE
                        ):
                            raw_scores[label_b] = 0.0

            baseline_label = max(_COMMON_METERS, key=lambda label: raw_scores[label])
            global_best = max(raw_scores, key=raw_scores.get)

            if global_best in _COMMON_METERS or (
                raw_scores[global_best] - raw_scores[baseline_label] >= _METER_MARGIN
                and raw_scores[global_best] * tempo_confidence >= _METER_FLOOR
            ):
                chosen = global_best
            else:
                chosen = baseline_label

            confidence = float(np.clip(raw_scores[chosen] * tempo_confidence, 0.0, 1.0))

            # Final, universal gate: applies even to a "common" 4/4/3/4
            # outcome, not just uncommon-meter candidates. Real-file testing
            # showed the common-baseline path alone was still mislabeling
            # tracks with weak underlying signal — raising the bar means
            # never emitting a label at all below this floor, rather than
            # only reserving it for gating rare-meter claims.
            if confidence < _METER_FLOOR:
                return None, confidence

            return chosen, confidence

        except (ValueError, ZeroDivisionError, IndexError) as e:
            logger.error(f"Time signature calculation failed: {e}")
            return None, 0.0

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
        MAJOR_KEY_NAMES = ["C", "Db", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]
        MINOR_KEY_NAMES = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "G#", "A", "Bb", "B"]

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
