"""
AudiophileScoreMixin — the audiophile-score gate/axis subsystem, plus
chromaprint fingerprinting.

Expects the host class to provide: self._ensure_loaded(), self._mono(),
self._segment(), self._stft_magnitude(), self.sr, self.samples,
self.audio_file_path, plus self.calculate_dynamic_range() from
AudioSpectralMixin.
"""

import acoustid
import numpy as np
from scipy import signal
from scipy.ndimage import uniform_filter1d

from src.core.logger_config import logger


class AudiophileScoreMixin:
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

    def _quiet_frame_headroom_db(self) -> float | None:
        """
        How far below the track's own peak the quietest ~75ms frames sit,
        in dB. Shared by `_noise_floor_gate` (audiophile scoring) and
        `calculate_liveness` (persistent room/crowd presence detection) —
        both start from the same question, "how close to true silence do
        this track's quiet moments get", just map a low answer to opposite
        conclusions (a defect for audiophile scoring, a live-ambience
        signal for liveness). Returns None if the track is too short to
        estimate this reliably.
        """
        mono = self._mono()
        frame = max(1, int(self.sr * 0.075))  # ~75ms frames
        n_frames = len(mono) // frame
        if n_frames < 20:
            return None
        chunks = mono[: n_frames * frame].reshape(n_frames, frame)
        frame_rms = np.sqrt(np.mean(chunks**2, axis=1))
        # Exclude digital-silence padding (leading/trailing zeros, encoder
        # gaps) — that's not the recording's noise floor, it's silence.
        active = frame_rms[frame_rms > 1e-6]
        if len(active) < 20:
            return None

        quiet_threshold = np.percentile(active, 10)
        quiet = active[active <= quiet_threshold]
        noise_floor_db = 20.0 * np.log10(np.mean(quiet) + 1e-9)

        peak = np.max(np.abs(mono))
        peak_db = 20.0 * np.log10(peak + 1e-9)
        return peak_db - noise_floor_db

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
        headroom_db = self._quiet_frame_headroom_db()
        if headroom_db is None:
            return 0.5

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

    def _lossy_cutoff_gate(self, ltas: tuple[np.ndarray, np.ndarray] | None = None) -> float:
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

    def _spectral_shape_score(self, ltas: tuple[np.ndarray, np.ndarray] | None = None) -> float:
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
            quality = max(soundstage_score, FLOOR) ** 0.6 * max(spectral_shape_score, FLOOR) ** 0.4

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
            duration, fingerprint = acoustid.fingerprint_file(self.audio_file_path, maxlength=120)
            return fingerprint.decode() if isinstance(fingerprint, bytes) else fingerprint, duration
        except acoustid.AcoustidError as e:
            logger.warning(f"Fingerprint calculation failed for {self.audio_file_path}: {e}")
            return None, None
