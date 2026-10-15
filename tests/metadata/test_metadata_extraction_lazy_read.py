"""Regression: MetadataExtractor.extract_metadata must not read the whole audio file into memory.

Bug: it read every byte of the file once and shared that buffer with all extractors, so each
large WAV/FLAC import used hundreds of MB. The extractors now share a LazyFileView, which reads
only the blocks a parser touches and caps its cache.

  AC1  a WAV with a large data chunk and a LIST/INFO chunk after it reads only a small part of
       the file, and still gets the tags, the duration and the true file size.
  AC2  a FLAC with a large audio tail reads only its metadata blocks; its bit rate still uses
       the true file size.
  AC3  an MP3 with no Xing header still gets its duration from a scan of every frame, but the
       view never keeps more than MAX_CACHED_BLOCKS blocks.
  AC4  LazyFileView indexing, slicing and rfind (also across a block edge) agree with bytes.
"""

import struct

import pytest

from src.metadata.readers import metadata_extraction
from src.metadata.readers.metadata_extraction import MetadataExtractor
from src.metadata.readers.metadata_file_view import LazyFileView


class _CountingFile:
    """File proxy that counts the bytes read through it."""

    def __init__(self, file_obj):
        self._file = file_obj
        self.bytes_read = 0

    def read(self, size=-1):
        chunk = self._file.read(size)
        self.bytes_read += len(chunk)
        return chunk

    def __getattr__(self, name):
        return getattr(self._file, name)


@pytest.fixture
def views(monkeypatch):
    """Record each LazyFileView that extract_metadata makes, with a byte counter on its file."""
    made = []

    class _RecordingView(LazyFileView):
        def __init__(self, file_obj):
            self.counter = _CountingFile(file_obj)
            super().__init__(self.counter)
            self.max_blocks_seen = 0
            made.append(self)

        def _block(self, index):
            block = super()._block(index)
            self.max_blocks_seen = max(self.max_blocks_seen, len(self._blocks))
            return block

    monkeypatch.setattr(metadata_extraction, "LazyFileView", _RecordingView)
    return made


def _chunk(chunk_id: bytes, payload: bytes, endian: str) -> bytes:
    return chunk_id + struct.pack(f"{endian}I", len(payload)) + payload + (b"\x00" if len(payload) & 1 else b"")


# --- AC1 ---------------------------------------------------------------------


def test_wav_reads_only_chunk_headers_and_finds_info_after_data(tmp_path, views):
    channels, sample_rate, bits = 2, 44100, 16
    audio = b"\x00" * (sample_rate * channels * bits // 8 * 120)  # 120 s, ~21 MB
    fmt = struct.pack("<HHIIHH", 1, channels, sample_rate, sample_rate * channels * bits // 8, channels * bits // 8, bits)
    info = b"INFO" + _chunk(b"INAM", b"Late Title\x00", "<")
    body = b"WAVE" + _chunk(b"fmt ", fmt, "<") + _chunk(b"data", audio, "<") + _chunk(b"LIST", info, "<")
    path = tmp_path / "big.wav"
    path.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)

    metadata = MetadataExtractor().extract_metadata(str(path))

    assert metadata["track_name"] == "Late Title"
    assert metadata["duration"] == pytest.approx(120.0)
    assert metadata["file_size"] == path.stat().st_size
    assert views[0].counter.bytes_read < 512 * 1024


# --- AC2 ---------------------------------------------------------------------


def test_flac_reads_only_metadata_blocks_and_bit_rate_uses_file_size(tmp_path, views):
    sample_rate, total_samples = 44100, 44100 * 60
    # STREAMINFO bytes 10..17: sample rate (20 bits), channels-1 (3), bits-1 (5), total samples (36).
    packed = (sample_rate << 44) | (1 << 41) | (15 << 36) | total_samples
    streaminfo = b"\x00" * 10 + packed.to_bytes(8, "big") + b"\x00" * 16
    comment = b"\x00\x00\x00\x00" + struct.pack("<I", 1) + struct.pack("<I", 11) + b"TITLE=Flac!"
    header = b"fLaC" + struct.pack(">I", len(streaminfo)) + streaminfo + struct.pack(">I", (0x80 | 4) << 24 | len(comment)) + comment
    path = tmp_path / "big.flac"
    path.write_bytes(header + b"\x5a" * (20 * 1024 * 1024))

    metadata = MetadataExtractor().extract_metadata(str(path))

    assert metadata["track_name"] == "Flac!"
    assert metadata["duration"] == pytest.approx(60.0)
    assert metadata["bit_rate"] == int(path.stat().st_size * 8 / 60.0 / 1000)
    assert views[0].counter.bytes_read < 512 * 1024


# --- AC3 ---------------------------------------------------------------------


def test_mp3_without_xing_scans_every_frame_with_a_capped_cache(tmp_path, views):
    # MPEG-1 Layer III, 128 kbps, 44.1 kHz, no padding: 417-byte frames of 1152 samples.
    frame = struct.pack(">I", 0xFFFB9000) + b"\x00" * 413
    frame_count = 15_000  # ~6 MB, more than the cache ceiling
    path = tmp_path / "noxing.mp3"
    path.write_bytes(frame * frame_count)

    metadata = MetadataExtractor().extract_metadata(str(path))

    assert metadata["duration"] == pytest.approx(frame_count * 1152 / 44100)
    assert metadata["bit_rate"] == 128
    assert views[0].max_blocks_seen <= LazyFileView.MAX_CACHED_BLOCKS
    assert views[0].max_blocks_seen * LazyFileView.BLOCK_SIZE < path.stat().st_size


# --- AC4 ---------------------------------------------------------------------


def test_lazy_view_matches_bytes(tmp_path):
    block = LazyFileView.BLOCK_SIZE
    raw = bytes(range(256)) * (3 * block // 256) + b"OggS-tail"
    # A marker that crosses the edge between block 0 and block 1.
    raw = raw[: block - 2] + b"MARK" + raw[block + 2 :]
    path = tmp_path / "f.bin"
    path.write_bytes(raw)

    with path.open("rb") as f:
        view = LazyFileView(f)
        assert len(view) == len(raw)
        for i in (0, 1, block - 1, block, -1, -128):
            assert view[i] == raw[i]
        for s in (slice(0, 4), slice(block - 3, block + 3), slice(-128, -125), slice(-128, None), slice(10, 2 * block + 7), slice(5, 5), slice(len(raw) - 2, len(raw) + 50), slice(0, 40, 3)):
            assert view[s] == raw[s]
        assert view.rfind(b"MARK") == raw.rfind(b"MARK")
        assert view.rfind(b"OggS") == raw.rfind(b"OggS")
        assert view.rfind(b"OggS", 0, len(raw) - 9) == raw.rfind(b"OggS", 0, len(raw) - 9)
        assert view.rfind(b"absent") == -1
        with pytest.raises(IndexError):
            view[len(raw)]
