"""Lazy, bounded file reads: artwork prefix reads and metadata extraction
that read only headers/metadata blocks, not the audio body.
"""

import io
import os
import struct

from PIL import Image
import pytest

from src.metadata.readers import metadata_extraction
from src.metadata.readers.metadata_artwork import ArtworkExtractor
from src.metadata.readers.metadata_extraction import MetadataExtractor
from src.metadata.readers.metadata_file_view import LazyFileView
from src.metadata.writers.id3.metadata_id3_writer import ID3TagWriter
from src.metadata.writers.id3.metadata_writer_id3_picture import Id3PictureWriter
from src.metadata.writers.vorbis.metadata_flac_file_writer import FlacFileWriter
from src.metadata.writers.vorbis.metadata_writer_flac_picture import FlacPictureWriter

# ---------------------------------------------------------------------------
# Regression: extract_artwork_by_role must read only the tag/metadata
# ---------------------------------------------------------------------------


_STREAMINFO = b"\x00" * 34  # contents irrelevant to the metadata surgery under test


def _png(size: int, colour: tuple[int, int, int]) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (size, size), colour).save(buf, "PNG")
    return buf.getvalue()


def _noisy_png(width: int, height: int) -> bytes:
    """Random pixel data defeats PNG's compression, so the encoded picture
    block reliably exceeds ArtworkExtractor._FLAC_READ_CHUNK."""
    raw = os.urandom(width * height * 3)
    buf = io.BytesIO()
    Image.frombytes("RGB", (width, height), raw).save(buf, "PNG")
    return buf.getvalue()


def _make_flac(path, picture_payloads: list[bytes], audio_tail: bytes = b"") -> None:
    writer = FlacFileWriter()
    blocks = [(0, _STREAMINFO)] + [(6, payload) for payload in picture_payloads]
    path.write_bytes(writer._serialize_blocks(blocks, audio_tail=audio_tail, prefix=b""))


def test_flac_prefix_read_skips_audio_tail(tmp_path):
    flac = tmp_path / "big.flac"
    png = _png(20, (10, 20, 30))
    audio_tail = b"\xab" * (5 * 1024 * 1024)  # 5 MiB of "audio"
    _make_flac(flac, [FlacPictureWriter().build_picture_block("front", png)], audio_tail=audio_tail)

    extractor = ArtworkExtractor()
    prefix = extractor._read_flac_metadata_prefix(str(flac))

    assert len(prefix) < len(audio_tail)
    assert len(prefix) < extractor._FLAC_READ_CHUNK

    roles = extractor.extract_artwork_by_role(str(flac), ".flac")
    assert roles["front"]["data"] == png


def test_flac_prefix_read_grows_for_large_picture_block(tmp_path):
    flac = tmp_path / "bigcover.flac"
    big_png = _noisy_png(400, 400)
    audio_tail = b"\x11" * (2 * 1024 * 1024)
    picture_block = FlacPictureWriter().build_picture_block("front", big_png)
    _make_flac(flac, [picture_block], audio_tail=audio_tail)

    extractor = ArtworkExtractor()
    assert len(picture_block) > extractor._FLAC_READ_CHUNK  # actually exercises the growth loop

    prefix = extractor._read_flac_metadata_prefix(str(flac))
    assert len(prefix) < len(audio_tail)

    roles = extractor.extract_artwork_by_role(str(flac), ".flac")
    assert roles["front"]["data"] == big_png


def test_mp3_prefix_read_skips_audio_tail(tmp_path):
    mp3 = tmp_path / "big.mp3"
    png = _png(20, (200, 0, 0))
    tag = ID3TagWriter().build_id3_tag([Id3PictureWriter().build_apic_frame("front", png)])
    audio_tail = b"\xcd" * (5 * 1024 * 1024)
    mp3.write_bytes(tag + audio_tail)

    extractor = ArtworkExtractor()
    prefix = extractor._read_mp3_id3_prefix(str(mp3))

    assert len(prefix) == len(tag)
    assert len(prefix) < len(audio_tail)

    roles = extractor.extract_artwork_by_role(str(mp3), ".mp3")
    assert roles["front"]["data"] == png


def test_flac_prefix_read_handles_missing_marker(tmp_path):
    junk = tmp_path / "not_flac.flac"
    junk.write_bytes(b"not a real flac file" * 10)

    extractor = ArtworkExtractor()
    prefix = extractor._read_flac_metadata_prefix(str(junk))

    assert prefix == junk.read_bytes()
    assert extractor.extract_artwork_by_role(str(junk), ".flac") == {}


def test_unreadable_file_raises_instead_of_reporting_no_art(tmp_path):
    # A read error must not look like "no pictures": ArtworkCache would cache that as "no art".
    missing = tmp_path / "gone.flac"

    with pytest.raises(OSError):
        ArtworkExtractor().extract_artwork_by_role(str(missing), ".flac")


# ---------------------------------------------------------------------------
# Regression: MetadataExtractor.extract_metadata must not read the whole audio file into memory.
# ---------------------------------------------------------------------------


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
