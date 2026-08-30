"""
player_transport.py — playback transport controls (play/pause/stop/seek/
next/previous) for MusicPlayer.

Expects the host class to provide: self.sd, self.audio_stream, self.current_*
track state, self._reader_lock/_buffer_lock and reader-thread controls (see
PlayerReaderMixin), self._get_device_config/_prepare_exclusive_device (see
PlayerDeviceMixin), self.queue_manager, self._position_timer, and the
state_changed/error_occurred signals.
"""

from pathlib import Path
import threading
import time

from src.core.logger_config import logger
from src.player.player_position import PLAY_COUNT_THRESHOLD
from src.player.player_reader import READER_LOCK_TIMEOUT

RESTART_THRESHOLD_MS = 10_000

# PortAudio callback block size. 0 = let PortAudio choose a small block and keep
# a deep buffer sized by latency="high". Do NOT set this to the reader's decode
# chunk size (16384): a large fixed block means every callback that runs even
# slightly late -- because a main- or worker-thread alloc burst is holding the
# GIL, or triggered a GC pause -- drops a full ~371ms of audio at once instead
# of a ~10-40ms blip PortAudio's own queue can ride through.
STREAM_BLOCKSIZE = 0


class PlayerTransportMixin:
    """Play/pause/stop/seek and track-advance controls."""

    def play(self):
        logger.debug(f"play() ENTER at {time.time():.3f}")
        if self.sd is None and not self._initialize_audio_backend():
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
                logger.info(f"Playback continued on existing stream: {self.current_file.name}")
                logger.debug(f"play() EXIT at {time.time():.3f}")
                return

            # ── Open a new stream (first play, or sample rate/channel count changed) ─
            self._close_stream()
            self._stream_generation += 1
            my_generation = self._stream_generation
            self._callback_native_tid = None
            self._finish_pending.clear()

            device_config = self._get_device_config()

            def _stamped_callback(outdata, frames, time, status, _gen=my_generation):
                if self._callback_native_tid is None:
                    self._callback_native_tid = threading.get_native_id()
                self._audio_callback(outdata, frames, time, status, _gen)

            def _open_stream(device):
                stream = self.sd.OutputStream(
                    samplerate=self.current_sample_rate,
                    channels=self.current_channels,
                    dtype="float32",
                    device=device,
                    latency=device_config.get("latency", "high"),
                    blocksize=STREAM_BLOCKSIZE,
                    callback=_stamped_callback,
                )
                stream.start()
                return stream

            open_device = device_config["device"]
            open_attempts = 1
            opened_exclusive_device = False
            if self.exclusive_mode:
                grabbed_index = self._prepare_exclusive_device(open_device)
                if grabbed_index is not None:
                    open_device = grabbed_index
                    open_attempts = 3
                    opened_exclusive_device = True

            try:
                last_exc = None
                for attempt in range(open_attempts):
                    try:
                        self.audio_stream = _open_stream(open_device)
                        last_exc = None
                        break
                    except (self.sd.PortAudioError, OSError, ValueError) as exc:
                        last_exc = exc
                        if attempt < open_attempts - 1:
                            time.sleep(0.1)
                if last_exc is not None:
                    raise last_exc
            except (self.sd.PortAudioError, OSError, ValueError) as exc:
                opened_exclusive_device = False
                if self._suspended_sink_name is not None:
                    self._suspend_sink(self._suspended_sink_name, False)
                    self._suspended_sink_name = None
                if device_config["device"] is not None:
                    # The configured/previous device likely disconnected mid-session,
                    # or (in exclusive mode) a raw hw: device is already claimed by
                    # PipeWire/PulseAudio (e.g. PortAudio "Device unavailable"). Fall
                    # back to the system default so playback still works, but surface
                    # it — this used to fail silently except for a log line.
                    logger.warning(
                        f"Output device unavailable ({exc}); falling back to default device"
                    )
                    if self.exclusive_mode:
                        self.error_occurred.emit(
                            "Bit-perfect device unavailable (likely in use by "
                            "PulseAudio/PipeWire) — falling back to the default "
                            "output. Close other apps using the device, or select "
                            "a different one, and try again."
                        )
                    else:
                        self.error_occurred.emit(
                            f"Output device unavailable ({exc}); falling back to "
                            "the default device."
                        )
                    self.current_device = None
                    fallback_config = self._get_device_config()
                    self.audio_stream = _open_stream(fallback_config["device"])
                else:
                    raise
            if opened_exclusive_device:
                self._request_exclusive_realtime_priority()
            # A live output stream means a real-time callback thread with a hard
            # deadline. Automatic cyclic GC is stop-the-world (freezes that
            # thread too), so hold it off until the stream closes -- see
            # _suspend_gc_during_playback().
            self._suspend_gc_during_playback()
            self._start_reader_thread()

            self.playing = True
            self.paused = False
            self.state_changed.emit("playing")
            self._position_timer.start()
            logger.info(f"Playback started: {self.current_file.name}")
            logger.debug(f"play() EXIT at {time.time():.3f}")

        except (OSError, RuntimeError, self.sd.PortAudioError) as exc:
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
        if self._reader_lock.acquire(timeout=READER_LOCK_TIMEOUT):
            try:
                if self._sf_reader is not None:
                    try:
                        self._sf_reader.seek(0)
                        self._current_frame = 0
                    except (OSError, self.sf.LibsndfileError) as e:
                        logger.debug(f"Could not seek reader to 0 on stop: {e}")
            finally:
                self._reader_lock.release()
        else:
            logger.warning(
                "stop(): reader lock busy (reader thread likely stuck on slow "
                "I/O); skipping cursor reset"
            )
        self._frames_played = 0
        with self._buffer_lock:
            self._audio_buffer.clear()
        self._position = 0
        # Close the stream so play() opens a fresh one from frame 0
        self._close_stream()
        self.state_changed.emit("stopped")
        logger.debug("Playback stopped")

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
        except (TypeError, RuntimeError) as exc:
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
        except (TypeError, RuntimeError) as exc:
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

            if not self._reader_lock.acquire(timeout=READER_LOCK_TIMEOUT):
                logger.warning(
                    "seek(): reader lock busy (reader thread likely stuck on "
                    "slow I/O); aborting seek"
                )
                # _stop_reader_thread() already tore down the reader thread;
                # restart it (from the unchanged position) so playback
                # doesn't stay silently stopped.
                self._start_reader_thread()
                return
            try:
                self._sf_reader.seek(target_frame)
                self._current_frame = target_frame
            finally:
                self._reader_lock.release()

            self._position = position_ms
            self._frames_played = int(position_ms / 1000.0 * self.current_sample_rate)

            if self._duration > 0 and (position_ms / self._duration) < PLAY_COUNT_THRESHOLD:
                self._has_reached_threshold = False
                self._play_count_recorded = False

            # Restart the reader thread so the buffer refills from the new position.
            self._start_reader_thread()

            logger.debug(f"Seek to {position_ms}ms (frame {target_frame})")
        except (OSError, TypeError, ValueError, RuntimeError) as exc:
            logger.error(f"Seek error: {exc}")

    def seek_forward(self):
        if self._duration > 0:
            self.seek(min(self._duration, self._position + 10_000))

    def seek_backward(self):
        if self._duration > 0:
            self.seek(max(0, self._position - 10_000))

    def set_repeat_mode(self, mode: int):
        self.repeat_mode = mode
        logger.debug(f"Repeat mode: {mode}")

    def _handle_playback_finished(self):
        """Called on the main thread when the current track ends."""
        logger.debug(f"_handle_playback_finished called at {time.time()}")
        if self.repeat_mode == 1:
            self.seek(0)
            self.play()
        else:
            self.play_next()
