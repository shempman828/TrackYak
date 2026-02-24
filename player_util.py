"""Music Player Capabilities"""

from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np
from PySide6.QtCore import QObject, Qt, QTimer, Signal

from config_setup import app_config
from equalizer_utility import EqualizerUtility
from logger_config import logger
from queue_utility import QueueManager


class MusicPlayer(QObject):
    """Audiophile-grade music player with bit-perfect playback capabilities."""

    # Signals for UI updates
    position_changed = Signal(int)
    duration_changed = Signal(int)
    state_changed = Signal(str)
    volume_changed = Signal(int)
    error_occurred = Signal(str)
    track_changed = Signal(Path)
    play_count_updated = Signal(Path, int)
    audio_device_changed = Signal(str)
    playback_mode_changed = Signal(str)

    _internal_stop_signal = Signal()

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.equalizer = EqualizerUtility(self)

        # Audio backend components - initialize to None first
        self.sd = None
        self.sf = None
        self.np = None
        self.audio_stream = None
        self.current_decoder = None
        self.audio_thread = None

        # crossfade and normalization
        self.crossfade_enabled = False
        self.crossfade_duration = 3000  # milliseconds
        self.normalization_enabled = False
        self.normalization_target = -23.0  # LUFS target for loudness normalization
        self.next_audio_data = None  # For crossfade pre-loading
        self.crossfade_frames = 0
        self.crossfade_active = False
        self.crossfade_start_frame = 0

        # Playback state
        self.current_file: Optional[Path] = None
        self.playing = False
        self.paused = False
        self._position = 0
        self._duration = 0
        self.audio_data = None
        self.current_frame = 0

        # Audiophile settings
        self.exclusive_mode = False
        self.current_device = None
        self.volume_level = app_config.get_volume()
        logger.debug(f"Loaded volume from config: {self.volume_level}")

        # Audio format info
        self.current_sample_rate = 44100
        self.current_bit_depth = 16
        self.current_channels = 2
        self.current_format = None

        # Play count tracking
        self.play_count_threshold = 0.9
        self.has_reached_threshold = False
        self.play_count_recorded = False

        # Queue manager
        self.queue_manager = QueueManager()
        self.repeat_mode = 0

        # Volume save debounce timer
        self.volume_save_timer = QTimer(self)
        self.volume_save_timer.setSingleShot(True)
        self.volume_save_timer.timeout.connect(self._save_volume_to_config)
        self.volume_save_delay = 500  # milliseconds

        # Initialize audio system - ensure this runs
        self.audio_initialized = self._initialize_audio_backend()

        # Update timer for position tracking
        self.position_timer = QTimer(self)
        self.position_timer.timeout.connect(self._update_position)
        self.position_timer.setInterval(100)

        self._internal_stop_signal.connect(
            self._handle_playback_finished, type=Qt.ConnectionType.QueuedConnection
        )
        self._is_advancing = False
        if not self.audio_initialized:
            logger.error("MusicPlayer failed to initialize audio backend")

    def toggle_play_pause(self):
        """Toggle between play and pause states."""
        if self.playing:
            if self.paused:
                self.play()  # Resume
            else:
                self.pause()  # Pause
        else:
            self.play()  # Start playing

    def increase_volume(self):
        """Increase volume by 5%."""
        new_volume = min(100, self.volume_slider.value() + 5)
        self.volume_slider.setValue(new_volume)

    def decrease_volume(self):
        """Decrease volume by 5%."""
        new_volume = max(0, self.volume_slider.value() - 5)
        self.volume_slider.setValue(new_volume)

    def seek_forward(self):
        """Seek forward 10 seconds."""
        if self.controller.mediaplayer.duration > 0:
            new_position = min(
                self.controller.mediaplayer.duration,
                self.controller.mediaplayer.position + 10000,
            )
            self.controller.mediaplayer.seek(new_position)

    def seek_backward(self):
        """Seek backward 10 seconds."""
        if self.controller.mediaplayer.duration > 0:
            new_position = max(0, self.controller.mediaplayer.position - 10000)
            self.controller.mediaplayer.seek(new_position)

    def _initialize_audio_backend(self):
        """Initialize audio backend with error handling."""
        try:
            # Try to import required libraries
            logger.info("Initializing audio backend...")

            # Import numpy first
            try:
                import numpy as np

                self.np = np
                logger.info("NumPy imported successfully")
            except ImportError as e:
                logger.error(f"Failed to import numpy: {e}")
                self.error_occurred.emit(
                    "NumPy not available. Please install: pip install numpy"
                )
                return False

            # Import sounddevice
            try:
                import sounddevice as sd

                self.sd = sd
                logger.info("SoundDevice imported successfully")
            except ImportError as e:
                logger.error(f"Failed to import sounddevice: {e}")
                self.error_occurred.emit(
                    "SoundDevice not available. Please install: pip install sounddevice"
                )
                return False

            # Import soundfile
            try:
                import soundfile as sf

                self.sf = sf
                logger.info("SoundFile imported successfully")
            except ImportError as e:
                logger.error(f"Failed to import soundfile: {e}")
                self.error_occurred.emit(
                    "SoundFile not available. Please install: pip install soundfile"
                )
                return False

            # Test audio device access
            try:
                devices = self.sd.query_devices()
                logger.info(f"Found {len(devices)} audio devices")
                self.available_devices = devices
                return True
            except Exception as e:
                logger.error(f"Failed to query audio devices: {e}")
                self.error_occurred.emit(f"Audio device error: {str(e)}")
                return False

        except Exception as e:
            logger.error(f"Audio backend initialization failed: {e}")
            self.error_occurred.emit(f"Audio system error: {str(e)}")
            return False

    def _get_audio_device_info(self):
        """Get optimal device settings for current mode with better buffer settings."""
        try:
            if not self.current_device:
                try:
                    default_info = self.sd.default.device
                    if default_info is not None:
                        default_device = default_info[1]  # output device index
                        if default_device is not None and default_device >= 0:
                            self.current_device = default_device
                            logger.info(f"Using default audio device: {default_device}")
                except Exception as e:
                    logger.warning(
                        f"Could not determine default device: {e}, will let sounddevice choose"
                    )

            if self.exclusive_mode and self.current_device:
                device_info = self.sd.query_devices(self.current_device)
                if device_info:
                    return {
                        "device": self.current_device,
                        "exclusive": self.exclusive_mode,
                        "latency": "low",
                        "blocksize": 2048,  # Increased buffer size
                    }

            # Use more conservative settings for better stability
            return {
                "device": self.current_device,
                "latency": "high",  # Better for stability
                "blocksize": 2048,  # Increased from 1024
                "clip_off": True,  # Prevent clipping issues
            }

        except Exception as e:
            logger.warning(f"Could not get device info: {e}, using defaults")
            return {"device": self.current_device, "blocksize": 2048, "latency": "high"}

    def load_track(self, file_path: Path) -> bool:
        """Load audio file for bit-perfect playback with optional normalization."""
        try:
            if not file_path.exists():
                self.error_occurred.emit(f"File not found: {file_path}")
                return False

            # Reset playback state
            self.stop()
            self.current_frame = 0
            self._position = 0
            self.crossfade_active = False
            self.next_audio_data = None

            # Reset play count tracking
            self.has_reached_threshold = False
            self.play_count_recorded = False

            logger.info(f"Loading track: {file_path}")

            # Determine file format and select appropriate decoder
            file_ext = file_path.suffix.lower()
            supported_formats = {".wav", ".flac", ".mp3", ".aiff", ".aif", ".ogg"}

            if file_ext not in supported_formats:
                self.error_occurred.emit(f"Unsupported audio format: {file_ext}")
                return False

            # Load audio data using soundfile
            try:
                self.audio_data, self.current_sample_rate = self.sf.read(
                    str(file_path),
                    always_2d=True,  # Ensure stereo output
                    dtype="float32",  # Use float32 for consistent processing
                )
            except Exception as e:
                self.error_occurred.emit(f"Failed to read audio file: {str(e)}")
                return False

            # Validate audio data
            if self.audio_data is None or len(self.audio_data) == 0:
                self.error_occurred.emit("Audio file appears to be empty or corrupted")
                return False

            # Get audio properties
            self.current_channels = (
                self.audio_data.shape[1] if len(self.audio_data.shape) > 1 else 1
            )
            self.current_bit_depth = 32  # Since we're using float32
            self.current_format = file_ext

            # Calculate duration in milliseconds
            if self.current_sample_rate > 0:
                self._duration = int(
                    len(self.audio_data) / self.current_sample_rate * 1000
                )
            else:
                self.error_occurred.emit("Invalid sample rate detected")
                return False

            # Update current file reference
            self.current_file = file_path
            self.track_changed.emit(file_path)
            self.duration_changed.emit(self._duration)

            # Apply normalization if enabled
            if self.normalization_enabled and self.audio_data is not None:
                # Log gain information for debugging
                track_gain, track_peak = self._get_track_gain_info()
                if track_gain is not None:
                    logger.info(
                        f"Using track gain: {track_gain} dB, peak: {track_peak}"
                    )
                else:
                    logger.info(
                        "No track gain metadata available, using fallback normalization"
                    )

                self.audio_data = self._normalize_audio(self.audio_data)

            logger.info(
                f"Track loaded: {file_path.name}, "
                f"{self.current_sample_rate}Hz, {self.current_bit_depth}bit, "
                f"{self.current_channels}ch, {self._duration}ms"
            )
            return True

        except Exception as e:
            error_msg = f"Failed to load track {file_path}: {str(e)}"
            logger.error(error_msg)
            self.error_occurred.emit(error_msg)
            return False

    def play(self):
        """Start audiophile-grade playback."""
        # Guard: ensure audio backend is initialized
        if self.sd is None:
            if not self._initialize_audio_backend():
                self.error_occurred.emit("Audio backend not available. Cannot play.")
                return
        # Check if we need to load a track from the queue first
        if self.current_file is None:
            track = self.queue_manager.get_current_track()
            if track:
                self.load_track(Path(track.track_file_path))
            else:
                self.error_occurred.emit("Queue is empty.")
                return

        # Reset play count tracking
        self.has_reached_threshold = False
        self.play_count_recorded = False

        try:
            # Stop any existing playback
            if self.audio_stream is not None:
                self.audio_stream.stop()
                self.audio_stream.close()

            # Get device configuration
            device_config = self._get_audio_device_info()

            # Create audio stream with optimal settings
            self.audio_stream = self.sd.OutputStream(
                samplerate=self.current_sample_rate,
                channels=self.current_channels,
                dtype=self.audio_data.dtype,
                device=device_config["device"],
                latency=device_config.get("latency", "high"),
                blocksize=1024,  # Good balance for most systems
                callback=self._audio_callback if not self.paused else None,
            )

            # Start playback
            self.audio_stream.start()
            self.playing = True
            self.paused = False
            self.state_changed.emit("playing")

            # Start position updates
            self.position_timer.start()

            logger.info(f"Started playback: {self.current_file.name}")

        except Exception as e:
            error_msg = f"Playback error: {str(e)}"
            logger.error(error_msg)
            self.error_occurred.emit(error_msg)
            self.playing = False

    def _audio_callback(self, outdata, frames, time, status):
        """Callback with an added epsilon to prevent end-of-track hanging."""
        if status:
            logger.warning(f"Audio callback status: {status}")

        if not self.playing or self.audio_data is None:
            outdata.fill(0)
            return

        try:
            if self.paused:
                outdata.fill(0)
                return

            total_frames = len(self.audio_data)
            frames_remaining = total_frames - self.current_frame

            # FAILSAFE: If remaining frames are less than 1ms of audio or
            # less than a tiny fraction of the buffer, consider it finished.
            # This prevents the stream from hanging on the very last buffer.
            if self.current_frame >= len(self.audio_data):
                logger.info("End reached. Emitting internal stop signal.")
                self._internal_stop_signal.emit()  # This is thread-safe
                return

            # Crossfade trigger logic
            should_start_crossfade = (
                self.crossfade_enabled
                and self.next_audio_data is not None
                and frames_remaining <= self.crossfade_frames
                and not self.crossfade_active
            )

            if should_start_crossfade:
                self.crossfade_active = True
                self.crossfade_start_frame = self.current_frame
                logger.debug("Starting crossfade transition")

            # Handle data delivery
            if self.crossfade_active and self.next_audio_data is not None:
                self._handle_crossfade_playback(outdata, frames)
            else:
                self._handle_normal_playback(outdata, frames)

        except Exception as e:
            logger.error(f"Audio callback error: {e}")
            outdata.fill(0)

    def _handle_normal_playback(self, outdata, frames):
        """Handle normal playback with immediate finish triggering."""

        # Calculate frames we can actually provide
        available_frames = len(self.audio_data) - self.current_frame
        frames_to_write = min(frames, available_frames)

        if frames_to_write > 0:
            # We have data to write
            chunk = self.audio_data[
                self.current_frame : self.current_frame + frames_to_write
            ]

            # Processing (Equalizer/Normalization) ...
            if frames_to_write >= 32 and not self.exclusive_mode:
                if self.normalization_enabled:
                    chunk = self._normalize_audio(chunk)
                # ... eq logic ...
                volume_factor = self.volume_level / 100.0
                chunk = chunk * volume_factor

            # Write to output buffer
            outdata[:frames_to_write] = chunk

            # Fill the rest with zeros if we reached the end in this very block
            if frames_to_write < frames:
                outdata[frames_to_write:] = 0

            self.current_frame += frames_to_write

            # CRITICAL FIX: If we just wrote the last frame, finish NOW.
            # Don't wait for the next callback.
            if self.current_frame >= len(self.audio_data):
                logger.info(
                    f"End of track reached at frame {self.current_frame}. Triggering finish."
                )
                QTimer.singleShot(0, self._handle_playback_finished)

        else:
            # We were already at the end when callback started
            logger.info("Callback called but no frames remaining.")
            outdata.fill(0)
            QTimer.singleShot(0, self._handle_playback_finished)

    def _handle_crossfade_playback(self, outdata, frames):
        """Handle playback during crossfade."""
        # Calculate crossfade progress
        frames_into_crossfade = self.current_frame - self.crossfade_start_frame
        fade_progress = min(1.0, frames_into_crossfade / self.crossfade_frames)

        # Get current track chunk
        current_frames_available = len(self.audio_data) - self.current_frame
        current_frames_to_read = min(frames, current_frames_available)

        if current_frames_to_read > 0:
            current_chunk = self.audio_data[
                self.current_frame : self.current_frame + current_frames_to_read
            ]
        else:
            current_chunk = np.zeros(
                (frames, self.current_channels), dtype=self.audio_data.dtype
            )

        # Get next track chunk
        next_frames_to_read = min(frames, len(self.next_audio_data))
        next_chunk = self.next_audio_data[:next_frames_to_read]

        # Ensure compatible shapes
        min_frames = min(len(current_chunk), len(next_chunk))
        if min_frames > 0:
            current_chunk = current_chunk[:min_frames]
            next_chunk = next_chunk[:min_frames]

            # Apply crossfade
            outdata_chunk = self._apply_crossfade(
                current_chunk, next_chunk, fade_progress
            )
            outdata[: len(outdata_chunk)] = outdata_chunk

            if len(outdata_chunk) < frames:
                outdata[len(outdata_chunk) :] = 0
        else:
            outdata.fill(0)

        self.current_frame += current_frames_to_read

        # Check if crossfade complete
        if fade_progress >= 1.0 or current_frames_to_read == 0:
            self._switch_to_next_track()

    def _switch_to_next_track(self):
        """Switch to next track after crossfade."""
        try:
            if self.next_audio_data is not None:
                # Switch to next track
                self.audio_data = self.next_audio_data
                self.current_frame = 0
                self._position = 0
                self.crossfade_active = False
                self.next_audio_data = None

                # Update current file reference and emit track change
                next_track = self.queue_manager.get_next_track()
                if next_track and hasattr(next_track, "track_file_path"):
                    self.current_file = Path(next_track.track_file_path)
                    self.track_changed.emit(self.current_file)
                    logger.info(
                        f"Crossfade completed, switched to: {self.current_file.name}"
                    )

        except Exception as e:
            logger.error(f"Track switch error: {e}")
            QTimer.singleShot(0, self._handle_playback_finished)

    def _handle_playback_finished(self):
        """Handle end of playback - Refactored to ensure advancement."""
        logger.debug("Handling playback finished logic.")

        # 1. Record play count if threshold was met
        if (
            self.current_file
            and not self.play_count_recorded
            and self.has_reached_threshold
        ):
            self._increment_play_count()

        # 2. Reset internal playback flags BEFORE calling next
        # This prevents the new track from thinking it's already finished
        self.playing = False
        self.has_reached_threshold = False
        self.play_count_recorded = False

        if self.repeat_mode == 1:  # Repeat One
            logger.debug("Repeat Mode: One. Restarting.")
            self.seek(0)
            self.play()
        else:
            logger.debug("Advancing to next track.")
            self.play_next()

    def pause(self):
        """Pause playback."""
        if self.playing and not self.paused:
            self.paused = True
            self.state_changed.emit("paused")
            self.position_timer.stop()
            logger.debug("Playback paused")

    def stop(self):
        """Stop playback completely and reset crossfade state."""
        self.playing = False
        self.paused = False
        self.state_changed.emit("stopped")
        self.position_timer.stop()

        # Reset crossfade state
        self.crossfade_active = False
        self.next_audio_data = None

        if self.audio_stream is not None:
            try:
                self.audio_stream.stop()
                self.audio_stream.close()
            except:  # noqa: E722
                pass
            finally:
                self.audio_stream = None

        # Reset play count tracking
        self.has_reached_threshold = False
        self.play_count_recorded = False

        logger.debug("Playback stopped")

    def seek(self, position: int):
        """Seek to position in milliseconds."""
        try:
            if self.audio_data is not None and self.current_sample_rate > 0:
                # Convert ms to frames
                target_frame = int(position / 1000.0 * self.current_sample_rate)
                target_frame = max(0, min(target_frame, len(self.audio_data) - 1))

                self.current_frame = target_frame
                self._position = position

                # Reset threshold tracking when user seeks
                if self._duration > 0:
                    playback_ratio = position / self._duration
                    if playback_ratio < self.play_count_threshold:
                        self.has_reached_threshold = False
                        self.play_count_recorded = False

                logger.debug(f"Seeked to: {position}ms (frame {target_frame})")

        except Exception as e:
            logger.error(f"Seek error: {str(e)}")

    def _update_position(self):
        """Update position and check play count threshold."""
        if not self.playing or self.paused or self.audio_data is None:
            return

        # Calculate current position
        if self.current_sample_rate > 0:
            self._position = int(self.current_frame / self.current_sample_rate * 1000)
            self.position_changed.emit(self._position)

        # Play count threshold checking
        if (
            self.current_file
            and self._duration > 0
            and not self.play_count_recorded
            and self.playing
        ):
            playback_ratio = self._position / self._duration

            if (
                playback_ratio >= self.play_count_threshold
                and not self.has_reached_threshold
            ):
                self.has_reached_threshold = True
                self._increment_play_count()

            elif (
                playback_ratio < self.play_count_threshold
                and self.has_reached_threshold
            ):
                self.has_reached_threshold = False
                self.play_count_recorded = False

    def set_volume(self, value: int):
        """Set volume level (0-100) with debounced save to config."""
        try:
            old_volume = self.volume_level
            self.volume_level = max(0, min(100, value))

            # Only emit and save if volume actually changed
            if old_volume != self.volume_level:
                self.volume_changed.emit(self.volume_level)
                logger.debug(f"Volume changed from {old_volume} to {self.volume_level}")

                # Debounce save to config
                self.volume_save_timer.start(self.volume_save_delay)

        except Exception as e:
            logger.error(f"Volume set error: {str(e)}")

    def set_exclusive_mode(self, enabled: bool):
        """Enable/disable exclusive mode."""
        if self.exclusive_mode != enabled:
            self.exclusive_mode = enabled
            self.playback_mode_changed.emit("exclusive" if enabled else "shared")

            # Restart playback with new mode if needed
            if self.current_file and self.playing:
                current_pos = self._position
                was_playing = self.playing

                self.stop()
                if was_playing:
                    self.seek(current_pos)
                    self.play()

    def set_audio_device(self, device_name: str):
        """Change audio output device."""
        if device_name != self.current_device:
            self.current_device = device_name
            self.audio_device_changed.emit(device_name)

            # Restart playback with new device if needed
            if self.current_file and self.playing:
                current_pos = self._position
                was_playing = self.playing

                self.stop()
                if was_playing:
                    self.seek(current_pos)
                    self.play()

    def get_audio_devices(self):
        """Get list of available audio devices."""
        try:
            devices = []
            for i, device in enumerate(self.available_devices):
                if device["max_output_channels"] > 0:  # Output devices only
                    devices.append(
                        {
                            "id": i,
                            "name": device["name"],
                            "default": device.get("default", False),
                        }
                    )
            return devices
        except Exception as e:
            logger.error(f"Error getting audio devices: {e}")
            return []

    def play_next(self):
        """Advances queue with a lock to prevent double-advancing."""
        if self._is_advancing:
            logger.debug("play_next already in progress, skipping duplicate call.")
            return

        try:
            self._is_advancing = True
            logger.info("Advancing queue...")

            # Move the window
            self.queue_manager.advance_queue()
            track = self.queue_manager.get_current_track()

            if track:
                # We use a temporary flag to prevent stop() from re-triggering playback_finished
                self.load_track(Path(track.track_file_path))
                self.play()
            else:
                self.stop()

        finally:
            # Small delay before releasing the lock to catch rapid-fire signals
            QTimer.singleShot(500, self._reset_advance_lock)

    def _reset_advance_lock(self):
        self._is_advancing = False

    def play_previous(self):
        """Logic to handle the 'Back' button using the buffer."""
        if self.queue_manager.get_queue_length() >= 2:
            prev_track = self.queue_manager.previous_track_in_queue()

            if prev_track:
                self.load_track(Path(prev_track.track_file_path))
                self.play()

    def set_repeat_mode(self, mode):
        """Set repeat mode (0=none, 1=one, 2=all)."""
        self.repeat_mode = mode

    def _increment_play_count(self):
        """Increment play count for current track."""
        try:
            if self.current_file and not self.play_count_recorded:
                track = self.controller.get.get_entity_object(
                    "Track", track_file_path=str(self.current_file)
                )
                if track:
                    current_play_count = getattr(track, "play_count", 0) or 0
                    new_play_count = current_play_count + 1
                    current_datetime = datetime.now()

                    track_id = getattr(track, "track_id", None)
                    if track_id:
                        self.controller.update.update_entity(
                            "Track",
                            track_id,
                            play_count=new_play_count,
                            last_listened_date=current_datetime,
                        )
                        self.play_count_recorded = True
                        logger.info(f"Incremented play count to: {new_play_count}")
                        self.play_count_updated.emit(self.current_file, new_play_count)
        except Exception as e:
            logger.error(f"Error incrementing play count: {e}")

    def enable_crossfade(self, enabled: bool):
        """Enable or disable crossfade between tracks."""
        self.crossfade_enabled = enabled
        logger.info(f"Crossfade {'enabled' if enabled else 'disabled'}")

    def set_crossfade_duration(self, duration_ms: int):
        """Set crossfade duration in milliseconds."""
        self.crossfade_duration = max(
            0, min(10000, duration_ms)
        )  # Limit to 0-10 seconds
        logger.info(f"Crossfade duration set to {self.crossfade_duration}ms")

    def enable_normalization(self, enabled: bool):
        """Enable or disable audio normalization."""
        self.normalization_enabled = enabled
        logger.info(f"Audio normalization {'enabled' if enabled else 'disabled'}")

    def set_normalization_target(self, target_lufs: float):
        """Set normalization target in LUFS."""
        self.normalization_target = max(
            -50.0, min(-5.0, target_lufs)
        )  # Reasonable range
        logger.info(f"Normalization target set to {self.normalization_target} LUFS")

    def _calculate_loudness(self, audio_data: np.ndarray) -> float:
        """Calculate integrated loudness - only used as fallback when track_gain is not available."""
        try:
            if len(audio_data) == 0:
                return self.normalization_target

            # Calculate RMS as fallback
            rms = np.sqrt(np.mean(audio_data**2))

            if rms > 0:
                loudness_lufs = 20 * np.log10(rms)
            else:
                loudness_lufs = -70

            return loudness_lufs

        except Exception as e:
            logger.error(f"Loudness calculation error: {e}")
            return self.normalization_target

    def _get_track_gain_info(self) -> tuple:
        """Get track gain and peak information from current track metadata."""
        try:
            if self.current_file is None:
                return None, None

            # Get track entity from database
            track = self.controller.get.get_entity_object(
                "Track", track_file_path=str(self.current_file)
            )

            if track:
                track_gain = getattr(track, "track_gain", None)
                track_peak = getattr(track, "track_peak", None)

                # Return values if they exist and are valid
                if track_gain is not None and track_peak is not None:
                    logger.debug(
                        f"Using track gain: {track_gain} dB, peak: {track_peak}"
                    )
                    return float(track_gain), float(track_peak)

            logger.debug("No track gain metadata available, using fallback")
            return None, None

        except Exception as e:
            logger.error(f"Error getting track gain info: {e}")
            return None, None

    def _normalize_audio(self, audio_data: np.ndarray) -> np.ndarray:
        """Normalize audio using track_gain and track_peak metadata when available."""
        if not self.normalization_enabled or audio_data is None:
            return audio_data

        try:
            # Try to use pre-calculated track gain first
            track_gain, track_peak = self._get_track_gain_info()

            if track_gain is not None and track_peak is not None:
                # Use ReplayGain metadata
                gain_factor = 10 ** ((self.normalization_target + track_gain) / 20)

                # Apply gain with peak protection
                normalized_audio = audio_data * gain_factor

                # Prevent clipping using track_peak
                if track_peak > 0:
                    max_peak = np.max(np.abs(normalized_audio))
                    if max_peak > 1.0:
                        # Scale down to prevent clipping
                        safety_factor = 0.99 / max_peak  # Leave 1% headroom
                        normalized_audio = normalized_audio * safety_factor

                logger.debug(
                    f"Normalized using ReplayGain: {track_gain:.1f} dB -> target {self.normalization_target} LUFS"
                )

            else:
                # Fallback to loudness calculation
                current_loudness = self._calculate_loudness(audio_data)
                loudness_diff = self.normalization_target - current_loudness

                # Calculate gain factor
                gain_factor = 10 ** (loudness_diff / 20)

                # Apply gain with soft clipping
                normalized_audio = audio_data * gain_factor
                threshold = 0.95
                normalized_audio = np.tanh(normalized_audio * threshold) / threshold

                logger.debug(
                    f"Normalized using fallback: {current_loudness:.1f} LUFS -> {self.normalization_target:.1f} LUFS"
                )

            return normalized_audio

        except Exception as e:
            logger.error(f"Audio normalization error: {e}")
            return audio_data

    def _prepare_crossfade(self, next_file_path: Path):
        """Pre-load next track for crossfade with normalization."""
        try:
            if not self.crossfade_enabled or self.crossfade_duration == 0:
                self.next_audio_data = None
                return

            # Load next track audio data
            next_audio_data, next_sample_rate = self.sf.read(
                str(next_file_path),
                always_2d=True,
            )

            # Ensure sample rate matches current track
            if next_sample_rate != self.current_sample_rate:
                logger.warning("Sample rate mismatch for crossfade")
                self.next_audio_data = None
                return

            # Normalize next track if enabled
            if self.normalization_enabled:
                # Temporarily set current_file to get gain info for next track
                original_file = self.current_file
                self.current_file = next_file_path
                next_audio_data = self._normalize_audio(next_audio_data)
                self.current_file = original_file  # Restore original

            self.next_audio_data = next_audio_data
            self.crossfade_frames = int(
                self.crossfade_duration / 1000 * self.current_sample_rate
            )

            logger.debug(f"Prepared crossfade: {self.crossfade_frames} frames")

        except Exception as e:
            logger.error(f"Crossfade preparation error: {e}")
            self.next_audio_data = None

    def _apply_crossfade(
        self, current_chunk: np.ndarray, next_chunk: np.ndarray, fade_progress: float
    ) -> np.ndarray:
        """Apply crossfade between current and next track chunks."""
        try:
            if current_chunk.shape != next_chunk.shape:
                # Ensure compatible shapes
                min_frames = min(len(current_chunk), len(next_chunk))
                current_chunk = current_chunk[:min_frames]
                next_chunk = next_chunk[:min_frames]

            # Calculate fade curves
            current_gain = np.cos(fade_progress * np.pi / 2)  # Fade out current
            next_gain = np.sin(fade_progress * np.pi / 2)  # Fade in next

            # Apply crossfade
            crossfaded_chunk = current_chunk * current_gain + next_chunk * next_gain

            return crossfaded_chunk

        except Exception as e:
            logger.error(f"Crossfade application error: {e}")
            return current_chunk  # Fallback to current chunk

    def _save_volume_to_config(self):
        """Save current volume to configuration file."""
        try:
            from config_setup import app_config

            app_config.set_volume(self.volume_level)
            app_config.save()
            logger.info(f"Volume {self.volume_level} saved to config")
        except Exception as e:
            logger.error(f"Failed to save volume to config: {e}")

    def __del__(self):
        """Cleanup resources and ensure volume is saved."""
        # Save volume one last time before destruction
        try:
            if hasattr(self, "volume_level"):
                from config_setup import app_config

                app_config.set_volume(self.volume_level)
                app_config.save()
                logger.info(f"Final volume {self.volume_level} saved to config")
        except Exception as e:
            logger.error(f"Error saving final volume: {e}")

        self.stop()

    @property
    def duration(self) -> int:
        return self._duration

    @property
    def state(self) -> str:
        """Get current playback state as string."""
        if not self.playing:
            return "stopped"
        elif self.paused:
            return "paused"
        else:
            return "playing"

    @property
    def position(self) -> int:
        return self._position

    @property
    def volume(self) -> int:
        return self.volume_level
