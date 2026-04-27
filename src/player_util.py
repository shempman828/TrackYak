"""
player_util.py — MusicPlayer
Streaming music playback engine.
"""

import collections
import threading
import time
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np
from PySide6.QtCore import QObject, QTimer, Signal

from src.config_setup import app_config
from src.equalizer_utility import EqualizerUtility
from src.logger_config import logger
from src.queue_utility import QueueManager

# ─────────────────────────────────────────────────────────────────────────────
#  Constants
# ─────────────────────────────────────────────────────────────────────────────

BLOCKSIZE = 16384  # Frames per audio callback buffer. Larger = more stable.
READ_AHEAD_BLOCKS = 16  # How many blocks to read ahead into the ring buffer.
POSITION_INTERVAL_MS = 50  # UI position update interval (20 fps).
PLAY_COUNT_THRESHOLD = 0.90
RESTART_THRESHOLD_MS = 10_000
BUFFER_QUEUE_SIZE = 20
PREFILL_BLOCKS = 8  # blocks to decode ahead of the callback

SUPPORTED_FORMATS = {".wav", ".flac", ".mp3", ".aiff", ".aif", ".ogg"}


# ─────────────────────────────────────────────────────────────────────────────
#  MusicPlayer
# ─────────────────────────────────────────────────────────────────────────────


