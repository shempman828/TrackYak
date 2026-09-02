"""
player_reader.py — background decode thread for MusicPlayer.

Expects the host class to provide: self._buffer_lock, self._audio_buffer,
self._reader_lock, self._reader_stop, self._reader_thread, self._sf_reader,
self._resolved_file_path, self._total_frames, self._current_frame,
self.current_channels, self.sf (soundfile module).
"""

import contextlib
import os
from pathlib import Path
import subprocess
import tempfile
import threading

import numpy as np

from src.foundation.logger_config import logger

# Formats libsndfile (soundfile) can't decode natively and must be
# transcoded to WAV via ffmpeg before soundfile can open them.
_FFMPEG_TRANSCODE_FORMATS = {".m4a"}


def _transcode_to_wav(file_path: Path) -> Path:
    """Decode file_path to a temp float32 WAV via ffmpeg so the rest of the
    reader pipeline (SoundFile-based read/seek/resync) can treat it like any
    other format. Raises OSError on failure. Caller is responsible for
    unlinking the returned path once it has opened it."""
    fd, tmp_name = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-y",
                "-i",
                str(file_path),
                "-f",
                "wav",
                "-acodec",
                "pcm_f32le",
                str(tmp_path),
            ],
            check=True,
            capture_output=True,
            stdin=subprocess.DEVNULL,
            timeout=30,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as exc:
        tmp_path.unlink(missing_ok=True)
        raise OSError(f"ffmpeg transcode failed: {exc}") from exc
    return tmp_path


def _open_soundfile(sf_module, file_path: Path):
    """Open file_path as a soundfile.SoundFile, transcoding first via ffmpeg
    if the format isn't natively decodable by libsndfile."""
    if file_path.suffix.lower() in _FFMPEG_TRANSCODE_FORMATS:
        tmp_path = _transcode_to_wav(file_path)
        try:
            return sf_module.SoundFile(str(tmp_path), mode="r")
        finally:
            # Safe to unlink immediately: soundfile/libsndfile keeps its own
            # open file descriptor, and on Linux an unlinked-but-open file's
            # data stays alive until that descriptor closes.
            tmp_path.unlink(missing_ok=True)
    return sf_module.SoundFile(str(file_path), mode="r")


BLOCKSIZE = 16384  # Frames per decode chunk the reader thread pushes into the
# ring buffer. NOT the audio-callback/PortAudio block size -- that is
# STREAM_BLOCKSIZE in player_transport.py, deliberately left at 0 (let PortAudio
# pick). Coupling the two used to force a 16384-frame callback, turning any
# brief scheduling/GIL stall into a full ~371ms gap of silence.
# How long UI-thread callers (seek/stop/load_track) will wait to acquire
# _reader_lock before giving up. The reader thread can be blocked inside a
# stalled disk read (flaky external/network storage) while holding this
# lock; without a timeout, acquiring it from the UI thread blocks the Qt
# event loop indefinitely -- observed as "Python is not responding."
READER_LOCK_TIMEOUT = 2.0
# How many blocks to read ahead into the ring buffer. At 44.1kHz this is
# ~37s of lookahead (100 * 16384 / 44100) -- deliberately generous so the
# reader thread has enough banked audio to absorb OS scheduling stalls
# (e.g. a CPU-heavy game elsewhere on the system delaying this process)
# without the callback ever seeing an empty buffer.
READ_AHEAD_BLOCKS = 100


