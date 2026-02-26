import queue
import threading
import warnings

import numpy as np
from pydub import AudioSegment
from scipy import signal

from src.logger_config import logger

warnings.filterwarnings("ignore")

"""Track Model
    # Spectral analysis
    spectral_centroid = Column(Float)  # Brightness of sound
    spectral_rolloff = Column(Float)  # Frequency cutoff
    # Audio Characteristics
    bpm = Column(Float)
    track_gain = Column(Float)
    track_peak = Column(Float)
    key = Column(String)  # Musical key (C, D, E, etc.)
    mode = Column(String)  # Major or minor
    key_confidence = Column(Float)
    tempo_confidence = Column(Float)  # Confidence in BPM detection
    # Advanced features
    dynamic_range = Column(Float)  # Range: 6.0 - 20.0+
    stereo_width = Column(Float)  # Range: 0.0 - 1.0
    transient_strength = Column(Float)  # Range: 0.0 - 0.5+
    danceability = Column(Float)  # 0-1 how danceable
    energy = Column(Float)  # 0-1 intensity/activity
    acousticness = Column(Float)  # 0-1 acoustic vs electric
    liveness = Column(Float)  # 0-1 performed live
    valence = Column(Float)  # 0-1 musical positiveness
    fidelity_score = Column(Float)  # 1 - (RMS_compression + spectral_flatness) * 0.5
"""


