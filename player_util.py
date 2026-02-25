"""
player_util.py — MusicPlayer
Audiophile-grade music playback engine.

Design principles:
  - One and only one code path triggers track advancement (_handle_playback_finished)
  - The audio callback NEVER touches Qt objects directly — it only emits a
    queued Signal (_track_finished) which Qt safely delivers to the main thread.
    QTimer.singleShot is NOT used from the callback because sounddevice's C-level
    thread is not integrated with Qt's event loop and the calls get dropped.
  - Gain/normalization is calculated ONCE at load time, never per-callback
  - Crossfade pre-loading is triggered automatically after every load_track()
  - All public methods are safe to call from the UI thread at any time
"""

from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np
from PySide6.QtCore import QObject, QTimer, Signal

from asset_paths import config
from config_setup import app_config
from equalizer_utility import EqualizerUtility
from logger_config import logger
from queue_utility import QueueManager

# ─────────────────────────────────────────────────────────────────────────────
#  Constants
# ─────────────────────────────────────────────────────────────────────────────

BLOCKSIZE = 4096  # Audio callback buffer size (frames). Larger = more stable.
POSITION_INTERVAL_MS = (
    50  # UI position update interval. 50 ms = 20 fps, smooth scrubbing.
)
ADVANCE_LOCK_MS = 500  # How long to hold the advance lock after a track change.
PLAY_COUNT_THRESHOLD = 0.90  # Fraction of track that must play to count as a listen.
CROSSFADE_MAX_MS = 10_000  # Maximum allowed crossfade duration.
RESTART_THRESHOLD_MS = 3_000  # "Previous" restarts current track if past this position.


# ─────────────────────────────────────────────────────────────────────────────
#  MusicPlayer
# ─────────────────────────────────────────────────────────────────────────────


