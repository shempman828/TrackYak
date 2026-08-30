"""Regression: the PortAudio output stream must not be opened with a large
fixed block size.

It used to pass blocksize=BLOCKSIZE (16384, the reader thread's decode-chunk
size). At 44.1kHz that is one ~371ms callback period, so any callback that ran
even slightly late — a main- or worker-thread allocation burst holding the GIL,
or a stop-the-world GC pause — dropped a full 371ms of audio at once, heard as
a hitch, while the app-level ring buffer stayed completely full. blocksize=0
lets PortAudio pick a small block and ride short stalls out of its own queue.
"""

from src.player.player_reader import BLOCKSIZE
from src.player.player_transport import STREAM_BLOCKSIZE


def test_stream_blocksize_lets_portaudio_choose():
    assert STREAM_BLOCKSIZE == 0


def test_stream_blocksize_is_decoupled_from_reader_decode_chunk():
    # The reader's decode chunk can stay large for efficient disk reads; that
    # must not dictate the real-time callback block.
    assert STREAM_BLOCKSIZE != BLOCKSIZE
