"""
player_util.py — MusicPlayer
Streaming music playback engine.

The class itself is intentionally thin: it owns __init__, Qt signals, and
read-only UI properties, while behavior lives in the mixins below (each is
one former section of this file):

    player_device.py         — audio backend init, output device selection,
                                exclusive/bit-perfect (raw hw:) mode
    player_reader.py          — background decode thread + ring buffer
    player_track_loading.py   — opening files, next-track pre-load
    player_transport.py       — play/pause/stop/seek/next/previous
    player_callback.py        — the real-time PortAudio callback + its
                                deferred diagnostics logging
    player_position.py        — position-timer tick, play-count recording
    player_gain.py             — volume + ReplayGain/normalization

Diagnostics note: the audio callback (player_callback.py) must never log
directly — it runs on PortAudio's real-time thread, and a blocking disk
write there can itself cause the next underrun. It only counts events;
_flush_callback_diagnostics (called from the position timer each tick)
does the actual logging from the main thread.
"""

import collections
import contextlib
from pathlib import Path
import threading

from PySide6.QtCore import QObject, QTimer, Signal

from src.core.config_setup import app_config
from src.core.logger_config import logger
from src.equalizer.equalizer_utility import EqualizerUtility
from src.player.player_callback import PlayerCallbackMixin
from src.player.player_device import PlayerDeviceMixin
from src.player.player_gain import PlayerGainMixin
from src.player.player_position import POSITION_INTERVAL_MS, PlayerPositionMixin
from src.player.player_reader import PlayerReaderMixin
from src.player.player_track_loading import PlayerTrackLoadingMixin
from src.player.player_transport import PlayerTransportMixin
from src.player.queue_utility import QueueManager


class MusicPlayer(
    QObject,
    PlayerDeviceMixin,
    PlayerReaderMixin,
    PlayerTrackLoadingMixin,
    PlayerTransportMixin,
    PlayerCallbackMixin,
    PlayerPositionMixin,
    PlayerGainMixin,
):
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

    # ── Init ──────────────────────────────────────────────────────────────────

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.equalizer = EqualizerUtility(self)
        self.queue_manager = QueueManager(config=app_config)
        self.queue_manager.load_queue_from_config(self.controller.SessionFactory)

        # ── Audio backend ──────────────────────────────────────────────────────
        self.sd = None
        self.sf = None
        self.audio_stream: object | None = None

        # ── Current track state ───────────────────────────────────────────────
        # We keep a SoundFile reader open instead of the whole array.
        self.current_file: Path | None = None
        self._resolved_file_path: Path | None = None  # on-disk path for current_file
        self._sf_reader: object | None = None  # soundfile.SoundFile
        self.current_sample_rate: int = 44100
        self.current_channels: int = 2
        self.current_bit_depth: int = 32
        self.current_format: str | None = None

        self._total_frames: int = 0  # total frames in the file
        self._current_frame: int = 0  # how many frames we have read so far
        self._frames_played: int = 0  # how many frames the audio callback has output

        # Lock protecting _sf_reader and _current_frame from concurrent access
        # between the audio callback thread and the main thread (seek).
        self._reader_lock = threading.Lock()
        self._audio_buffer: collections.deque = collections.deque()
        self._buffer_lock = threading.Lock()
        # Partially-consumed decode chunk: the reader pushes fixed 16384-frame
        # chunks, but the PortAudio callback block (STREAM_BLOCKSIZE=0) is
        # smaller and variably sized, so one popped chunk feeds many callbacks.
        # gain + EQ are applied once, when the chunk is popped. Reset (under
        # _buffer_lock) everywhere _audio_buffer is cleared.
        self._callback_residual = None  # np.ndarray | None, already gain/EQ'd
        self._callback_residual_pos: int = 0
        # Set once the reader's short final (EOF) chunk has been popped, so the
        # callback still emits track-finished after that chunk drains even for
        # files whose header frame count is missing/unreliable.
        self._callback_final_chunk_seen: bool = False
        self._reader_thread: threading.Thread | None = None
        self._reader_stop = threading.Event()

        # ── Pre-load: next track ──────────────────────────────────────────────
        # We open the *next* track's SoundFile in a background thread so it is
        # ready before the current track ends, giving zero-gap transitions.
        self._next_sf_reader: object | None = None
        self._next_file: Path | None = None
        self._next_sample_rate: int = 0
        self._next_channels: int = 0
        self._next_total_frames: int = 0
        self._preload_lock = threading.Lock()
        self._preload_thread: threading.Thread | None = None
        self._preload_generation: int = 0

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
        self._callback_native_tid: int | None = None  # set by _stamped_callback on first fire
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

        # ── Deferred callback diagnostics ───────────────────────────────────────
        # The audio callback runs on PortAudio's realtime thread, so it must never
        # log directly (a blocking disk write there can itself cause the next
        # underrun, cascading into the very hitches it's reporting). It just
        # counts occurrences here; _update_position flushes them to the log from
        # the main thread instead.
        self._pending_status_count: int = 0
        self._last_status_value = None
        self._pending_error_count: int = 0
        self._last_error_message: str | None = None
        # App-level buffer underrun (reader thread fell behind) — distinct from
        # PortAudio's own `status` flag above: this one never reaches PortAudio
        # late, so PortAudio never flags it, but it's heard as the same hitch.
        self._pending_buffer_underrun_count: int = 0

        # ── Normalization ─────────────────────────────────────────────────────
        self.normalization_enabled: bool = False
        self.normalization_target: float = -14.0  # LUFS (music streaming standard)

        # ── Audio device ──────────────────────────────────────────────────────
        self.exclusive_mode: bool = app_config.get_exclusive_mode()
        self.current_device = None
        self.available_devices: list = []
        # Name of the PipeWire/PulseAudio sink suspended to grab raw hw:
        # access for the current exclusive-mode stream, if any — resumed
        # when the stream closes so system sounds work again.
        self._suspended_sink_name: str | None = None

        # ── Position timer ────────────────────────────────────────────────────
        self._position_timer = QTimer(self)
        self._position_timer.setInterval(POSITION_INTERVAL_MS)
        self._position_timer.timeout.connect(self._update_position)

        # Wire cross-thread finish signal with QueuedConnection so it always
        # runs on the main thread regardless of which thread emits it.
        from PySide6.QtCore import Qt as _Qt

        self._track_finished.connect(
            self._handle_playback_finished, type=_Qt.ConnectionType.QueuedConnection
        )

        # ── Boot ──────────────────────────────────────────────────────────────
        self._audio_initialized = self._initialize_audio_backend()
        if not self._audio_initialized:
            logger.error("MusicPlayer: audio backend failed to initialize")
        else:
            self._load_saved_output_device()

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
                with contextlib.suppress(OSError):
                    self._sf_reader.close()
                self._sf_reader = None

        with self._preload_lock:
            if self._next_sf_reader is not None:
                with contextlib.suppress(OSError):
                    self._next_sf_reader.close()
                self._next_sf_reader = None

        self._save_volume_to_config()
        logger.info("MusicPlayer cleanup complete")
