"""
player_feeder.py — the background thread that pushes decoded audio into the
PortAudio output stream, plus its deferred diagnostics.

Design: PortAudio's output stream is opened WITHOUT a callback. A dedicated
feeder thread pulls the reader's decode chunks out of the ring buffer, applies
gain + EQ, and hands them to ``stream.write()`` in small slices. ``write()``
blocks *inside PortAudio's C code with the GIL released* until the device has
room, and PortAudio's own C thread moves the samples to the DAC. So no Python
ever runs on the real-time thread and a GIL stall on the feeder (a DB write,
an image decode, a Qt model rebuild elsewhere in the process) just delays the
next ``write()`` by a few ms instead of blowing a hardware deadline. The
~37 s ring buffer (player_reader.py) is the cushion; the feeder only has to
average keeping up.

The feeder thread OWNS the stream's start/stop/write/abort for the stream's
lifetime — the main thread only constructs the stream and closes it once the
feeder has been joined (see _stop_feeder_thread / _close_stream).

Expects the host class to provide: self.audio_stream, self._finish_pending,
self._track_finished (signal), self._stream_generation, self.playing,
self.paused, self._buffer_lock, self._audio_buffer, self._total_frames,
self._current_frame, self._gain_factor, self.volume_level, self.equalizer,
self._frames_played, self._is_advancing, self._buffer_epoch (bumped by reset
sites when _audio_buffer is cleared), self._final_chunk_seen, and the feeder
control fields set up in MusicPlayer.__init__ (self._feeder_thread,
self._feeder_stop, self._feeder_wake, self._feeder_flush,
self._feeder_native_tid, self._feeder_generation).
"""

import contextlib
import threading

import numpy as np

from src.foundation.logger_config import logger
from src.player.player_device import demote_thread_from_realtime
from src.player.player_reader import BLOCKSIZE

# Frames handed to a single stream.write() call. One decode chunk (BLOCKSIZE,
# 16384) is written out in slices this size so the feeder loop re-checks
# stop/pause/seek roughly every FEEDER_WRITE_BLOCKSIZE/samplerate seconds
# (~46 ms at 44.1 kHz) instead of being stuck in one ~370 ms write.
FEEDER_WRITE_BLOCKSIZE = 2048