class AudioCalculations:
    """
    class for calculating audio properties using pydub + scipy instead of librosa
    """

    def __init__(self, audio_file_path):
        self.audio_file_path = audio_file_path
        self.audio = None
        self.samples = None
        self.sr = None
        self._audio_loaded = False

    def _load_audio(self):
        """Load audio file using pydub"""
        if self._audio_loaded:
            return True

        try:
            logger.info(f"Loading audio file with pydub: {self.audio_file_path}")

            # Load audio with pydub
            self.audio = AudioSegment.from_file(self.audio_file_path)
            self.sr = self.audio.frame_rate

            # Convert to numpy array
            samples = np.array(self.audio.get_array_of_samples())

            # Handle stereo
            if self.audio.channels == 2:
                # Reshape stereo to [left, right]
                samples = samples.reshape((-1, 2))
                self.samples = samples.T  # Shape: (2, n_samples)
            else:
                # Mono
                self.samples = np.array([samples])

            # Normalize to float32 [-1, 1]
            if self.samples.dtype == np.int16:
                self.samples = self.samples.astype(np.float32) / 32768.0
            elif self.samples.dtype == np.int32:
                self.samples = self.samples.astype(np.float32) / 2147483648.0

            self._audio_loaded = True
            logger.info(
                f"Audio loaded successfully: SR={self.sr}, Channels={self.samples.shape[0]}, Samples={self.samples.shape[1]}"
            )
            return True

        except Exception as e:
            logger.error(
                f"Error loading audio file {self.audio_file_path}: {str(e)}",
                exc_info=True,
            )
            # Create silent audio as fallback
            self.sr = 44100
            self.samples = np.zeros((1, 44100))
            self._audio_loaded = True
            return False

    def _ensure_audio_loaded(self):
        """Ensure audio is loaded before calculations"""
        if not self._audio_loaded:
            return self._load_audio()
        return True

    def _get_mono(self):
        """Get mono version of audio"""
        if not self._ensure_audio_loaded():
            return np.zeros(44100)

        if self.samples.shape[0] == 1:
            return self.samples[0]
        else:
            return np.mean(self.samples, axis=0)

    def _stft(self, audio_data, window="hann", nperseg=2048):
        """Simple STFT implementation"""
        f, t, Zxx = signal.stft(audio_data, fs=self.sr, window=window, nperseg=nperseg)
        return f, t, Zxx

    def calculate_bpm(self):
        """Calculate BPM using improved autocorrelation with better peak detection"""
        if not self._ensure_audio_loaded():
            return 120.0, 0.5

        try:
            mono_audio = self._get_mono()

            # Use a segment for calculation (45 seconds for better accuracy)
            segment_length = min(len(mono_audio), self.sr * 45)
            audio_segment = mono_audio[:segment_length]

            # Apply high-pass filter to remove DC offset and very low frequencies
            nyquist = self.sr / 2
            b, a = signal.butter(3, 40 / nyquist, btype="high")
            audio_filtered = signal.filtfilt(b, a, audio_segment)

            # Compute autocorrelation
            correlation = signal.correlate(audio_filtered, audio_filtered, mode="full")
            correlation = correlation[len(correlation) // 2 :]  # Keep positive lags

            # Only consider lags corresponding to 40-240 BPM
            min_lag = int(self.sr * 60 / 240)  # 240 BPM
            max_lag = int(self.sr * 60 / 40)  # 40 BPM
            correlation = correlation[min_lag:max_lag]

            if len(correlation) == 0:
                return 120.0, 0.1

            # Find peaks with improved parameters
            peaks, properties = signal.find_peaks(
                correlation,
                height=np.max(correlation) * 0.2,
                distance=int(self.sr * 60 / 240),  # Minimum distance for 240 BPM
                prominence=np.max(correlation) * 0.1,
            )

            if len(peaks) > 1:
                # Use the most prominent peaks
                if "prominences" in properties and len(properties["prominences"]) > 0:
                    # Get the most prominent peak (usually the fundamental period)
                    main_peak_idx = np.argmax(properties["prominences"])
                    main_peak = peaks[main_peak_idx]

                    # Convert lag to BPM
                    period_samples = main_peak + min_lag
                    period_seconds = period_samples / self.sr
                    bpm = 60.0 / period_seconds

                    # Calculate confidence based on peak prominence and consistency
                    prominence_norm = properties["prominences"][main_peak_idx] / np.max(
                        correlation
                    )
                    confidence = min(prominence_norm * 1.5, 1.0)
                else:
                    # Fallback: use first significant peak
                    period_samples = peaks[0] + min_lag
                    period_seconds = period_samples / self.sr
                    bpm = 60.0 / period_seconds
                    confidence = 0.3
            else:
                bpm = 120.0
                confidence = 0.1

            # Constrain to reasonable BPM range and round
            bpm = max(40, min(bpm, 240))
            bpm = round(bpm, 1)

            return float(bpm), float(confidence)

        except Exception as e:
            logger.error(f"Error calculating BPM: {e}")
        return 120.0, 0.0

    def calculate_key(self):
        """Improved key detection using chromagram and key profiles"""
        if not self._ensure_audio_loaded():
            return "C", "major", 0.5

        try:
            mono_audio = self._get_mono()

            # Use longer segment for better accuracy
            segment_length = min(len(mono_audio), self.sr * 15)
            audio_segment = mono_audio[:segment_length]

            # Compute chromagram using STFT
            f, t, Zxx = self._stft(audio_segment, nperseg=4096)
            magnitude = np.abs(Zxx)

            # Define chroma bands (MIDI note frequencies)
            chroma_names = [
                "C",
                "C#",
                "D",
                "D#",
                "E",
                "F",
                "F#",
                "G",
                "G#",
                "A",
                "A#",
                "B",
            ]
            A4 = 440.0
            chroma_centers = [A4 * 2 ** ((i - 9) / 12.0) for i in range(12)]

            # Create chromagram by mapping frequencies to chroma bins
            chromagram = np.zeros(12)
            for i, center_freq in enumerate(chroma_centers):
                # Find frequencies within ±50 cents of chroma center
                lower_freq = center_freq * 2 ** (-1 / 24)
                upper_freq = center_freq * 2 ** (1 / 24)

                freq_mask = (f >= lower_freq) & (f <= upper_freq)
                if np.any(freq_mask):
                    chromagram[i] = np.mean(magnitude[freq_mask, :])

            # Normalize chromagram
            chroma_sum = np.sum(chromagram)
            if chroma_sum > 0:
                chromagram /= chroma_sum

            # Simple key profiles (major and minor)
            major_profile = np.array(
                [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88]
            )
            minor_profile = np.array(
                [6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17]
            )
            major_profile /= np.sum(major_profile)
            minor_profile /= np.sum(minor_profile)

            # Find best matching key
            best_correlation = -1
            best_key = 0
            best_mode = "major"

            for shift in range(12):
                # Rotate chromagram for each possible key
                rotated_chroma = np.roll(chromagram, shift)

                # Correlate with major profile
                major_corr = np.corrcoef(rotated_chroma, major_profile)[0, 1]
                if not np.isnan(major_corr) and major_corr > best_correlation:
                    best_correlation = major_corr
                    best_key = shift
                    best_mode = "major"

                # Correlate with minor profile
                minor_corr = np.corrcoef(rotated_chroma, minor_profile)[0, 1]
                if not np.isnan(minor_corr) and minor_corr > best_correlation:
                    best_correlation = minor_corr
                    best_key = shift
                    best_mode = "minor"

            key_name = chroma_names[best_key]
            confidence = (
                max(0.1, min(best_correlation, 1.0))
                if not np.isnan(best_correlation)
                else 0.5
            )

            return key_name, best_mode, float(confidence)

        except Exception as e:
            logger.error(f"Error calculating key: {e}")
            return "C", "major", 0.0

    def calculate_track_gain(self):
        """Calculate RMS gain in dB"""
        if not self._ensure_audio_loaded():
            return -20.0

        try:
            mono_audio = self._get_mono()
            rms = np.sqrt(np.mean(mono_audio**2))
            gain_db = 20 * np.log10(rms + 1e-8)
            return float(gain_db)
        except Exception as e:
            logger.error(f"Error calculating track gain: {e}")
            return -20.0

    def calculate_track_peak(self):
        """Calculate peak amplitude"""
        if not self._ensure_audio_loaded():
            return 0.0

        try:
            mono_audio = self._get_mono()
            peak = np.max(np.abs(mono_audio))
            return float(peak)
        except Exception as e:
            logger.error(f"Error calculating track peak: {e}")
            return 0.0

    def calculate_spectral_centroid(self):
        """Calculate spectral centroid with improved frequency weighting"""
        if not self._ensure_audio_loaded():
            return 2000.0

        try:
            mono_audio = self._get_mono()
            f, t, Zxx = self._stft(mono_audio, nperseg=2048)

            # Calculate magnitude spectrum
            magnitude = np.abs(Zxx)

            # Ensure we're using frequency values correctly
            # The centroid should be weighted by frequency
            centroid_frames = []

            for i in range(magnitude.shape[1]):
                frame_mag = magnitude[:, i]
                total_mag = np.sum(frame_mag)

                if total_mag > 1e-8:
                    # Proper frequency-weighted centroid
                    centroid = np.sum(f * frame_mag) / total_mag
                    centroid_frames.append(centroid)

            if centroid_frames:
                avg_centroid = np.median(centroid_frames)
            else:
                avg_centroid = 2000.0

            return float(avg_centroid)

        except Exception as e:
            logger.error(f"Error calculating spectral centroid: {e}")
            return 2000.0

    def calculate_spectral_rolloff(self):
        """Calculate spectral rolloff (85th percentile)"""
        if not self._ensure_audio_loaded():
            return 8000.0

        try:
            mono_audio = self._get_mono()
            f, t, Zxx = self._stft(mono_audio)

            magnitude = np.abs(Zxx)
            rolloffs = []

            for i in range(magnitude.shape[1]):
                frame_mag = magnitude[:, i]
                total_energy = np.sum(frame_mag)
                cumulative_energy = np.cumsum(frame_mag)

                # Find frequency where 85% of energy is contained
                threshold = 0.85 * total_energy
                rolloff_idx = np.where(cumulative_energy >= threshold)[0]
                if len(rolloff_idx) > 0:
                    rolloffs.append(f[rolloff_idx[0]])

            avg_rolloff = np.mean(rolloffs) if rolloffs else 8000.0
            return float(avg_rolloff)
        except Exception as e:
            logger.error(f"Error calculating spectral rolloff: {e}")
            return 8000.0

    def calculate_dynamic_range(self):
        """Calculate dynamic range in dB"""
        if not self._ensure_audio_loaded():
            return 12.0

        try:
            mono_audio = self._get_mono()

            # Calculate RMS in chunks
            chunk_size = self.sr // 10  # 100ms chunks
            num_chunks = len(mono_audio) // chunk_size

            if num_chunks == 0:
                return 12.0

            rms_values = []
            for i in range(num_chunks):
                chunk = mono_audio[i * chunk_size : (i + 1) * chunk_size]
                rms = np.sqrt(np.mean(chunk**2))
                # Avoid log of zero and very small values
                if rms > 1e-8:
                    rms_db = 20 * np.log10(rms)
                    rms_values.append(rms_db)

            if len(rms_values) < 2:
                return 12.0

            # Remove outliers (top and bottom 5%)
            if len(rms_values) >= 20:
                rms_values_sorted = np.sort(rms_values)
                trim_count = max(1, len(rms_values) // 20)  # 5%
                trimmed_values = rms_values_sorted[trim_count:-trim_count]
                dynamic_range = np.max(trimmed_values) - np.min(trimmed_values)
            else:
                dynamic_range = np.max(rms_values) - np.min(rms_values)

            # Realistic constraint for music
            return float(min(max(dynamic_range, 6.0), 24.0))

        except Exception as e:
            logger.error(f"Error calculating dynamic range: {e}")
            return 12.0

    def calculate_stereo_width(self):
        """Calculate stereo width (0-1) using multiple methods"""
        if not self._ensure_audio_loaded():
            return 0.5

        try:
            if self.samples.shape[0] == 1:
                return 0.0  # Mono

            left_channel = self.samples[0]
            right_channel = self.samples[1]

            # Use a representative segment
            segment_length = min(len(left_channel), self.sr * 10)
            left_segment = left_channel[:segment_length]
            right_segment = right_channel[:segment_length]

            # Method 1: RMS level difference (simpler, more intuitive)
            rms_left = np.sqrt(np.mean(left_segment**2))
            rms_right = np.sqrt(np.mean(right_segment**2))

            # Avoid division by zero
            if rms_left + rms_right < 1e-8:
                return 0.0

            level_difference = abs(rms_left - rms_right) / (rms_left + rms_right)

            # Method 2: Mid/Side analysis (professional approach)
            mid = (left_segment + right_segment) / 2
            side = (left_segment - right_segment) / 2

            rms_mid = np.sqrt(np.mean(mid**2))
            rms_side = np.sqrt(np.mean(side**2))

            if rms_mid + rms_side < 1e-8:
                ms_ratio = 0.0
            else:
                ms_ratio = rms_side / (rms_mid + rms_side)

            # Method 3: Correlation (keep existing but with bounds)
            correlation = np.corrcoef(left_segment, right_segment)[0, 1]
            if np.isnan(correlation):
                correlation = 1.0
            correlation_width = 1.0 - ((correlation + 1) / 2)

            # Combine methods with weights
            stereo_width = (
                (1 - level_difference) * 0.3  # Level similarity
                + ms_ratio * 0.5  # Mid/Side ratio (most important)
                + correlation_width * 0.2  # Phase correlation
            )

            # Apply non-linear mapping for better distribution
            # Most music falls between 0.2-0.8
            if stereo_width < 0.1:
                stereo_width = stereo_width * 0.5  # Compress very narrow
            elif stereo_width > 0.7:
                stereo_width = 0.7 + (stereo_width - 0.7) * 0.6  # Compress very wide

            return float(min(max(stereo_width, 0.0), 1.0))

        except Exception as e:
            logger.error(f"Error calculating stereo width: {e}")
            return 0.5

    def calculate_transient_strength(self):
        """Improved transient detection using envelope and onset detection"""
        if not self._ensure_audio_loaded():
            return 0.1

        try:
            mono_audio = self._get_mono()

            # Use shorter segment for transient analysis
            segment_length = min(len(mono_audio), self.sr * 20)
            audio_segment = mono_audio[:segment_length]

            # 1. High-pass filter to emphasize transients
            nyquist = self.sr / 2
            b, a = signal.butter(2, 100 / nyquist, btype="high")
            audio_filtered = signal.filtfilt(b, a, audio_segment)

            # 2. Calculate envelope with Hilbert transform
            analytic_signal = signal.hilbert(audio_filtered)
            envelope = np.abs(analytic_signal)

            # 3. Smooth the envelope to remove high-frequency fluctuations
            smooth_window = min(101, len(envelope) // 10)  # Adaptive window size
            if smooth_window % 2 == 0:  # Ensure odd window size
                smooth_window += 1

            if smooth_window > 3:
                envelope_smooth = signal.savgol_filter(
                    envelope, window_length=smooth_window, polyorder=3
                )
            else:
                envelope_smooth = envelope

            # 4. Detect transients using onset detection
            # Calculate derivative of smoothed envelope
            envelope_diff = np.diff(envelope_smooth)

            # Find peaks in the derivative (rapid increases in amplitude)
            min_peak_height = np.std(envelope_diff) * 0.5
            transient_peaks, _ = signal.find_peaks(
                envelope_diff,
                height=min_peak_height,
                distance=int(self.sr * 0.05),  # Minimum 50ms between transients
            )

            # 5. Calculate transient strength and density
            if len(transient_peaks) > 0:
                # Average strength of transients
                avg_transient_strength = np.mean(envelope_diff[transient_peaks])

                # Transient density (transients per second)
                duration_seconds = len(audio_segment) / self.sr
                transient_density = len(transient_peaks) / duration_seconds

                # Normalize density (typical range: 0-10 transients/sec)
                density_factor = min(transient_density / 8.0, 1.0)

                # Normalize strength (based on envelope range)
                strength_factor = min(
                    avg_transient_strength / (np.max(envelope_smooth) + 1e-8), 1.0
                )

                # Combine density and strength
                transient_strength = density_factor * 0.6 + strength_factor * 0.4
            else:
                transient_strength = 0.05  # Very few transients

            # Apply non-linear mapping to better distribute values
            # Most music falls in 0.1-0.4 range, electronic/percussive up to 0.8
            if transient_strength < 0.1:
                transient_strength = transient_strength * 0.5  # Compress low end
            elif transient_strength > 0.5:
                transient_strength = (
                    0.5 + (transient_strength - 0.5) * 0.7
                )  # Compress high end

            return float(min(max(transient_strength, 0.01), 0.8))

        except Exception as e:
            logger.error(f"Error calculating transient ratio: {e}")
            return 0.1

    def calculate_danceability(self):
        """Improved danceability based on rhythm stability, beat strength, and spectral balance"""
        if not self._ensure_audio_loaded():
            return 0.5

        try:
            mono_audio = self._get_mono()

            # Calculate rhythm features
            f, t, Zxx = self._stft(mono_audio)
            magnitude = np.abs(Zxx)

            # 1. Beat strength - variance in low-frequency energy over time
            bass_mask = f <= 250
            bass_energy = np.mean(magnitude[bass_mask, :], axis=0)
            beat_strength = np.std(bass_energy) / (np.mean(bass_energy) + 1e-8)
            beat_strength_norm = min(beat_strength, 2.0) / 2.0

            # 2. Spectral balance - danceable tracks have strong bass and clear mids
            mid_mask = (f > 250) & (f <= 4000)
            high_mask = f > 4000

            bass_energy_avg = np.mean(magnitude[bass_mask, :])
            mid_energy_avg = np.mean(magnitude[mid_mask, :])
            high_energy_avg = np.mean(magnitude[high_mask, :])
            total_energy = bass_energy_avg + mid_energy_avg + high_energy_avg + 1e-8

            bass_ratio = bass_energy_avg / total_energy
            mid_ratio = mid_energy_avg / total_energy
            high_ratio = high_energy_avg / total_energy

            # Ideal balance for danceability
            spectral_balance = (
                min(bass_ratio, 0.4) / 0.4 * 0.4
                + min(mid_ratio, 0.4) / 0.4 * 0.4
                + (1 - min(high_ratio, 0.3)) / 0.7 * 0.2
            )

            # 3. Tempo factor - moderate to fast tempos are more danceable
            bpm, _ = self.calculate_bpm()
            tempo_factor = 0.0
            if 90 <= bpm <= 140:  # Optimal dance tempo range
                tempo_factor = 1.0
            elif 70 <= bpm < 90 or 140 < bpm <= 160:
                tempo_factor = 0.7
            else:
                tempo_factor = 0.3

            # Combine factors
            danceability = (
                beat_strength_norm * 0.3 + spectral_balance * 0.4 + tempo_factor * 0.3
            )

            return float(min(max(danceability, 0.0), 1.0))
        except Exception as e:
            logger.error(f"Error calculating danceability: {e}")
            return 0.5

    def calculate_energy(self):
        """Improved energy calculation using perceptual loudness and dynamic features"""
        if not self._ensure_audio_loaded():
            return 0.5

        try:
            mono_audio = self._get_mono()

            # 1. Perceptual loudness (RMS with emphasis on mid frequencies)
            rms = np.sqrt(np.mean(mono_audio**2))

            # 2. High-frequency content (brightness contributes to perceived energy)
            f, t, Zxx = self._stft(mono_audio)
            magnitude = np.abs(Zxx)

            # Perceptual weighting - mid/high frequencies contribute more to energy perception
            mid_high_mask = f > 1000
            brightness = (
                np.mean(magnitude[mid_high_mask, :]) if np.any(mid_high_mask) else 0
            )

            # 3. Dynamic intensity - variance in short-term energy
            window_size = self.sr // 10  # 100ms windows
            num_windows = len(mono_audio) // window_size
            window_energies = []

            for i in range(num_windows):
                window = mono_audio[i * window_size : (i + 1) * window_size]
                window_energy = np.sqrt(np.mean(window**2))
                window_energies.append(window_energy)

            if window_energies:
                dynamic_intensity = np.std(window_energies) / (
                    np.mean(window_energies) + 1e-8
                )
                dynamic_intensity_norm = min(dynamic_intensity, 1.0)
            else:
                dynamic_intensity_norm = 0.5

            # Combine factors with perceptual weighting
            energy = (
                rms * 0.5
                + min(brightness * 2, 1.0) * 0.3
                + dynamic_intensity_norm * 0.2
            )

            return float(min(max(energy, 0.0), 1.0))
        except Exception as e:
            logger.error(f"Error calculating energy: {e}")
            return 0.5

    def calculate_acousticness(self):
        """Improved acousticness detection using harmonic content and spectral characteristics"""
        if not self._ensure_audio_loaded():
            return 0.5

        try:
            mono_audio = self._get_mono()
            f, t, Zxx = self._stft(
                mono_audio, nperseg=4096
            )  # Higher resolution for harmonic analysis
            magnitude = np.abs(Zxx)

            # 1. Harmonicity - acoustic instruments have strong harmonics
            # Calculate spectral smoothness (harmonic sounds have smoother spectra)
            spectral_smoothness = []
            for i in range(magnitude.shape[1]):
                frame = magnitude[:, i]
                if np.sum(frame) > 1e-8:
                    # Harmonic spectra have peaks at harmonic frequencies
                    peaks, _ = signal.find_peaks(
                        frame, height=np.max(frame) * 0.1, distance=10
                    )
                    if len(peaks) > 3:  # Multiple harmonics present
                        # Check if peaks are roughly at harmonic intervals
                        peak_freqs = f[peaks]
                        fundamental_candidates = peak_freqs[
                            peak_freqs < 500
                        ]  # Look for fundamental
                        if len(fundamental_candidates) > 0:
                            fundamental = np.min(fundamental_candidates)
                            # Count how many peaks are near harmonic multiples
                            harmonic_count = 0
                            for harmonic in range(2, 6):  # Check 2nd to 5th harmonics
                                target_freq = fundamental * harmonic
                                if (
                                    np.min(np.abs(peak_freqs - target_freq))
                                    < fundamental * 0.1
                                ):
                                    harmonic_count += 1
                            harmonicity = harmonic_count / 4.0  # Normalize
                            spectral_smoothness.append(harmonicity)

            harmonic_factor = (
                np.mean(spectral_smoothness) if spectral_smoothness else 0.2
            )

            # 2. High-frequency rolloff - acoustic sounds roll off faster
            high_freq_mask = f > 5000
            high_freq_energy = (
                np.mean(magnitude[high_freq_mask, :]) if np.any(high_freq_mask) else 0
            )
            total_energy = np.mean(magnitude)
            high_freq_ratio = high_freq_energy / (total_energy + 1e-8)

            # 3. Spectral centroid - acoustic sounds often have lower centroid
            spectral_centroid = self.calculate_spectral_centroid()
            centroid_factor = 1.0 - min(spectral_centroid / 4000, 1.0)

            # Combine factors
            acousticness = (
                harmonic_factor * 0.5
                + (1 - min(high_freq_ratio * 3, 1.0)) * 0.3
                + centroid_factor * 0.2
            )

            return float(min(max(acousticness, 0.0), 1.0))
        except Exception as e:
            logger.error(f"Error calculating acousticness: {e}")
            return 0.5

    def calculate_liveness(self):
        """Calculate liveness based on high-frequency noise and reverberation"""
        if not self._ensure_audio_loaded():
            return 0.2

        try:
            mono_audio = self._get_mono()
            f, t, Zxx = self._stft(mono_audio)
            magnitude = np.abs(Zxx)

            # Live recordings often have more high-frequency noise
            noise_freq_mask = f > 8000
            noise_ratio = np.mean(magnitude[noise_freq_mask, :]) / (
                np.mean(magnitude) + 1e-8
            )

            # Live recordings may have less consistent spectral distribution
            spectral_variance = np.var(np.mean(magnitude, axis=0))
            variance_factor = min(spectral_variance * 100, 1.0)

            liveness = noise_ratio * 0.6 + variance_factor * 0.4
            return float(min(max(liveness, 0.0), 1.0))
        except Exception as e:
            logger.error(f"Error calculating liveness: {e}")
            return 0.2

    def calculate_valence(self):
        """Improved valence calculation using multiple musical features"""
        if not self._ensure_audio_loaded():
            return 0.5

        try:
            mono_audio = self._get_mono()

            # 1. Tempo factor - faster tempos often indicate positive valence
            bpm, _ = self.calculate_bpm()
            if bpm < 60:
                tempo_factor = 0.2
            elif 60 <= bpm < 90:
                tempo_factor = 0.4
            elif 90 <= bpm < 120:
                tempo_factor = 0.7
            elif 120 <= bpm < 140:
                tempo_factor = 0.9
            else:  # 140+
                tempo_factor = 0.6  # Very fast can be aggressive

            # 2. Spectral characteristics
            spectral_centroid = self.calculate_spectral_centroid()
            brightness_factor = min(spectral_centroid / 5000, 1.0)

            # 3. Rhythm stability - positive valence often has steady rhythm
            f, t, Zxx = self._stft(mono_audio)
            magnitude = np.abs(Zxx)

            # Calculate low-frequency energy variation (beat stability)
            bass_mask = f <= 200
            bass_energy = np.mean(magnitude[bass_mask, :], axis=0)
            if len(bass_energy) > 10:
                # Positive valence often has consistent rhythm
                rhythm_stability = 1.0 - min(
                    np.std(bass_energy) / (np.mean(bass_energy) + 1e-8), 1.0
                )
            else:
                rhythm_stability = 0.5

            # 4. Harmonic content - major vs minor perception
            key, mode, key_confidence = self.calculate_key()
            mode_factor = 0.7 if mode == "major" else 0.3

            # 5. Dynamic range - positive valence often has moderate dynamics
            dynamic_range = self.calculate_dynamic_range()
            if dynamic_range < 8:  # Very compressed
                dynamics_factor = 0.3
            elif 8 <= dynamic_range < 12:  # Moderate
                dynamics_factor = 0.7
            elif 12 <= dynamic_range < 16:  # Good dynamics
                dynamics_factor = 0.9
            else:  # Very dynamic
                dynamics_factor = 0.6

            # Combine factors with weights
            valence = (
                tempo_factor * 0.25
                + brightness_factor * 0.20
                + rhythm_stability * 0.20
                + mode_factor * 0.20
                + dynamics_factor * 0.15
            )

            return float(min(max(valence, 0.0), 1.0))

        except Exception as e:
            logger.error(f"Error calculating valence: {e}")
            return 0.5

    def calculate_fidelity_score(self):
        """Calculate fidelity score based on dynamic range and noise floor"""
        if not self._ensure_audio_loaded():
            return 0.8

        try:
            mono_audio = self._get_mono()

            # Dynamic range component
            dynamic_range = self.calculate_dynamic_range()
            range_score = min(dynamic_range / 20.0, 1.0)

            # Noise floor estimation (high-frequency content when signal is quiet)
            quiet_threshold = np.percentile(np.abs(mono_audio), 10)
            quiet_mask = np.abs(mono_audio) < quiet_threshold

            if np.any(quiet_mask):
                quiet_segments = mono_audio[quiet_mask]
                f, t, Zxx = self._stft(
                    quiet_segments[: min(len(quiet_segments), self.sr)]
                )
                magnitude = np.abs(Zxx)
                high_freq_noise = (
                    np.mean(magnitude[f > 10000, :]) if len(f[f > 10000]) > 0 else 0
                )
                noise_score = 1.0 - min(high_freq_noise * 10, 1.0)
            else:
                noise_score = 0.8

            fidelity = range_score * 0.6 + noise_score * 0.4
            return float(min(max(fidelity, 0.0), 1.0))
        except Exception as e:
            logger.error(f"Error calculating fidelity score: {e}")
            return 0.8


class AudioAnalysis:
    def __init__(self, controller, track):
        self.controller = controller
        self.track = track

    def analyze_track(self):
        """Perform complete audio analysis on track"""
        try:
            logger.info(f"Starting analysis for: {self.track.track_file_path}")
            audio_calc = AudioCalculations(self.track.track_file_path)

            # Calculate all audio properties with individual error handling
            metadata = {}

            try:
                bpm, tempo_confidence = audio_calc.calculate_bpm()
                metadata.update({"bpm": bpm, "tempo_confidence": tempo_confidence})
            except Exception as e:
                logger.error(f"Error calculating BPM: {e}")
                metadata.update({"bpm": 120.0, "tempo_confidence": 0.5})

            try:
                key, mode, key_confidence = audio_calc.calculate_key()
                metadata.update(
                    {"key": key, "mode": mode, "key_confidence": key_confidence}
                )
            except Exception as e:
                logger.error(f"Error calculating key: {e}")
                metadata.update({"key": "C", "mode": "major", "key_confidence": 0.5})

            # Continue with other calculations...
            try:
                track_gain = audio_calc.calculate_track_gain()
                metadata["track_gain"] = track_gain
            except Exception as e:
                logger.error(f"Error calculating track gain: {e}")
                metadata["track_gain"] = -20.0

            try:
                track_peak = audio_calc.calculate_track_peak()
                metadata["track_peak"] = track_peak
            except Exception as e:
                logger.error(f"Error calculating track peak: {e}")
                metadata["track_peak"] = 0.0

            # Add remaining calculations with similar error handling...
            spectral_centroid = audio_calc.calculate_spectral_centroid()
            spectral_rolloff = audio_calc.calculate_spectral_rolloff()
            dynamic_range = audio_calc.calculate_dynamic_range()
            stereo_width = audio_calc.calculate_stereo_width()
            transient_strength = audio_calc.calculate_transient_strength()
            danceability = audio_calc.calculate_danceability()
            energy = audio_calc.calculate_energy()
            acousticness = audio_calc.calculate_acousticness()
            liveness = audio_calc.calculate_liveness()
            valence = audio_calc.calculate_valence()
            fidelity_score = audio_calc.calculate_fidelity_score()

            metadata.update(
                {
                    "spectral_centroid": spectral_centroid,
                    "spectral_rolloff": spectral_rolloff,
                    "dynamic_range": dynamic_range,
                    "stereo_width": stereo_width,
                    "transient_strength": transient_strength,
                    "danceability": danceability,
                    "energy": energy,
                    "acousticness": acousticness,
                    "liveness": liveness,
                    "valence": valence,
                    "fidelity_score": fidelity_score,
                }
            )

            logger.info(f"Analysis completed for: {self.track.track_file_path}")
            return metadata

        except Exception as e:
            logger.error(
                f"Critical error analyzing track {self.track.track_file_path}: {e}",
                exc_info=True,
            )
            # Return default metadata to prevent complete failure
            return {
                "bpm": 120.0,
                "tempo_confidence": 0.5,
                "key": "C",
                "mode": "major",
                "key_confidence": 0.5,
                "track_gain": -20.0,
                "track_peak": 0.0,
                "spectral_centroid": 2000.0,
                "spectral_rolloff": 8000.0,
                "dynamic_range": 12.0,
                "stereo_width": 0.5,
                "transient_strength": 0.1,
                "danceability": 0.5,
                "energy": 0.5,
                "acousticness": 0.5,
                "liveness": 0.2,
                "valence": 0.5,
                "fidelity_score": 0.8,
            }

    def update_track(self):
        """Analyze track and update database"""
        metadata = self.analyze_track()
        if metadata:
            self.controller.update.update_entity(
                "Track", self.track.track_id, **metadata
            )
            logger.info(f"Updated audio analysis for track {self.track.track_name}")
        else:
            logger.error(f"Failed to analyze track {self.track.track_name}")


class AudioAnalysisWorker(threading.Thread):
    """
    Worker thread for background audio analysis
    """

    def __init__(self, task_queue, result_callback=None):
        super().__init__()
        self.task_queue = task_queue
        self.result_callback = result_callback
        self.daemon = True
        self.running = True

    def run(self):
        """Main worker loop"""
        while self.running:
            try:
                # Get task from queue with timeout
                task = self.task_queue.get(timeout=1.0)
                if task is None:  # Shutdown signal
                    break

                controller, track = task
                self.analyze_track(controller, track)
                self.task_queue.task_done()

            except queue.Empty:
                # Timeout is normal, just continue
                continue
            except Exception as e:
                logger.error(f"Error in audio analysis worker: {e}", exc_info=True)
                self.task_queue.task_done()  # Important: mark task as done even on error

    def analyze_track(self, controller, track):
        """Perform analysis and update track in database"""
        try:
            logger.info(f"Starting background analysis for: {track.track_file_path}")
            analysis = AudioAnalysis(controller, track)
            metadata = analysis.analyze_track()

            if metadata:
                controller.update.update_entity("Track", track.track_id, **metadata)
                logger.info(f"Updated audio analysis for track {track.track_name}")

                # Notify callback if provided
                if self.result_callback:
                    self.result_callback(track.track_id, metadata)

            else:
                logger.error(f"Failed to analyze track {track.track_name}")

        except Exception as e:
            logger.error(
                f"Critical error in worker analyzing track {track.track_file_path}: {e}"
            )

    def __del__(self):
        """Cleanup resources"""
        if hasattr(self, "audio") and self.audio:
            # pydub AudioSegment doesn't have explicit close, but we can dereference
            self.audio = None
        self.samples = None
