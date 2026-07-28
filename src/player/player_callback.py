"""
player_callback.py — the real-time PortAudio callback for MusicPlayer, plus
its deferred diagnostics.

Expects the host class to provide: self._finish_pending, self._track_finished
(signal), self._stream_generation, self.playing, self.paused, self._buffer_lock,
self._audio_buffer, self._total_frames, self._current_frame, self._gain_factor,
self.volume_level, self.equalizer, self._frames_played, self._is_advancing.
"""

import numpy as np

from src.core.logger_config import logger


class PlayerCallbackMixin:
    """Runs on PortAudio's real-time thread. Must never block (no logging,
    no locks held longer than a dict pop) — see _flush_callback_diagnostics
    for why even the diagnostic logging is deferred to the main thread.
    """

    def _emit_track_finished_once(self):
        """Emit _track_finished exactly once per stream lifetime (callback-safe).
        Uses a threading.Event so the check-and-set is atomic across threads."""
        if not self._finish_pending.is_set():
            self._finish_pending.set()
            self._track_finished.emit()

    def _audio_callback(
        self, outdata: np.ndarray, frames: int, time, status, generation: int
    ):
        if status:
            # Don't log here — the callback runs on PortAudio's realtime
            # thread, and a blocking disk write from logger.warning() here
            # can itself delay the next buffer long enough to cause another
            # underrun, cascading into the very hitches it's reporting.
            # _flush_callback_diagnostics (called from the main thread's
            # position timer) logs this instead.
            self._pending_status_count += 1
            self._last_status_value = status

        if generation != self._stream_generation:
            outdata.fill(0)
            return

        if not self.playing or self.paused:
            outdata.fill(0)
            return

        # Every other audio-processing path in this codebase (AudioCalculations,
        # Equalizer.process_audio) catches its own exceptions and falls back
        # to a safe default so one bad buffer can't take anything down with
        # it. This callback does the same below — an exception here is not a
        # CallbackStop/CallbackAbort, so sounddevice would otherwise let it
        # propagate straight to the PortAudio/CFFI boundary instead of just
        # glitching the audio. That's especially reachable when something
        # CPU/GIL-heavy (e.g. batch audio analysis) is running concurrently
        # on the main process and this thread ends up starved mid-buffer.
        try:
            # Pop a pre-decoded chunk from the buffer
            with self._buffer_lock:
                if self._audio_buffer:
                    chunk = self._audio_buffer.popleft()
                else:
                    # App-level buffer underrun (reader thread fell behind) —
                    # distinct from PortAudio's own `status` flag above: this
                    # one never reaches PortAudio late, so PortAudio never
                    # flags it, but it's heard as the same hitch.
                    self._pending_buffer_underrun_count += 1
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
            elif self._total_frames > 0 and self._current_frame >= self._total_frames:
                with self._buffer_lock:
                    buffer_empty = len(self._audio_buffer) == 0
                if buffer_empty:
                    self._emit_track_finished_once()
        except Exception as exc:
            # Don't log here — see the comment on the `status` branch above.
            self._pending_error_count += 1
            self._last_error_message = str(exc)
            outdata.fill(0)

    def _flush_callback_diagnostics(self):
        """Log anything the audio callback flagged since the last call.

        Called from the main-thread position timer (see PlayerPositionMixin)
        rather than from the callback itself, so the logging I/O never runs
        on PortAudio's real-time thread.
        """
        if self._pending_status_count:
            count, status = self._pending_status_count, self._last_status_value
            self._pending_status_count = 0
            logger.warning(
                f"Audio callback status: {status} x{count} since last check "
                f"(playing={self.playing}, advancing={self._is_advancing}, "
                f"frames_done={self._current_frame})"
            )
        if self._pending_error_count:
            count, msg = self._pending_error_count, self._last_error_message
            self._pending_error_count = 0
            logger.error(f"Audio callback error x{count} since last check: {msg}")
        if self._pending_buffer_underrun_count:
            count = self._pending_buffer_underrun_count
            self._pending_buffer_underrun_count = 0
            with self._buffer_lock:
                buf_len = len(self._audio_buffer)
            logger.warning(
                f"Audio buffer underrun x{count} since last check "
                f"(reader thread fell behind; buffer now has {buf_len} chunks)"
            )
