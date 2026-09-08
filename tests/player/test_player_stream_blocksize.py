"""Regression: the PortAudio output stream must not be opened with a large
fixed block size.

blocksize=0 lets PortAudio pick its own period for the blocking-write path the
feeder thread uses. It must not be pinned to the reader's decode-chunk size
(BLOCKSIZE, 16384): the feeder already slices its writes down to
FEEDER_WRITE_BLOCKSIZE so stop/pause/seek stay responsive, and a large
PortAudio period would only add output latency.
"""

from src.player.player_reader import BLOCKSIZE
from src.player.player_transport import STREAM_BLOCKSIZE


def test_stream_blocksize_lets_portaudio_choose():
    assert STREAM_BLOCKSIZE == 0


def test_stream_blocksize_is_decoupled_from_reader_decode_chunk():
    # The reader's decode chunk can stay large for efficient disk reads; that
    # must not dictate the real-time callback block.
    assert STREAM_BLOCKSIZE != BLOCKSIZE