class PlayerReaderMixin:
    """Decodes audio from disk in the background and feeds the ring buffer
    that the real-time audio callback (see PlayerCallbackMixin) drains."""

    def _start_reader_thread(self):
        """Start background thread that decodes audio into the buffer.

        No-op if a reader thread is already running for the current reader —
        load_track() always starts one, and play() may call this again right
        after (e.g. when it has to open a new device stream for a sample-rate/
        channel change). Restarting here would orphan the already-running
        thread (nothing ever stops it) and wipe out whatever it already
        decoded without resetting _current_frame, silently dropping the start
        of the track.
        """
        if self._reader_thread is not None and self._reader_thread.is_alive():
            return
        self._reader_stop.clear()
        with self._buffer_lock:
            self._audio_buffer.clear()
            self._buffer_epoch += 1
            self._callback_final_chunk_seen = False

        # Prime the buffer with one chunk synchronously before returning. When
        # a track change reuses the existing stream (see play()), the live
        # callback keeps firing on its own real-time thread the whole time —
        # handing the very first decode off to a background thread leaves a
        # window (up to one callback period, ~BLOCKSIZE/samplerate) where the
        # callback sees an empty buffer and outputs silence, heard as a hitch.
        # This is most likely to bite on a cold-cache read of a large file.
        # Errors here are swallowed; the reader loop below retries from
        # scratch with its full resync/reopen handling.
        try:
            with self._reader_lock:
                reader = self._sf_reader
                if reader is not None:
                    known_length = self._total_frames > 0
                    if known_length:
                        to_read = min(BLOCKSIZE, self._total_frames - self._current_frame)
                    else:
                        to_read = BLOCKSIZE
                    if to_read > 0:
                        chunk = reader.read(to_read, dtype="float32", always_2d=True)
                        self._current_frame += len(chunk)
                        if len(chunk):
                            with self._buffer_lock:
                                self._audio_buffer.append(chunk)
        except (OSError, self.sf.LibsndfileError) as exc:
            logger.warning(f"Buffer priming failed, deferring to reader thread: {exc}")

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
            self._buffer_epoch += 1
            self._callback_final_chunk_seen = False

    def _reader_loop(self):
        """
        Background thread: reads BLOCKSIZE chunks from the SoundFile and
        pushes them into _audio_buffer. Sleeps when the buffer is full.
        """
        while not self._reader_stop.is_set():
            with self._buffer_lock:
                buf_len = len(self._audio_buffer)

            if buf_len >= READ_AHEAD_BLOCKS:
                self._reader_stop.wait(timeout=0.02)
                continue

            # Hold the reader lock for the entire read so a seek on the main
            # thread can't move the file cursor between our frame check and
            # our reader.read() call — that interleaving is what causes FLAC
            # desync and bad header errors when skipping quickly.
            with self._reader_lock:
                reader = self._sf_reader
                if reader is None:
                    break
                # Some files (streaming-encoded FLAC with an unset STREAMINFO
                # total_samples, VBR MP3s missing a Xing/VBRI header, etc.)
                # report an unreliable or zero frame count from the header.
                # Trusting it to decide when to stop reading can leave the
                # reader loop breaking before it ever reads a byte, which
                # means end-of-track is never detected and the track just
                # plays silence forever. When the count looks usable, use it
                # to size reads; otherwise fall back to reading full blocks
                # and let the short/empty read below signal real EOF.
                known_length = self._total_frames > 0
                if known_length:
                    frames_remaining = self._total_frames - self._current_frame
                    if frames_remaining <= 0:
                        break
                    to_read = min(BLOCKSIZE, frames_remaining)
                else:
                    to_read = BLOCKSIZE
                unrecoverable = False
                try:
                    chunk = reader.read(to_read, dtype="float32", always_2d=True)
                    self._current_frame += len(chunk)
                except (OSError, self.sf.LibsndfileError) as exc:
                    logger.error(f"Reader thread decode error, attempting to resync: {exc}")
                    # Re-seek to the SAME position and retry once before giving
                    # up on it. Jumping straight to current_frame + BLOCKSIZE
                    # (the fallback below) permanently drops that span of
                    # audio — harmless mid-track, but on the very first read
                    # of a track (current_frame == 0) it silently skips the
                    # opening of the file, which is the "track doesn't start
                    # at the beginning" bug. Most decode errors here are a
                    # transient decoder hiccup that a fresh seek clears up.
                    try:
                        reader.seek(self._current_frame)
                        chunk = reader.read(to_read, dtype="float32", always_2d=True)
                        self._current_frame += len(chunk)
                    except (OSError, self.sf.LibsndfileError):
                        # Same-handle retry also failed. If we're still at frame 0,
                        # the handle itself is the likely culprit rather than a
                        # transient decode hiccup — this is the common case for a
                        # preloaded reader that was opened well before playback
                        # started (only forward/skip-next swaps in a reader that was
                        # preloaded ahead of time; going backward always opens a
                        # fresh handle immediately before use, which is why this
                        # failure mode only ever shows up going forward). Reopen a
                        # brand new handle from disk and retry once more before
                        # resorting to the destructive skip-forward below.
                        recovered = False
                        if self._current_frame == 0 and self._resolved_file_path is not None:
                            try:
                                fresh_reader = _open_soundfile(self.sf, self._resolved_file_path)
                                chunk = fresh_reader.read(to_read, dtype="float32", always_2d=True)
                                self._current_frame += len(chunk)
                                with contextlib.suppress(OSError, self.sf.LibsndfileError):
                                    reader.close()
                                reader = fresh_reader
                                self._sf_reader = fresh_reader
                                recovered = True
                            except (OSError, self.sf.LibsndfileError) as reopen_exc:
                                logger.error(
                                    f"Fresh reopen after decode error also failed: {reopen_exc}"
                                )
                        if not recovered:
                            try:
                                skip_to = self._current_frame + BLOCKSIZE
                                if known_length:
                                    skip_to = min(skip_to, self._total_frames)
                                reader.seek(skip_to)
                                self._current_frame = skip_to
                            except (OSError, self.sf.LibsndfileError) as seek_exc:
                                logger.error(
                                    f"Reader thread could not resync after decode error, "
                                    f"ending track: {seek_exc}"
                                )
                                # Can't recover — push an empty chunk so the callback's
                                # short-read check signals track-finished once the
                                # buffer drains, instead of hanging silently forever.
                                if known_length:
                                    self._current_frame = self._total_frames
                                chunk = np.zeros((0, self.current_channels), dtype="float32")
                                unrecoverable = True
                            else:
                                continue

            with self._buffer_lock:
                self._audio_buffer.append(chunk)

            if unrecoverable or len(chunk) < to_read:
                # Decoder returned less than requested — genuine EOF,
                # regardless of what the header's frame count claims.
                break
