"""Regression: extract_artwork_by_role must read only the tag/metadata
region of a track, not the whole file.

Bug: it read the entire file into memory before looking for embedded
artwork, even though the artwork lives in the file's header/metadata
region. The library-wide artwork-consistency scan (src/library/
library_artwork_consistency.py) calls this once per track, so on a large
library this turned into tens of minutes spent reading multi-megabyte
audio payloads no one asked for.

  AC1  a FLAC with a large audio tail after its metadata blocks is read
       far short of the file's full size, and the embedded front picture
       is still found correctly.
  AC2  a FLAC whose PICTURE block itself is bigger than one read chunk
       still has its metadata read in full (growing the buffer), without
       pulling in the audio tail.
  AC3  an MP3 with a large blob after its ID3 tag is read far short of
       the file's full size, and the embedded front picture is still
       found correctly.
  AC4  a file with no "fLaC" marker at all is read once and handled
       without looping forever.
"""

import io
import os

from PIL import Image

from src.metadata.metadata_artwork import ArtworkExtractor
from src.metadata.writers.metadata_flac_file_writer import FlacFileWriter
from src.metadata.writers.metadata_id3_writer import ID3TagWriter
from src.metadata.writers.metadata_writer_flac_picture import FlacPictureWriter
from src.metadata.writers.metadata_writer_id3_picture import Id3PictureWriter

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


# --- AC1 ---------------------------------------------------------------------


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


# --- AC2 ---------------------------------------------------------------------


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


# --- AC3 ---------------------------------------------------------------------


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


# --- AC4 ---------------------------------------------------------------------


def test_flac_prefix_read_handles_missing_marker(tmp_path):
    junk = tmp_path / "not_flac.flac"
    junk.write_bytes(b"not a real flac file" * 10)

    extractor = ArtworkExtractor()
    prefix = extractor._read_flac_metadata_prefix(str(junk))

    assert prefix == junk.read_bytes()
    assert extractor.extract_artwork_by_role(str(junk), ".flac") == {}
