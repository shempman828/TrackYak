"""
player_callback.py — the real-time PortAudio callback for MusicPlayer, plus
its deferred diagnostics.

Expects the host class to provide: self._finish_pending, self._track_finished
(signal), self._stream_generation, self.playing, self.paused, self._buffer_lock,
self._audio_buffer, self._total_frames, self._current_frame, self._gain_factor,
self.volume_level, self.equalizer, self._frames_played, self._is_advancing,
self._buffer_epoch (bumped by reset sites when _audio_buffer is cleared).
"""

import numpy as np

from src.core.logger_config import logger
from src.player.player_reader import BLOCKSIZE


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

    def _audio_callback(self, outdata: np.ndarray, frames: int, time, status, generation: int):
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
            # The reader thread pushes fixed 16384-frame decode chunks, but this
            # callback's `frames` (STREAM_BLOCKSIZE=0) is smaller and varies per
            # call, so a single popped chunk feeds many callbacks. Serve `frames`
            # from the partially-consumed residual, popping (and gain/EQ-ing) a
            # fresh chunk only when the residual runs out. A short final chunk
            # from the reader (its EOF read) signals track-finished.
            effective_gain = self._gain_factor * (self.volume_level / 100.0)
            written = 0

            while written < frames:
                residual = self._callback_residual
                if (
                    residual is None
                    or self._callback_residual_pos >= len(residual)
                    or self._callback_residual_epoch != self._buffer_epoch
                ):
                    with self._buffer_lock:
                        chunk = self._audio_buffer.popleft() if self._audio_buffer else None
                        epoch = self._buffer_epoch
                    if chunk is None:
                        # App-level buffer underrun (reader thread fell behind) —
                        # distinct from PortAudio's own `status` flag above: this
                        # one never reaches PortAudio late, so PortAudio never
                        # flags it, but it's heard as the same hitch. Also the
                        # path hit at a clean end-of-track once the buffer drains.
                        self._pending_buffer_underrun_count += 1
                        outdata[written:] = 0
                        if self._callback_final_chunk_seen or (
                            self._total_frames > 0 and self._current_frame >= self._total_frames
                        ):
                            self._emit_track_finished_once()
                        return
                    if len(chunk) == 0:
                        # Reader's explicit EOF sentinel (unrecoverable decode).
                        outdata[written:] = 0
                        self._emit_track_finished_once()
                        return

                    chunk = chunk * effective_gain
                    if len(chunk) >= 32:
                        chunk = self.equalizer.process_audio(chunk)
                    if epoch != self._buffer_epoch:
                        # A reset (seek/stop) fired while we were popping/EQ-ing
                        # this chunk — it belongs to the pre-reset buffer. Drop
                        # it rather than serve stale audio or install it as the
                        # residual under a now-stale epoch.
                        outdata[written:] = 0
                        return
                    # The reader only ever pushes a shorter-than-BLOCKSIZE chunk
                    # as its final read of the track.
                    if len(chunk) < BLOCKSIZE:
                        self._callback_final_chunk_seen = True
                    self._callback_residual = residual = chunk
                    self._callback_residual_pos = 0
                    self._callback_residual_epoch = epoch

                pos = self._callback_residual_pos
                n = min(len(residual) - pos, frames - written)
                outdata[written : written + n] = residual[pos : pos + n]
                self._callback_residual_pos = pos + n
                written += n
                self._frames_played += n

            if self._callback_residual is None:
                # frames == 0: the write loop above never ran, so no chunk was
                # popped and no residual installed. Nothing to drain, nothing to
                # finish — bail before len(None). (Previously this raised a
                # TypeError that the broad except below silently swallowed.)
                return
            residual_drained = self._callback_residual_pos >= len(self._callback_residual)
            if not residual_drained:
                return
            if self._callback_final_chunk_seen:
                self._emit_track_finished_once()
            elif self._total_frames > 0 and self._current_frame >= self._total_frames:
                with self._buffer_lock:
                    buffer_empty = len(self._audio_buffer) == 0
                if buffer_empty:
                    self._emit_track_finished_once()
        except Exception as exc:
            # Intentional broad boundary catch: this runs on the real-time audio
            # thread and must never propagate or the stream aborts (see #163).
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
