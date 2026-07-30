"""
player_track_loading.py — opening audio files and pre-loading the next
queued track for MusicPlayer.

Expects the host class to provide: self.sf (soundfile module), self._sf_reader,
self._reader_lock, self._preload_lock, self._next_* preload state,
self.queue_manager, self.equalizer, self.current_* track state, and the
track_changed/duration_changed/position_changed signals.
"""

import threading
import time
import unicodedata
from pathlib import Path
from typing import Optional

from src.core.logger_config import logger

SUPPORTED_FORMATS = {".wav", ".flac", ".mp3", ".aiff", ".aif", ".ogg"}


class PlayerTrackLoadingMixin:
    """Opens a track for streaming playback and pre-loads the next one in
    the queue in the background so track transitions are gapless."""

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
        original_path = file_path  # matches track_file_path as stored in the DB
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
                if self._next_file == original_path and self._next_sf_reader is not None:
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
                except OSError:
                    pass

            logger.debug(f"file opened at {time.time()}")
            self.current_file = original_path
            self._resolved_file_path = file_path
            self.current_format = file_path.suffix.lower()
            self.current_bit_depth = 32
            self._duration = int(new_frames / new_sr * 1000)
            self._position_timer.stop()
            self._position = 0
            self._frames_played = 0
            self.position_changed.emit(0)

            self.equalizer.set_sample_rate(new_sr)
            self._gain_factor = self._calculate_gain_factor()

            self.track_changed.emit(original_path)
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

        except (OSError, RuntimeError) as exc:
            self.error_occurred.emit(f"Failed to open audio: {exc}")
            logger.error(f"load_track error: {exc}")
            return False

    def _start_preload_next(self):
        """
        Determine the next track in the queue and open its SoundFile in a
        background thread so it is ready before the current track ends.
        """
        # Cancel any in-flight preload. We can't actually kill the thread, so we
        # bump a generation counter — when the in-flight preload finishes, it
        # checks its own generation against the current one before storing its
        # result, so a superseded preload can no longer clobber a newer one.
        with self._preload_lock:
            self._preload_generation += 1
            my_generation = self._preload_generation

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
                    if my_generation != self._preload_generation:
                        # A different track became "next" while we were
                        # opening this one — discard it instead of storing
                        # a result for a track we're no longer heading to.
                        reader.close()
                        return
                    self._next_sf_reader = reader
                    self._next_file = next_path
                    self._next_sample_rate = reader.samplerate
                    self._next_channels = reader.channels
                    self._next_total_frames = len(reader)
                logger.debug(f"Pre-loaded next track: {next_path.name}")
            except OSError as exc:
                logger.warning(f"Pre-load failed for {next_path.name}: {exc}")

        self._preload_thread = threading.Thread(
            target=_preload, daemon=True, name="TrackPreload"
        )
        self._preload_thread.start()