class MusicPlayer(QObject):
    """
    Streaming music player.  Reads audio from disk in small chunks so RAM usage
    stays flat regardless of file size or library size.
    """

    # ── Signals ───────────────────────────────────────────────────────────────
    position_changed = Signal(int)
    duration_changed = Signal(int)
    state_changed = Signal(str)
    volume_changed = Signal(int)
    error_occurred = Signal(str)
    track_changed = Signal(Path)
    play_count_updated = Signal(Path, int)
    audio_device_changed = Signal(str)
    playback_mode_changed = Signal(str)
    track_metadata_loaded = Signal(Path, dict)  # Path and metadata dict

    # Cross-thread signal: audio callback → main thread track advancement.
    # Must use QueuedConnection (see __init__).
    _track_finished = Signal()

    # ── Stubs for crossfade API (kept so existing UI code doesn't break) ──────
    crossfade_enabled = False
    crossfade_duration = 0

    def enable_crossfade(self, enabled: bool):
        """Crossfade is not supported in streaming mode — call is silently ignored."""
        pass

    def set_crossfade_duration(self, duration_ms: int):
        """Crossfade is not supported in streaming mode — call is silently ignored."""
        pass

    # ── Init ──────────────────────────────────────────────────────────────────

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.equalizer = EqualizerUtility(self)
        self.queue_manager = QueueManager()
        self.queue_manager.load_queue_from_config(app_config)

        # ── Audio backend ──────────────────────────────────────────────────────
        self.sd = None
        self.sf = None
        self.audio_stream: Optional[object] = None

        # ── Current track state ───────────────────────────────────────────────
        # We keep a SoundFile reader open instead of the whole array.
        self.current_file: Optional[Path] = None
        self._sf_reader: Optional[object] = None  # soundfile.SoundFile
        self.current_sample_rate: int = 44100
        self.current_channels: int = 2
        self.current_bit_depth: int = 32
        self.current_format: Optional[str] = None

        self._total_frames: int = 0  # total frames in the file
        self._current_frame: int = 0  # how many frames we have read so far
        self._frames_played: int = 0  # how many frames the audio callback has output

        # Lock protecting _sf_reader and _current_frame from concurrent access
        # between the audio callback thread and the main thread (seek).
        self._reader_lock = threading.Lock()
        self._audio_buffer: collections.deque = collections.deque()
        self._buffer_lock = threading.Lock()
        self._reader_thread: Optional[threading.Thread] = None
        self._reader_stop = threading.Event()

        # ── Pre-load: next track ──────────────────────────────────────────────
        # We open the *next* track's SoundFile in a background thread so it is
        # ready before the current track ends, giving zero-gap transitions.
        self._next_sf_reader: Optional[object] = None
        self._next_file: Optional[Path] = None
        self._next_sample_rate: int = 0
        self._next_channels: int = 0
        self._next_total_frames: int = 0
        self._next_gain_factor: float = 1.0
        self._preload_lock = threading.Lock()
        self._preload_thread: Optional[threading.Thread] = None

        # ── Gain ──────────────────────────────────────────────────────────────
        self._gain_factor: float = 1.0

        # ── Playback state ────────────────────────────────────────────────────
        self.playing: bool = False
        self.paused: bool = False
        self._position: int = 0  # ms
        self._duration: int = 0  # ms
        self.repeat_mode: int = 0  # 0=none, 1=one, 2=all

        self._is_advancing: bool = False
        self._stream_generation: int = 0
        self._finish_pending = threading.Event()  # thread-safe flag for end-of-stream
        self._stream_close_event = threading.Event()  # set when async close completes
        self._stream_close_event.set()  # starts "set" (no close in progress)

        # ── Volume ────────────────────────────────────────────────────────────
        self.volume_level: int = app_config.get_volume()
        self._volume_save_timer = QTimer(self)
        self._volume_save_timer.setSingleShot(True)
        self._volume_save_timer.timeout.connect(self._save_volume_to_config)

        # ── Play count ────────────────────────────────────────────────────────
        self._has_reached_threshold: bool = False
        self._play_count_recorded: bool = False

        # ── Normalization ─────────────────────────────────────────────────────
        self.normalization_enabled: bool = False
        self.normalization_target: float = -14.0  # LUFS (music streaming standard)

        # ── Audio device ──────────────────────────────────────────────────────
        self.exclusive_mode: bool = False
        self.current_device = None
        self.available_devices: list = []

        # ── Position timer ────────────────────────────────────────────────────
        self._position_timer = QTimer(self)
        self._position_timer.setInterval(POSITION_INTERVAL_MS)
        self._position_timer.timeout.connect(self._update_position)

        # Wire cross-thread finish signal with QueuedConnection so it always
        # runs on the main thread regardless of which thread emits it.
        from PySide6.QtCore import Qt as _Qt

        self._track_finished.connect(
            self._handle_playback_finished,
            type=_Qt.ConnectionType.QueuedConnection,
        )

        # ── Boot ──────────────────────────────────────────────────────────────
        self._audio_initialized = self._initialize_audio_backend()
        if not self._audio_initialized:
            logger.error("MusicPlayer: audio backend failed to initialize")

    def _start_reader_thread(self):
        """Start background thread that decodes audio into the buffer."""
        self._reader_stop.clear()
        with self._buffer_lock:
            self._audio_buffer.clear()
        self._reader_thread = threading.Thread(
            target=self._reader_loop, daemon=True, name="AudioReader"
        )
        self._reader_thread.start()

    def _stop_reader_thread(self):
        """Signal the reader thread to stop and wait briefly."""
        self._reader_stop.set()
        if self._reader_thread and self._reader_thread.is_alive():
            self._reader_thread.join(timeout=2.0)
        self._reader_thread = None
        with self._buffer_lock:
            self._audio_buffer.clear()

    def _reader_loop(self):
        """
        Background thread: reads BLOCKSIZE chunks from the SoundFile and
        pushes them into _audio_buffer. Sleeps when the buffer is full.
        """
        while not self._reader_stop.is_set():
            with self._buffer_lock:
                buf_len = len(self._audio_buffer)

            if buf_len >= PREFILL_BLOCKS:
                self._reader_stop.wait(timeout=0.02)
                continue

            # Hold the reader lock for the entire read so a seek on the main
            # thread can't move the file cursor between our frame check and
            # our reader.read() call — that interleaving is what causes FLAC
            # desync and bad header errors when skipping quickly.
            with self._reader_lock:
                reader = self._sf_reader
                frames_remaining = self._total_frames - self._current_frame
                if frames_remaining <= 0 or reader is None:
                    break
                to_read = min(BLOCKSIZE, frames_remaining)
                try:
                    chunk = reader.read(to_read, dtype="float32", always_2d=True)
                    self._current_frame += len(chunk)
                except Exception as exc:
                    logger.error(f"Reader thread read error: {exc}")
                    break

            with self._buffer_lock:
                self._audio_buffer.append(chunk)

    # =========================================================================
    #  Public playback controls
    # =========================================================================

    def play(self):
        logger.debug(f"play() ENTER at {time.time():.3f}")
        if self.sd is None:
            if not self._initialize_audio_backend():
                self.error_occurred.emit("Audio backend not available.")
                return

        if self.current_file is None or self._sf_reader is None:
            track = self.queue_manager.get_current_track()
            if track:
                if not self.load_track(Path(track.track_file_path)):
                    return
            else:
                self.error_occurred.emit("Queue is empty.")
                return

        self._has_reached_threshold = False
        self._play_count_recorded = False

        try:
            if self.paused and self.audio_stream is not None:
                self.paused = False
                self.playing = True
                self.state_changed.emit("playing")
                self._position_timer.start()
                logger.info("Playback resumed")
                return

            # ── Reuse existing stream if sample rate and channels match ──────────
            if (
                self.audio_stream is not None
                and self.audio_stream.samplerate == self.current_sample_rate
                and self.audio_stream.channels == self.current_channels
            ):
                # Stream already open and compatible — clear finish flag and go.
                # The reader thread was already started by load_track(), so the
                # buffer is being filled. We just need to let the callback run.
                self._finish_pending.clear()
                self.playing = True
                self.paused = False
                self.state_changed.emit("playing")
                self._position_timer.start()
                logger.info(
                    f"Playback continued on existing stream: {self.current_file.name}"
                )
                logger.debug(f"play() EXIT at {time.time():.3f}")
                return

            # ── Open a new stream (first play, or sample rate/channel count changed) ─
            self._close_stream()
            self._stream_generation += 1
            my_generation = self._stream_generation
            self._finish_pending.clear()

            device_config = self._get_device_config()

            def _stamped_callback(outdata, frames, time, status, _gen=my_generation):
                self._audio_callback(outdata, frames, time, status, _gen)

            self.audio_stream = self.sd.OutputStream(
                samplerate=self.current_sample_rate,
                channels=self.current_channels,
                dtype="float32",
                device=device_config["device"],
                latency=device_config.get("latency", "high"),
                blocksize=BLOCKSIZE,
                callback=_stamped_callback,
            )
            self._start_reader_thread()
            self.audio_stream.start()

            self.playing = True
            self.paused = False
            self.state_changed.emit("playing")
            self._position_timer.start()
            logger.info(f"Playback started: {self.current_file.name}")
            logger.debug(f"play() EXIT at {time.time():.3f}")

        except Exception as exc:
            msg = f"Playback error: {exc}"
            logger.error(msg)
            self.error_occurred.emit(msg)
            self.playing = False

    def pause(self):
        """Pause playback.
        Stream stays open; callback outputs silence."""
        if self.playing and not self.paused:
            self.paused = True
            self.state_changed.emit("paused")
            self._position_timer.stop()
            logger.debug("Playback paused")

    def stop(self):
        """Stop playback and reset to the beginning."""
        self.playing = False
        self.paused = False
        self._finish_pending.set()
        self._position_timer.stop()
        self._has_reached_threshold = False
        self._play_count_recorded = False
        # Reset the reader cursor to the beginning of the track
        self._stop_reader_thread()
        with self._reader_lock:
            if self._sf_reader is not None:
                try:
                    self._sf_reader.seek(0)
                    self._current_frame = 0
                except Exception:
                    pass
        self._frames_played = 0
        with self._buffer_lock:
            self._audio_buffer.clear()
        self._position = 0
        # Close the stream so play() opens a fresh one from frame 0
        self._close_stream()
        self.state_changed.emit("stopped")
        logger.debug("Playback stopped")

    def _force_close_stream(self):
        """Actually close the audio stream — only call on device change or app exit."""
        self._close_stream()

    def toggle_play_pause(self):
        if self.paused:
            self.play()
        elif self.playing:
            self.pause()
        else:
            self.play()

    def play_next(self):
        logger.debug(f"play_next ENTER {time.time()}")
        if self._is_advancing:
            return
        self._is_advancing = True
        try:
            logger.info("Advancing to next track...")
            self.queue_manager.advance_queue()
            track = self.queue_manager.get_current_track()
            if track:
                if self.load_track(Path(track.track_file_path)):
                    self.play()
                else:
                    self._is_advancing = False
            else:
                self.stop()
        except Exception as exc:
            logger.error(f"play_next error: {exc}")
        finally:
            self._is_advancing = False

    def play_previous(self):
        """Go to the previous track, or restart the current one."""
        if self._is_advancing:
            return

        self._is_advancing = True
        try:
            # If we're more than 3 seconds in, just restart the current track.
            if self._position > RESTART_THRESHOLD_MS and self.current_file is not None:
                logger.info("play_previous: restarting current track")
                self.seek(0)
                if not self.playing:
                    self.play()
                return

            # go_to_previous() pops history[-1] and inserts it at queue[0].
            # If there is no history it returns False.
            went_back = self.queue_manager.go_to_previous()
            if went_back:
                track = self.queue_manager.get_current_track()
                if track and self.load_track(Path(track.track_file_path)):
                    self.play()
                else:
                    self._is_advancing = False
            else:
                # No history — just restart.
                self.seek(0)
                if not self.playing:
                    self.play()
        except Exception as exc:
            logger.error(f"play_previous error: {exc}")
        finally:
            self._is_advancing = False

    def seek(self, position_ms: int):
        """Seek to position in milliseconds."""
        if self._sf_reader is None or self.current_sample_rate == 0:
            return
        try:
            target_frame = int(position_ms / 1000.0 * self.current_sample_rate)
            target_frame = max(0, min(target_frame, self._total_frames - 1))

            # Stop the reader thread so it isn't mid-read when we move the file cursor.
            self._stop_reader_thread()

            with self._reader_lock:
                self._sf_reader.seek(target_frame)
                self._current_frame = target_frame

            self._position = position_ms
            self._frames_played = int(position_ms / 1000.0 * self.current_sample_rate)

            if (
                self._duration > 0
                and (position_ms / self._duration) < PLAY_COUNT_THRESHOLD
            ):
                self._has_reached_threshold = False
                self._play_count_recorded = False

            # Restart the reader thread so the buffer refills from the new position.
            self._start_reader_thread()

            logger.debug(f"Seek to {position_ms}ms (frame {target_frame})")
        except Exception as exc:
            logger.error(f"Seek error: {exc}")

    # ── Volume ─────────────────────────────────────────────────────────────────

    def set_volume(self, value: int):
        new_val = max(0, min(100, value))
        if new_val != self.volume_level:
            self.volume_level = new_val
            self.volume_changed.emit(self.volume_level)
            self._volume_save_timer.start(500)

    def increase_volume(self):
        self.set_volume(self.volume_level + 5)

    def decrease_volume(self):
        self.set_volume(self.volume_level - 5)

    def seek_forward(self):
        if self._duration > 0:
            self.seek(min(self._duration, self._position + 10_000))

    def seek_backward(self):
        if self._duration > 0:
            self.seek(max(0, self._position - 10_000))

    def set_repeat_mode(self, mode: int):
        self.repeat_mode = mode
        logger.debug(f"Repeat mode: {mode}")

    # =========================================================================
    #  Track loading
    # =========================================================================
    def _resolve_path(self, file_path: Path) -> Optional[Path]:
        """Return a Path that exists on disk, trying Unicode normalization forms if needed."""
        if file_path.exists():
            return file_path
        for form in ("NFC", "NFD", "NFKC", "NFKD"):
            normalized = Path(unicodedata.normalize(form, str(file_path)))
            if normalized.exists():
                return normalized
        return None

    def load_track(self, file_path: Path) -> bool:
        """
        Open an audio file for streaming playback.

        This is fast — it only opens the file and reads its metadata header.
        No audio data is decoded until the audio callback starts pulling chunks.
        Returns True on success, False on failure.
        """
        logger.debug(f"load_track ENTER {time.time()}")
        resolved = self._resolve_path(file_path)
        if resolved is None:
            logger.error(f"File not found (exists=False): {file_path!r}")
            self.error_occurred.emit(f"File not found: {file_path}")
            return False
        file_path = resolved  # use the resolved path for everything below

        if file_path.suffix.lower() not in SUPPORTED_FORMATS:
            self.error_occurred.emit(f"Unsupported format: {file_path.suffix}")
            return False

        # Stop current playback state flags (stream stays open).
        self.playing = False
        self._position_timer.stop()
        self._finish_pending.set()  # Prevent double-fire from old track
        self._has_reached_threshold = False
        self._play_count_recorded = False
        self._position = 0
        self._frames_played = 0

        # Stop the reader thread BEFORE swapping the file, so it can't race
        # against us while we close the old reader and open the new one.
        self._stop_reader_thread()

        logger.info(f"Opening: {file_path}")

        try:
            new_reader = None
            new_sr = 0
            new_ch = 0
            new_frames = 0
            # Check if we pre-loaded this exact file
            with self._preload_lock:
                if self._next_file == file_path and self._next_sf_reader is not None:
                    # Swap in the pre-loaded reader — zero disk latency
                    new_reader = self._next_sf_reader
                    new_sr = self._next_sample_rate
                    new_ch = self._next_channels
                    new_frames = self._next_total_frames
                    self._next_sf_reader = None
                    self._next_file = None
                    self._next_sample_rate = 0
                    self._next_channels = 0
                    self._next_total_frames = 0
                    logger.info("Using pre-loaded reader for instant start")
                else:
                    # Open fresh
                    new_reader = self.sf.SoundFile(str(file_path), mode="r")
                    new_sr = new_reader.samplerate
                    new_ch = new_reader.channels
                    new_frames = len(new_reader)

            # Swap in the new reader and close the old one.
            old_reader = None
            with self._reader_lock:
                old_reader = self._sf_reader
                self._sf_reader = new_reader
                self._current_frame = 0
                self.current_sample_rate = new_sr
                self.current_channels = new_ch
                self._total_frames = new_frames

            if old_reader is not None:
                try:
                    old_reader.close()
                except Exception:
                    pass

            logger.debug(f"file opened at {time.time()}")
            self.current_file = file_path
            self.current_format = file_path.suffix.lower()
            self.current_bit_depth = 32
            self._duration = int(new_frames / new_sr * 1000)
            self._position = 0
            self._frames_played = 0

            self.equalizer.set_sample_rate(new_sr)
            self._gain_factor = self._calculate_gain_factor()

            self.track_changed.emit(file_path)
            logger.debug(f"track_changed emitted at {time.time()}")
            self.duration_changed.emit(self._duration)

            logger.info(
                f"Loaded: {file_path.name} | {new_sr}Hz | {new_ch}ch | "
                f"{self._duration}ms | gain={self._gain_factor:.4f}"
            )

            # Start the reader thread so the buffer begins filling immediately.
            # play() will reuse the existing stream if SR/channels match, so
            # audio data needs to be ready before the callback fires.
            self._start_reader_thread()

            # Kick off background pre-load of the next queued track
            self._start_preload_next()

            return True

        except Exception as exc:
            self.error_occurred.emit(f"Failed to open audio: {exc}")
            logger.error(f"load_track error: {exc}")
            return False

    # =========================================================================
    #  Background pre-loading of the next track
    # =========================================================================

    def _start_preload_next(self):
        """
        Determine the next track in the queue and open its SoundFile in a
        background thread so it is ready before the current track ends.
        """
        # Cancel any in-flight preload
        if self._preload_thread is not None and self._preload_thread.is_alive():
            # We can't cancel it, but we mark the result stale by clearing _next_file.
            with self._preload_lock:
                self._next_file = None

        q = self.queue_manager.queue
        next_index = 1  # index 0 = current, index 1 = next
        if len(q) <= next_index:
            return  # No next track

        next_track = q[next_index]
        next_path = Path(next_track.track_file_path)

        if not next_path.exists():
            return
        if next_path.suffix.lower() not in SUPPORTED_FORMATS:
            return

        def _preload():
            try:
                reader = self.sf.SoundFile(str(next_path), mode="r")
                with self._preload_lock:
                    # Only store result if nobody cleared next_file in the meantime
                    # (which would mean a different track became next)
                    self._next_sf_reader = reader
                    self._next_file = next_path
                    self._next_sample_rate = reader.samplerate
                    self._next_channels = reader.channels
                    self._next_total_frames = len(reader)
                    self._next_gain_factor = 1.0  # Approximate; recalculated at swap
                logger.debug(f"Pre-loaded next track: {next_path.name}")
            except Exception as exc:
                logger.warning(f"Pre-load failed for {next_path.name}: {exc}")

        self._preload_thread = threading.Thread(
            target=_preload, daemon=True, name="TrackPreload"
        )
        self._preload_thread.start()

    # =========================================================================
    #  Normalization
    # =========================================================================

    def enable_normalization(self, enabled: bool):
        self.normalization_enabled = enabled
        # Recalculate gain for current track
        self._gain_factor = self._calculate_gain_factor()
        logger.info(f"Normalization {'on' if enabled else 'off'}")

    def set_normalization_target(self, target_lufs: float):
        self.normalization_target = max(-50.0, min(-5.0, target_lufs))
        self._gain_factor = self._calculate_gain_factor()
        logger.info(f"Normalization target: {self.normalization_target} LUFS")

    # =========================================================================
    #  Audio device
    # =========================================================================

    def set_audio_device(self, device_name: str):
        if device_name == self.current_device:
            return
        self.current_device = device_name
        self.audio_device_changed.emit(device_name)
        self._restart_playback_if_active()

    def get_audio_devices(self) -> list:
        try:
            return [
                {"id": i, "name": d["name"], "default": d.get("default", False)}
                for i, d in enumerate(self.available_devices)
                if d["max_output_channels"] > 0
            ]
        except Exception as exc:
            logger.error(f"get_audio_devices error: {exc}")
            return []

    # =========================================================================
    #  Read-only properties for the UI
    # =========================================================================

    @property
    def position(self) -> int:
        return self._position

    @property
    def duration(self) -> int:
        return self._duration

    @property
    def volume(self) -> int:
        return self.volume_level

    @property
    def state(self) -> str:
        if self.playing and not self.paused:
            return "playing"
        if self.paused:
            return "paused"
        return "stopped"

    # Kept for UI compatibility — streaming mode has no separate audio_data array
    @property
    def audio_data(self):
        return None  # Always None in streaming mode

    # =========================================================================
    #  Cleanup
    # =========================================================================

    def cleanup(self):
        """Call on application exit for a clean shutdown."""
        self._stop_reader_thread()
        self._position_timer.stop()
        self._volume_save_timer.stop()
        self._close_stream()

        with self._reader_lock:
            if self._sf_reader is not None:
                try:
                    self._sf_reader.close()
                except Exception:
                    pass
                self._sf_reader = None

        with self._preload_lock:
            if self._next_sf_reader is not None:
                try:
                    self._next_sf_reader.close()
                except Exception:
                    pass
                self._next_sf_reader = None

        self._save_volume_to_config()
        logger.info("MusicPlayer cleanup complete")

    def __del__(self):
        try:
            self.cleanup()
        except Exception:
            pass

    # =========================================================================
    #  Internal — audio backend
    # =========================================================================

    def _initialize_audio_backend(self) -> bool:
        try:
            import sounddevice as sd
            import soundfile as sf

            self.sd = sd
            self.sf = sf
            self.available_devices = list(sd.query_devices())
            logger.info(
                f"Audio backend ready. {len(self.available_devices)} devices found."
            )
            return True
        except ImportError as exc:
            logger.error(f"Audio backend import failed: {exc}")
            self.error_occurred.emit(
                f"Audio library missing: {exc}. Run: pip install sounddevice soundfile"
            )
            return False
        except Exception as exc:
            logger.error(f"Audio backend init error: {exc}")
            self.error_occurred.emit(f"Audio system error: {exc}")
            return False

    def _get_device_config(self) -> dict:
        try:
            if self.current_device is None:
                info = self.sd.default.device
                if info is not None:
                    self.current_device = info[1]

            if self.exclusive_mode and self.current_device is not None:
                return {
                    "device": self.current_device,
                    "latency": "low",
                }

            return {
                "device": self.current_device,
                "latency": "high",
                "clip_off": True,
            }
        except Exception as exc:
            logger.warning(f"Could not determine device config: {exc}")
            return {"device": None, "latency": "high", "blocksize": BLOCKSIZE}

    def _close_stream(self):
        if self.audio_stream is not None:
            try:
                self.audio_stream.stop()
                self.audio_stream.close()
            except Exception:
                pass
            finally:
                self.audio_stream = None

    def _restart_playback_if_active(self):
        if self.current_file and (self.playing or self.paused):
            saved_pos = self._position
            was_playing = self.playing and not self.paused
            self.stop()
            self.seek(saved_pos)
            if was_playing:
                self.play()

    # =========================================================================
    #  Internal — audio callback (runs on the sounddevice thread)
    # =========================================================================

    def _emit_track_finished_once(self):
        """Emit _track_finished exactly once per stream lifetime (callback-safe).
        Uses a threading.Event so the check-and-set is atomic across threads."""
        if not self._finish_pending.is_set():
            self._finish_pending.set()
            self._track_finished.emit()

    def _audio_callback(
        self, outdata: np.ndarray, frames: int, time, status, generation: int
    ):
        import time as _time

        _t0 = _time.perf_counter()

        if status:
            logger.warning(
                f"Audio callback status: {status} "
                f"(generation={generation}, playing={self.playing}, "
                f"advancing={self._is_advancing}, frames_done={self._current_frame})"
            )

        if generation != self._stream_generation:
            outdata.fill(0)
            return

        if not self.playing or self.paused:
            outdata.fill(0)
            return

        # Pop a pre-decoded chunk from the buffer
        with self._buffer_lock:
            if self._audio_buffer:
                chunk = self._audio_buffer.popleft()
            else:
                # Buffer underrun — reader thread hasn't caught up
                outdata.fill(0)
                if self._total_frames > 0 and self._current_frame >= self._total_frames:
                    self._emit_track_finished_once()
                return

        # Apply gain
        effective_gain = self._gain_factor * (self.volume_level / 100.0)
        chunk = chunk * effective_gain

        # Apply equalizer
        if len(chunk) >= 32:
            chunk = self.equalizer.process_audio(chunk)

        outdata[: len(chunk)] = chunk
        self._frames_played += len(chunk)
        if len(chunk) < frames:
            outdata[len(chunk) :] = 0
            self._emit_track_finished_once()

    # =========================================================================
    #  Internal — playback finished handler (runs on the main thread)
    # =========================================================================

    def _handle_playback_finished(self):
        """Called on the main thread when the current track ends."""
        logger.debug(f"_handle_playback_finished called at {time.time()}")
        if self.repeat_mode == 1:
            self.seek(0)
            self.play()
        else:
            self.play_next()

    # =========================================================================
    #  Internal — position tracking & play count
    # =========================================================================

    def _update_position(self):
        """Fired by the position timer every POSITION_INTERVAL_MS."""
        if not self.playing or self.paused or self._sf_reader is None:
            return
        self._position = int(self._frames_played / self.current_sample_rate * 1000)
        self.position_changed.emit(self._position)

        if (
            self._duration > 0
            and not self._play_count_recorded
            and (self._position / self._duration) >= PLAY_COUNT_THRESHOLD
            and not self._has_reached_threshold
        ):
            self._has_reached_threshold = True
            self._increment_play_count()

    def _increment_play_count(self):
        """Write an incremented play count to DB in background thread."""
        if not self.current_file or self._play_count_recorded:
            return

        # Store current file path locally for thread safety
        current_path = self.current_file

        def _update_db():
            try:
                track = self.controller.get.get_entity_object(
                    "Track", track_file_path=str(current_path)
                )
                if track and getattr(track, "track_id", None):
                    new_count = (getattr(track, "play_count", 0) or 0) + 1
                    self.controller.update.update_entity(
                        "Track",
                        track.track_id,
                        play_count=new_count,
                        last_listened_date=datetime.now(),
                    )
                    # Emit signal back to main thread
                    self.play_count_updated.emit(current_path, new_count)
                    logger.info(f"Play count → {new_count}: {current_path.name}")
            except Exception as exc:
                logger.error(f"Play count update error: {exc}")

        self._play_count_recorded = True  # Mark as recorded immediately
        threading.Thread(target=_update_db, daemon=True, name="PlayCountUpdate").start()

    # =========================================================================
    #  Internal — gain calculation
    # =========================================================================

    def _calculate_gain_factor(self) -> float:
        """
        Returns the multiplier applied to every audio chunk.
        Uses ReplayGain from the DB when available; falls back to 1.0.
        """
        if not self.normalization_enabled or self.current_file is None:
            return 1.0

        try:
            track_gain, track_peak = self._get_track_gain_from_db()

            if track_gain is not None:
                # ReplayGain stores the adjustment needed to reach reference loudness.
                # We then shift that reference to our target (default -14 LUFS).
                # Reference loudness for ReplayGain is -18 LUFS (older standard) or
                # -23 LUFS (EBU R128). We offset from -18 as a safe middle ground.
                REPLAYGAIN_REFERENCE_LUFS = -18.0
                target_offset = self.normalization_target - REPLAYGAIN_REFERENCE_LUFS
                gain_db = track_gain + target_offset
                gain_factor = 10.0 ** (gain_db / 20.0)

                # Peak limiter: only clamp if the boosted signal would clip.
                # This runs AFTER gain is set so quiet tracks still get lifted.
                if track_peak and track_peak > 0:
                    max_output = gain_factor * float(track_peak)
                    if max_output > 0.99:
                        gain_factor = 0.99 / float(track_peak)

                logger.debug(
                    f"Gain factor (ReplayGain): {gain_factor:.4f}  (track_gain={track_gain:.2f} dB, target={self.normalization_target} LUFS)"
                )
                return float(gain_factor)

        except Exception as exc:
            logger.error(f"Gain calculation error: {exc}")

        return 1.0  # Safe default

    def _get_track_gain_from_db(self):
        """Fetch track_gain and track_peak from the DB for the current file."""
        try:
            if self.current_file is None:
                return None, None
            track = self.controller.get.get_entity_object(
                "Track", track_file_path=str(self.current_file)
            )
            if track:
                gain = getattr(track, "track_gain", None)
                peak = getattr(track, "track_peak", None)
                if gain is not None and peak is not None:
                    return float(gain), float(peak)
        except Exception as exc:
            logger.error(f"DB gain lookup error: {exc}")
        return None, None

    # =========================================================================
    #  Internal — volume persistence
    # =========================================================================

    def _save_volume_to_config(self):
        try:
            app_config.set_volume(self.volume_level)
            app_config.save()
        except Exception as exc:
            logger.error(f"Volume save error: {exc}")