class MusicPlayer(QObject):
    """
    Audiophile-grade music player.

    Public signals (connect these in the UI):
        position_changed(int)       — current position in milliseconds
        duration_changed(int)       — total duration in milliseconds
        state_changed(str)          — "playing" | "paused" | "stopped"
        volume_changed(int)         — volume 0-100
        error_occurred(str)         — human-readable error message
        track_changed(Path)         — Path of the newly loaded track
        play_count_updated(Path, int) — track path + new play count after threshold
        audio_device_changed(str)   — name of the newly selected audio device
        playback_mode_changed(str)  — "exclusive" | "shared"

    Public methods called by the UI:
        play(), pause(), stop()
        play_next(), play_previous()
        seek(position_ms: int)
        set_volume(value: int)
        set_repeat_mode(mode: int)   — 0=none, 1=one, 2=all
        toggle_play_pause()
        increase_volume(), decrease_volume()
        seek_forward(), seek_backward()
        load_track(file_path: Path) -> bool
        enable_crossfade(enabled: bool)
        set_crossfade_duration(duration_ms: int)
        enable_normalization(enabled: bool)
        set_normalization_target(target_lufs: float)
        set_exclusive_mode(enabled: bool)
        set_audio_device(device_name: str)
        get_audio_devices() -> list
        cleanup()                    — call on application exit
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

    # Private signal — emitted by the audio callback (C-level sounddevice thread)
    # to safely trigger track advancement on the main Qt thread via QueuedConnection.
    # QTimer.singleShot is NOT used from the callback because sounddevice's thread
    # is not integrated with Qt's event loop and the calls get silently dropped.
    # This is the ONLY mechanism the callback uses to talk to the main thread.
    _track_finished = Signal()

    # ── Init ──────────────────────────────────────────────────────────────────

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.equalizer = EqualizerUtility(self)
        self.queue_manager = QueueManager()
        self.queue_manager.load_queue_from_config(config)

        # ── Audio backend (lazy-imported so the app can start without them) ──
        self.sd = None
        self.sf = None
        self.audio_stream: Optional[object] = None

        # ── Track data ────────────────────────────────────────────────────────
        self.current_file: Optional[Path] = None
        self.audio_data: Optional[np.ndarray] = None
        self.current_frame: int = 0
        self.current_sample_rate: int = 44100
        self.current_channels: int = 2
        self.current_bit_depth: int = 32
        self.current_format: Optional[str] = None

        # Pre-calculated gain factor (set once per load_track, used in callback)
        self._gain_factor: float = 1.0

        # ── Playback state ────────────────────────────────────────────────────
        self.playing: bool = False
        self.paused: bool = False
        self._position: int = 0
        self._duration: int = 0
        self.repeat_mode: int = 0  # 0=none, 1=one, 2=all

        # Advance lock — prevents rapid double-triggers of play_next/play_previous
        self._is_advancing: bool = False

        # Generation counter — incremented every time a new stream is opened.
        # The audio callback captures the current generation at stream-open time
        # and only emits _track_finished if its generation still matches.
        # This kills any queued signal emissions from a previous (now-closed) stream
        # that arrive after the next track has already started playing.
        self._stream_generation: int = 0

        # Finish-pending flag — set to True the moment the callback emits
        # _track_finished, reset to False when a new stream opens.
        # Prevents double-emission when two consecutive callbacks both see
        # frames_remaining <= 0 (e.g. after manually seeking near the end).
        self._finish_pending: bool = False

        # ── Volume ────────────────────────────────────────────────────────────
        self.volume_level: int = app_config.get_volume()

        # Debounce writing volume to config so we don't hammer disk on slider drag
        self._volume_save_timer = QTimer(self)
        self._volume_save_timer.setSingleShot(True)
        self._volume_save_timer.timeout.connect(self._save_volume_to_config)

        # ── Play count tracking ───────────────────────────────────────────────
        self._has_reached_threshold: bool = False
        self._play_count_recorded: bool = False

        # ── Normalization ─────────────────────────────────────────────────────
        self.normalization_enabled: bool = False
        self.normalization_target: float = -23.0  # LUFS

        # ── Crossfade ─────────────────────────────────────────────────────────
        self.crossfade_enabled: bool = False
        self.crossfade_duration: int = 3000  # ms
        self._crossfade_frames: int = 0
        self._crossfade_active: bool = False
        self._crossfade_start_frame: int = 0
        self._next_audio_data: Optional[np.ndarray] = None
        self._next_file: Optional[Path] = None

        # ── Audio device ──────────────────────────────────────────────────────
        self.exclusive_mode: bool = False
        self.current_device = None
        self.available_devices = []

        # ── Timers ────────────────────────────────────────────────────────────
        self._position_timer = QTimer(self)
        self._position_timer.setInterval(POSITION_INTERVAL_MS)
        self._position_timer.timeout.connect(self._update_position)

        # Wire the cross-thread finish signal. QueuedConnection guarantees the
        # slot runs on the main thread even though the signal is emitted from
        # the sounddevice audio callback thread.
        from PySide6.QtCore import Qt as _Qt

        self._track_finished.connect(
            self._handle_playback_finished, type=_Qt.ConnectionType.QueuedConnection
        )

        # ── Boot ──────────────────────────────────────────────────────────────
        self._audio_initialized = self._initialize_audio_backend()
        if not self._audio_initialized:
            logger.error("MusicPlayer: audio backend failed to initialize")

    # =========================================================================
    #  Public playback controls
    # =========================================================================

    def play(self):
        """Start or resume playback."""
        if self.sd is None:
            if not self._initialize_audio_backend():
                self.error_occurred.emit("Audio backend not available.")
                return

        # Auto-load from queue if nothing is loaded
        if self.current_file is None or self.audio_data is None:
            track = self.queue_manager.get_current_track()
            if track:
                if not self.load_track(Path(track.track_file_path)):
                    return
            else:
                self.error_occurred.emit("Queue is empty.")
                return

        # Reset play count gate for this new play session
        self._has_reached_threshold = False
        self._play_count_recorded = False

        try:
            # ── Resume from pause (no stream rebuild needed) ──────────────────
            if self.paused and self.audio_stream is not None:
                self.paused = False
                self.playing = True
                self.state_changed.emit("playing")
                self._position_timer.start()
                logger.info("Playback resumed")
                return

            # ── Fresh start: close any leftover stream, open a new one ────────
            self._close_stream()

            # Stamp this stream with a new generation so any queued _track_finished
            # signals from the previous stream are ignored when they arrive.
            self._stream_generation += 1
            my_generation = self._stream_generation
            self._finish_pending = False  # Fresh stream — no finish queued yet

            device_config = self._get_device_config()

            # Wrap the callback in a closure that captures my_generation.
            # When the callback fires, it checks whether its generation still matches
            # the current one — stale callbacks from closed streams will not match.
            def _stamped_callback(outdata, frames, time, status, _gen=my_generation):
                self._audio_callback(outdata, frames, time, status, _gen)

            self.audio_stream = self.sd.OutputStream(
                samplerate=self.current_sample_rate,
                channels=self.current_channels,
                dtype="float32",
                device=device_config["device"],
                latency=device_config.get("latency", "high"),
                blocksize=device_config.get("blocksize", BLOCKSIZE),
                callback=_stamped_callback,
            )
            self.audio_stream.start()

            self.playing = True
            self.paused = False
            self.state_changed.emit("playing")
            self._position_timer.start()
            logger.info(f"Playback started: {self.current_file.name}")

        except Exception as exc:
            msg = f"Playback error: {exc}"
            logger.error(msg)
            self.error_occurred.emit(msg)
            self.playing = False

    def pause(self):
        """Pause playback. Stream stays open; callback outputs silence."""
        if self.playing and not self.paused:
            self.paused = True
            self.state_changed.emit("paused")
            self._position_timer.stop()
            logger.debug("Playback paused")

    def stop(self):
        """Stop playback and reset position. Does NOT clear the loaded track."""
        self.playing = False
        self.paused = False
        self._close_stream()
        self._position_timer.stop()

        # Reset crossfade state
        self._crossfade_active = False
        self._next_audio_data = None
        self._next_file = None

        # Reset play count gate
        self._has_reached_threshold = False
        self._play_count_recorded = False

        self.state_changed.emit("stopped")
        logger.debug("Playback stopped")

    def toggle_play_pause(self):
        """Toggle between playing and paused, or start fresh if stopped."""
        if self.paused:
            self.play()
        elif self.playing:
            self.pause()
        else:
            self.play()

    def play_next(self):
        """Advance queue and start the next track."""
        if self._is_advancing:
            logger.debug("play_next: advance already in progress, skipping.")
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
                    # load_track already emitted an error
                    self._is_advancing = False
                    return
            else:
                logger.info("Queue exhausted — stopping.")
                self.stop()

        except Exception as exc:
            logger.error(f"play_next error: {exc}")
        finally:
            QTimer.singleShot(ADVANCE_LOCK_MS, self._release_advance_lock)

    def play_previous(self):
        """
        Go to the previous track, or restart the current one.
        If playback is past RESTART_THRESHOLD_MS, restart current track instead.
        """
        if self._is_advancing:
            logger.debug("play_previous: advance already in progress, skipping.")
            return

        self._is_advancing = True
        try:
            # If we're more than a few seconds in, just restart current track
            if self._position > RESTART_THRESHOLD_MS and self.current_file is not None:
                logger.info("play_previous: restarting current track")
                self.seek(0)
                if not self.playing:
                    self.play()
                return

            # Otherwise step back in the queue
            prev_track = self.queue_manager.previous_track_in_queue()
            if prev_track:
                if self.load_track(Path(prev_track.track_file_path)):
                    self.play()
                else:
                    self._is_advancing = False
                    return
            else:
                # Nothing before — restart current
                self.seek(0)
                if not self.playing:
                    self.play()

        except Exception as exc:
            logger.error(f"play_previous error: {exc}")
        finally:
            QTimer.singleShot(ADVANCE_LOCK_MS, self._release_advance_lock)

    def seek(self, position_ms: int):
        """Seek to position in milliseconds."""
        if self.audio_data is None or self.current_sample_rate == 0:
            return
        try:
            target_frame = int(position_ms / 1000.0 * self.current_sample_rate)
            target_frame = max(0, min(target_frame, len(self.audio_data) - 1))
            self.current_frame = target_frame
            self._position = position_ms

            # If seeking back before threshold, allow play count to re-trigger
            if (
                self._duration > 0
                and (position_ms / self._duration) < PLAY_COUNT_THRESHOLD
            ):
                self._has_reached_threshold = False
                self._play_count_recorded = False

            logger.debug(f"Seek to {position_ms}ms (frame {target_frame})")
        except Exception as exc:
            logger.error(f"Seek error: {exc}")

    # ── Volume ─────────────────────────────────────────────────────────────────

    def set_volume(self, value: int):
        """Set volume 0–100. Debounces config writes."""
        new_val = max(0, min(100, value))
        if new_val != self.volume_level:
            self.volume_level = new_val
            self.volume_changed.emit(self.volume_level)
            self._volume_save_timer.start(500)

    def increase_volume(self):
        """Increase volume by 5 points."""
        self.set_volume(self.volume_level + 5)

    def decrease_volume(self):
        """Decrease volume by 5 points."""
        self.set_volume(self.volume_level - 5)

    # ── Convenience seek wrappers ──────────────────────────────────────────────

    def seek_forward(self):
        """Skip forward 10 seconds."""
        if self._duration > 0:
            self.seek(min(self._duration, self._position + 10_000))

    def seek_backward(self):
        """Skip backward 10 seconds."""
        if self._duration > 0:
            self.seek(max(0, self._position - 10_000))

    # ── Repeat ────────────────────────────────────────────────────────────────

    def set_repeat_mode(self, mode: int):
        """Set repeat mode: 0=none, 1=one, 2=all."""
        self.repeat_mode = mode
        logger.debug(f"Repeat mode: {mode}")

    # =========================================================================
    #  Track loading
    # =========================================================================

    def load_track(self, file_path: Path) -> bool:
        """
        Load an audio file and prepare it for playback.
        Stops any current playback, decodes the file into memory as float32,
        pre-calculates the gain factor, and pre-loads the crossfade buffer
        for the next queued track if crossfade is enabled.
        Returns True on success, False on failure.
        """
        if not file_path.exists():
            self.error_occurred.emit(f"File not found: {file_path}")
            return False

        supported = {".wav", ".flac", ".mp3", ".aiff", ".aif", ".ogg"}
        if file_path.suffix.lower() not in supported:
            self.error_occurred.emit(f"Unsupported format: {file_path.suffix}")
            return False

        # Stop current playback cleanly before replacing audio data
        self.stop()
        self.current_frame = 0
        self._position = 0

        logger.info(f"Loading: {file_path}")

        try:
            audio_data, sample_rate = self.sf.read(
                str(file_path),
                always_2d=True,  # always shape (frames, channels)
                dtype="float32",  # consistent internal format
            )
        except Exception as exc:
            self.error_occurred.emit(f"Failed to read audio: {exc}")
            return False

        if audio_data is None or len(audio_data) == 0:
            self.error_occurred.emit("Audio file is empty or corrupted.")
            return False

        self.audio_data = audio_data
        self.current_sample_rate = sample_rate
        self.current_channels = audio_data.shape[1] if audio_data.ndim > 1 else 1
        self.current_bit_depth = 32
        self.current_format = file_path.suffix.lower()
        self.current_file = file_path
        self._duration = int(len(audio_data) / sample_rate * 1000)

        # Tell the equalizer about any sample rate change
        self.equalizer.set_sample_rate(sample_rate)

        # Pre-calculate gain factor once so the callback never needs to touch the DB
        self._gain_factor = self._calculate_gain_factor()

        # Emit metadata signals
        self.track_changed.emit(file_path)
        self.duration_changed.emit(self._duration)

        logger.info(
            f"Loaded: {file_path.name} | "
            f"{sample_rate}Hz | {self.current_channels}ch | "
            f"{self._duration}ms | gain={self._gain_factor:.4f}"
        )

        # Pre-load crossfade buffer for the next track (non-blocking, best-effort)
        if self.crossfade_enabled:
            self._prepare_crossfade()

        return True

    # =========================================================================
    #  Audio device & mode configuration
    # =========================================================================

    def set_exclusive_mode(self, enabled: bool):
        """Enable or disable exclusive audio device mode."""
        if self.exclusive_mode == enabled:
            return
        self.exclusive_mode = enabled
        self.playback_mode_changed.emit("exclusive" if enabled else "shared")
        self._restart_playback_if_active()

    def set_audio_device(self, device_name: str):
        """Switch audio output device."""
        if device_name == self.current_device:
            return
        self.current_device = device_name
        self.audio_device_changed.emit(device_name)
        self._restart_playback_if_active()

    def get_audio_devices(self) -> list:
        """Return list of available output audio devices."""
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
    #  Normalization
    # =========================================================================

    def enable_normalization(self, enabled: bool):
        self.normalization_enabled = enabled
        # Recalculate gain for the currently loaded track
        if self.audio_data is not None:
            self._gain_factor = self._calculate_gain_factor()
        logger.info(f"Normalization {'on' if enabled else 'off'}")

    def set_normalization_target(self, target_lufs: float):
        self.normalization_target = max(-50.0, min(-5.0, target_lufs))
        if self.audio_data is not None:
            self._gain_factor = self._calculate_gain_factor()
        logger.info(f"Normalization target: {self.normalization_target} LUFS")

    # =========================================================================
    #  Crossfade
    # =========================================================================

    def enable_crossfade(self, enabled: bool):
        self.crossfade_enabled = enabled
        if not enabled:
            self._next_audio_data = None
            self._next_file = None
        elif self.current_file is not None:
            self._prepare_crossfade()
        logger.info(f"Crossfade {'on' if enabled else 'off'}")

    def set_crossfade_duration(self, duration_ms: int):
        self.crossfade_duration = max(0, min(CROSSFADE_MAX_MS, duration_ms))
        logger.info(f"Crossfade duration: {self.crossfade_duration}ms")

    # =========================================================================
    #  Properties (read-only from the UI)
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

    # =========================================================================
    #  Cleanup
    # =========================================================================

    def cleanup(self):
        """
        Call this on application exit for a clean shutdown.
        Stops timers, closes the audio stream, saves volume.
        """
        self._position_timer.stop()
        self._volume_save_timer.stop()
        self._close_stream()
        self._save_volume_to_config()
        logger.info("MusicPlayer cleanup complete")

    def __del__(self):
        try:
            self.cleanup()
        except Exception:
            pass  # Never raise from __del__

    # =========================================================================
    #  Internal — audio backend
    # =========================================================================

    def _initialize_audio_backend(self) -> bool:
        """Import sounddevice and soundfile. Returns True on success."""
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
        """Return the optimal sounddevice stream parameters."""
        try:
            # Resolve default device index if none explicitly chosen
            if self.current_device is None:
                info = self.sd.default.device
                if info is not None:
                    self.current_device = info[1]  # output index

            if self.exclusive_mode and self.current_device is not None:
                return {
                    "device": self.current_device,
                    "latency": "low",
                    "blocksize": BLOCKSIZE,
                }

            return {
                "device": self.current_device,
                "latency": "high",
                "blocksize": BLOCKSIZE,
                "clip_off": True,
            }
        except Exception as exc:
            logger.warning(f"Could not determine device config: {exc}")
            return {"device": None, "latency": "high", "blocksize": BLOCKSIZE}

    def _close_stream(self):
        """Stop and close the audio stream safely."""
        if self.audio_stream is not None:
            try:
                self.audio_stream.stop()
                self.audio_stream.close()
            except Exception:
                pass
            finally:
                self.audio_stream = None

    def _restart_playback_if_active(self):
        """Restart stream after a device/mode change, preserving position."""
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
        """
        Emit _track_finished exactly once per stream lifetime.
        Called from the audio callback thread. Sets _finish_pending atomically
        so that even if two consecutive callbacks both see frames_remaining <= 0
        (which happens after a manual seek to near the end), only one signal
        is ever queued for delivery on the main thread.
        """
        if not self._finish_pending:
            self._finish_pending = True
            self._track_finished.emit()

    def _audio_callback(
        self, outdata: np.ndarray, frames: int, time, status, generation: int
    ):
        """
        Called by sounddevice on a background thread to fill the next audio buffer.

        Rules:
          - NEVER call Qt signals directly — only emit _track_finished
          - NEVER access the database here
          - Check `generation` matches _stream_generation before emitting, to
            discard any callbacks that fire after the stream was replaced
          - Keep it fast — this is the hot path
        """
        if status:
            logger.warning(f"Audio callback status: {status}")

        # Discard callbacks from a previous (now-replaced) stream.
        if generation != self._stream_generation:
            outdata.fill(0)
            return

        # Silence when not actively playing
        if not self.playing or self.paused or self.audio_data is None:
            outdata.fill(0)
            return

        try:
            frames_remaining = len(self.audio_data) - self.current_frame

            # Guard: already past end (e.g. repeated callback after last buffer)
            if frames_remaining <= 0:
                outdata.fill(0)
                self._emit_track_finished_once()
                return

            # ── Crossfade trigger ─────────────────────────────────────────────
            if (
                self.crossfade_enabled
                and self._next_audio_data is not None
                and frames_remaining <= self._crossfade_frames
                and not self._crossfade_active
            ):
                self._crossfade_active = True
                self._crossfade_start_frame = self.current_frame

            # ── Dispatch to normal or crossfade handler ───────────────────────
            if self._crossfade_active and self._next_audio_data is not None:
                self._callback_crossfade(outdata, frames)
            else:
                self._callback_normal(outdata, frames)

        except Exception as exc:
            logger.error(f"Audio callback error: {exc}")
            outdata.fill(0)

    def _callback_normal(self, outdata: np.ndarray, frames: int):
        """Fill buffer from the current track with volume + EQ applied."""
        available = len(self.audio_data) - self.current_frame
        to_write = min(frames, available)

        if to_write > 0:
            chunk = self.audio_data[
                self.current_frame : self.current_frame + to_write
            ].copy()

            # Apply combined gain (normalization × volume) — calculated at load time
            effective_gain = self._gain_factor * (self.volume_level / 100.0)
            chunk *= effective_gain

            # Apply equalizer (stateful SOS filter, handles its own bypass)
            if to_write >= 32:
                chunk = self.equalizer.process_audio(chunk)

            outdata[:to_write] = chunk
            if to_write < frames:
                outdata[to_write:] = 0  # Zero-pad tail of last buffer

            self.current_frame += to_write

            # Trigger finish as soon as we write the last frame.
            # Use the queued Signal — QTimer.singleShot is unreliable from this thread.
            if self.current_frame >= len(self.audio_data):
                self._emit_track_finished_once()
        else:
            outdata.fill(0)
            self._emit_track_finished_once()

    def _callback_crossfade(self, outdata: np.ndarray, frames: int):
        """Fill buffer by blending the end of the current track into the next."""
        # Progress through crossfade window (0.0 → 1.0)
        frames_into_fade = self.current_frame - self._crossfade_start_frame
        progress = min(1.0, frames_into_fade / max(1, self._crossfade_frames))

        # Current track chunk
        cur_available = len(self.audio_data) - self.current_frame
        cur_to_read = min(frames, cur_available)

        if cur_to_read > 0:
            cur_chunk = self.audio_data[
                self.current_frame : self.current_frame + cur_to_read
            ].copy()
        else:
            cur_chunk = np.zeros((frames, self.current_channels), dtype=np.float32)

        # Next track chunk
        nxt_to_read = min(frames, len(self._next_audio_data))
        nxt_chunk = self._next_audio_data[:nxt_to_read].copy()

        # Ensure both chunks have the same length for blending
        blend_frames = min(len(cur_chunk), len(nxt_chunk))
        if blend_frames > 0:
            cur_chunk = cur_chunk[:blend_frames]
            nxt_chunk = nxt_chunk[:blend_frames]

            # Equal-power crossfade curves
            fade_out = np.cos(progress * np.pi / 2)
            fade_in = np.sin(progress * np.pi / 2)
            blended = cur_chunk * fade_out + nxt_chunk * fade_in

            effective_gain = self._gain_factor * (self.volume_level / 100.0)
            blended *= effective_gain

            outdata[:blend_frames] = blended
            if blend_frames < frames:
                outdata[blend_frames:] = 0
        else:
            outdata.fill(0)

        self.current_frame += cur_to_read

        # Crossfade is done — switch to next track
        if progress >= 1.0 or cur_to_read == 0:
            self._emit_track_finished_once()

    # =========================================================================
    #  Internal — track advancement (always called on the main thread)
    # =========================================================================

    def _handle_playback_finished(self):
        """
        Called when a track ends naturally. Always runs on the main thread
        because _track_finished uses QueuedConnection.
        Routes to crossfade completion or normal track advancement.
        """
        # If a crossfade was active, hand off to the crossfade finisher
        if self._crossfade_active and self._next_audio_data is not None:
            self._finish_crossfade()
            return

        logger.debug("Track finished — handling normal advancement.")

        # Close the old stream immediately to prevent any further callbacks
        self._close_stream()
        self.playing = False

        # Record play count if threshold was met this session
        if (
            self.current_file
            and self._has_reached_threshold
            and not self._play_count_recorded
        ):
            self._increment_play_count()

        self._has_reached_threshold = False
        self._play_count_recorded = False

        if self.repeat_mode == 1:
            logger.debug("Repeat One: restarting.")
            self.seek(0)
            self.play()
        else:
            # repeat_mode 0 (none) and 2 (all) both advance — QueueManager handles
            # wrapping for repeat-all when the queue is exhausted.
            self.play_next()

    def _finish_crossfade(self):
        """
        Switch internal state to the pre-loaded next track after crossfade.
        Called on the main thread.
        """
        if self._next_audio_data is None:
            # Crossfade data was lost — fall back to normal advancement
            self._handle_playback_finished()
            return

        # Swap in the pre-loaded track data
        self.audio_data = self._next_audio_data
        self.current_frame = 0
        self._position = 0
        self._crossfade_active = False
        self._next_audio_data = None

        # Advance queue and update current file reference
        self.queue_manager.advance_queue()
        track = self.queue_manager.get_current_track()
        if track:
            self.current_file = Path(track.track_file_path)
            self._duration = int(len(self.audio_data) / self.current_sample_rate * 1000)
            self._gain_factor = self._calculate_gain_factor()
            self.track_changed.emit(self.current_file)
            self.duration_changed.emit(self._duration)
            logger.info(f"Crossfade complete, now playing: {self.current_file.name}")
            # Pre-load the next crossfade
            self._prepare_crossfade()
        else:
            # Queue ran out during the crossfade
            self.stop()

    def _release_advance_lock(self):
        self._is_advancing = False

    # =========================================================================
    #  Internal — gain calculation (run once per track load, NOT per callback)
    # =========================================================================

    def _calculate_gain_factor(self) -> float:
        """
        Calculate the single multiplier applied to every audio chunk.
        Uses ReplayGain metadata from the database when available, falls back
        to a simple RMS-based loudness estimate. Returns 1.0 if normalization
        is disabled.
        """
        if not self.normalization_enabled or self.audio_data is None:
            return 1.0

        try:
            # Try to use stored ReplayGain / track_gain metadata
            track_gain, track_peak = self._get_track_gain_from_db()

            if track_gain is not None:
                gain_factor = 10.0 ** ((self.normalization_target + track_gain) / 20.0)
                # Peak-protection: don't clip
                if track_peak and track_peak > 0:
                    headroom = 0.99
                    max_output = abs(gain_factor) * float(track_peak)
                    if max_output > headroom:
                        gain_factor *= headroom / max_output
                logger.debug(f"Gain factor (ReplayGain): {gain_factor:.4f}")
                return float(gain_factor)

            # Fallback: RMS-based estimate
            rms = float(np.sqrt(np.mean(self.audio_data**2)))
            if rms <= 0:
                return 1.0
            current_lufs_approx = 20.0 * np.log10(rms)
            diff = self.normalization_target - current_lufs_approx
            gain_factor = 10.0 ** (diff / 20.0)
            # Soft-clip ceiling at ×4 to prevent extreme boosts on very quiet tracks
            gain_factor = min(gain_factor, 4.0)
            logger.debug(f"Gain factor (RMS fallback): {gain_factor:.4f}")
            return float(gain_factor)

        except Exception as exc:
            logger.error(f"Gain calculation error: {exc}")
            return 1.0

    def _get_track_gain_from_db(self):
        """Fetch track_gain and track_peak from the database for the current file."""
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
    #  Internal — crossfade pre-loading
    # =========================================================================

    def _prepare_crossfade(self):
        """
        Pre-load the next queued track into memory ready for crossfade blending.
        Called after every load_track() when crossfade is enabled.
        Derives the next track from the queue's sliding window:
          - If history_exists: queue is [prev, current, next, ...], next is index 2
          - If not history_exists: queue is [current, next, ...], next is index 1
        """
        self._next_audio_data = None
        self._next_file = None

        if not self.crossfade_enabled or self.crossfade_duration == 0:
            return

        # Derive next track from the queue window without a dedicated method
        q = self.queue_manager.queue
        next_index = 2 if self.queue_manager.history_exists else 1
        if len(q) <= next_index:
            return  # No next track queued

        next_track = q[next_index]
        next_path = Path(next_track.track_file_path)

        if not next_path.exists():
            logger.warning(f"Crossfade: next track not found: {next_path}")
            return

        try:
            next_audio, next_rate = self.sf.read(
                str(next_path),
                always_2d=True,
                dtype="float32",  # Must match current track format
            )

            if next_rate != self.current_sample_rate:
                logger.warning(
                    f"Crossfade skipped: sample rate mismatch "
                    f"({next_rate} vs {self.current_sample_rate})"
                )
                return

            self._next_audio_data = next_audio
            self._next_file = next_path
            self._crossfade_frames = int(
                self.crossfade_duration / 1000.0 * self.current_sample_rate
            )
            logger.debug(
                f"Crossfade ready: {next_path.name} ({self._crossfade_frames} frames)"
            )

        except Exception as exc:
            logger.error(f"Crossfade preparation error: {exc}")
            self._next_audio_data = None
            self._next_file = None

    # =========================================================================
    #  Internal — position tracking & play count
    # =========================================================================

    def _update_position(self):
        """
        Fired by the position timer every POSITION_INTERVAL_MS.
        Updates _position and checks the play count threshold.
        """
        if not self.playing or self.paused or self.audio_data is None:
            return

        self._position = int(self.current_frame / self.current_sample_rate * 1000)
        self.position_changed.emit(self._position)

        # Play count threshold check
        if (
            self._duration > 0
            and not self._play_count_recorded
            and (self._position / self._duration) >= PLAY_COUNT_THRESHOLD
            and not self._has_reached_threshold
        ):
            self._has_reached_threshold = True
            self._increment_play_count()

    def _increment_play_count(self):
        """Write an incremented play count and updated last_listened_date to the DB."""
        try:
            if not self.current_file or self._play_count_recorded:
                return
            track = self.controller.get.get_entity_object(
                "Track", track_file_path=str(self.current_file)
            )
            if track and getattr(track, "track_id", None):
                new_count = (getattr(track, "play_count", 0) or 0) + 1
                self.controller.update.update_entity(
                    "Track",
                    track.track_id,
                    play_count=new_count,
                    last_listened_date=datetime.now(),
                )
                self._play_count_recorded = True
                self.play_count_updated.emit(self.current_file, new_count)
                logger.info(f"Play count → {new_count}: {self.current_file.name}")
        except Exception as exc:
            logger.error(f"Play count update error: {exc}")

    # =========================================================================
    #  Internal — volume persistence
    # =========================================================================

    def _save_volume_to_config(self):
        try:
            app_config.set_volume(self.volume_level)
            app_config.save()
        except Exception as exc:
            logger.error(f"Volume save error: {exc}")