class PlayerFeederMixin:
    """The feeder thread and its lifecycle. Nothing here runs on PortAudio's
    real-time thread — it is an ordinary Python thread whose only hard job is
    to stay, on average, ahead of the device."""

    # ── lifecycle ────────────────────────────────────────────────────────────

    def _start_feeder_thread(self):
        """Start the feeder thread for the current output stream.

        No-op if one is already running (play() reuses a live stream across
        track changes, and the feeder follows the ring buffer through the
        transition on its own — it must not be restarted or the transition
        gets a gap)."""
        if self._feeder_thread is not None and self._feeder_thread.is_alive():
            return
        if self.audio_stream is None:
            return
        self._feeder_stop.clear()
        self._feeder_wake.clear()
        self._feeder_flush.clear()
        self._feeder_generation = self._stream_generation
        self._feeder_native_tid = None
        self._feeder_thread = threading.Thread(
            target=self._feeder_loop,
            args=(self._stream_generation,),
            daemon=True,
            name="AudioFeeder",
        )
        self._feeder_thread.start()

    def _stop_feeder_thread(self):
        """Signal the feeder thread to exit and wait briefly. Safe to call
        more than once and when no feeder is running."""
        self._feeder_stop.set()
        self._feeder_wake.set()
        thread = self._feeder_thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        self._feeder_thread = None

    def request_feeder_flush(self):
        """Ask the feeder to drop whatever PortAudio has already buffered and
        pick up from the current ring-buffer position (used by seek so the
        jump is heard promptly instead of after the pre-seek tail)."""
        if self._feeder_thread is not None and self._feeder_thread.is_alive():
            self._feeder_flush.set()

    def wake_feeder(self):
        """Nudge the feeder out of its paused wait (called by play() on
        resume)."""
        self._feeder_wake.set()

    # ── the loop ─────────────────────────────────────────────────────────────

    def _emit_track_finished_once(self):
        """Emit _track_finished exactly once per stream lifetime. Uses the
        threading.Event as an atomic check-and-set across threads."""
        if not self._finish_pending.is_set():
            self._finish_pending.set()
            self._track_finished.emit()

    def _feeder_loop(self, generation: int):
        stream = self.audio_stream
        if stream is None:
            return
        self._feeder_native_tid = threading.get_native_id()
        gain_scale = np.float32(1.0)
        try:
            while not self._feeder_stop.is_set():
                if generation != self._stream_generation:
                    return

                if self._feeder_flush.is_set():
                    self._feeder_flush.clear()
                    with contextlib.suppress(Exception):
                        stream.abort()

                if self.paused:
                    if stream.active:
                        # abort(), not stop(): with a ~300 ms internal buffer
                        # (OUTPUT_LATENCY) stop() would keep playing for that
                        # long after the user hit pause. abort() cuts instantly;
                        # the cost is that resume replays roughly half the
                        # buffer (the reader cursor didn't advance past what the
                        # feeder wrote), which is imperceptible.
                        with contextlib.suppress(Exception):
                            stream.abort()
                    self._feeder_wake.wait(timeout=0.2)
                    self._feeder_wake.clear()
                    continue

                if not stream.active:
                    try:
                        stream.start()
                    except Exception as exc:
                        self._pending_error_count += 1
                        self._last_error_message = f"stream.start: {exc}"
                        self._feeder_stop.wait(timeout=0.05)
                        continue

                with self._buffer_lock:
                    chunk = self._audio_buffer.popleft() if self._audio_buffer else None
                    epoch = self._buffer_epoch

                if chunk is None:
                    # Ring buffer empty. Either the reader genuinely fell
                    # behind, or we're sitting at end-of-track waiting for the
                    # main thread to advance the queue. Only the former is a
                    # real fault worth counting.
                    if not self._finish_pending.is_set():
                        self._pending_buffer_underrun_count += 1
                    if self._final_chunk_seen or (
                        self._total_frames > 0 and self._current_frame >= self._total_frames
                    ):
                        self._emit_track_finished_once()
                    self._feeder_stop.wait(timeout=0.01)
                    continue

                if len(chunk) == 0:
                    # Reader's explicit EOF sentinel (unrecoverable decode).
                    self._emit_track_finished_once()
                    self._feeder_stop.wait(timeout=0.01)
                    continue

                gain = self._gain_factor * (self.volume_level / 100.0)
                if gain != float(gain_scale):
                    gain_scale = np.float32(gain)
                out = chunk * gain_scale
                if len(out) >= 32:
                    out = self.equalizer.process_audio(out)

                if epoch != self._buffer_epoch:
                    # A reset (seek/stop/track-change) fired while we were
                    # popping/processing this chunk — it belongs to the
                    # pre-reset buffer. Drop it.
                    continue

                out = np.ascontiguousarray(out, dtype="float32")
                # The reader only ever pushes a shorter-than-BLOCKSIZE chunk as
                # its final read of a track.
                if len(out) < BLOCKSIZE:
                    self._final_chunk_seen = True

                wrote_all = self._write_chunk(stream, out, generation, epoch)

                if wrote_all and self._final_chunk_seen:
                    self._emit_track_finished_once()
        except Exception as exc:
            self._pending_error_count += 1
            self._last_error_message = str(exc)
        finally:
            # Exclusive mode may have promoted this thread to real-time
            # (SCHED_RR) via RTKit (_request_exclusive_realtime_priority).
            # That promotion is bound to this exact thread and the feeder is
            # torn down and recreated whenever exclusive mode is toggled or
            # the stream format changes -- so undo it here, on the way out,
            # or a real-time thread is left running on the normal PipeWire
            # path. Clear the shared tid too (unless a newer feeder already
            # claimed it -- possible if our stop-join timed out) so a stale
            # value can't be promoted later. No-op if we were never promoted.
            own_tid = threading.get_native_id()
            demote_thread_from_realtime(own_tid)
            if self._feeder_native_tid == own_tid:
                self._feeder_native_tid = None
            with contextlib.suppress(Exception):
                if stream.active:
                    stream.stop()

    def _write_chunk(self, stream, out: np.ndarray, generation: int, epoch: int) -> bool:
        """Write `out` to the stream in FEEDER_WRITE_BLOCKSIZE slices, bailing
        out between slices if playback state moved under us. Returns True iff
        the whole chunk was written."""
        pos = 0
        total = len(out)
        while pos < total:
            if (
                self._feeder_stop.is_set()
                or self.paused
                or generation != self._stream_generation
                or epoch != self._buffer_epoch
                or self._feeder_flush.is_set()
            ):
                return False
            end = min(pos + FEEDER_WRITE_BLOCKSIZE, total)
            try:
                underflowed = stream.write(out[pos:end])
            except Exception as exc:
                self._pending_error_count += 1
                self._last_error_message = f"stream.write: {exc}"
                return False
            if underflowed and not self.paused:
                # PortAudio had to insert silence since the previous write --
                # the feeder itself didn't get scheduled in time (a GC pause,
                # or CPU starvation). The ring buffer is irrelevant here; this
                # is the direct analogue of the old callback's paOutputUnderflow.
                self._pending_output_underflow_count += 1
            self._frames_played += end - pos
            pos = end
        return True

    # ── diagnostics (flushed from the main-thread position timer) ─────────────

    def _flush_playback_diagnostics(self):
        """Log anything the feeder flagged since the last call. Called from the
        position timer (main thread) so the feeder never does logging I/O."""
        if self._pending_error_count:
            count, msg = self._pending_error_count, self._last_error_message
            self._pending_error_count = 0
            logger.error(f"Audio feeder error x{count} since last check: {msg}")
        if self._pending_output_underflow_count:
            count = self._pending_output_underflow_count
            self._pending_output_underflow_count = 0
            logger.warning(
                f"Audio output underflow x{count} since last check "
                f"(feeder thread missed the device deadline; "
                f"playing={self.playing}, frames_done={self._current_frame})"
            )
        if self._pending_buffer_underrun_count:
            count = self._pending_buffer_underrun_count
            self._pending_buffer_underrun_count = 0
            with self._buffer_lock:
                buf_len = len(self._audio_buffer)
            logger.warning(
                f"Audio buffer underrun x{count} since last check "
                f"(reader thread fell behind; buffer now has {buf_len} chunks)"
            )
