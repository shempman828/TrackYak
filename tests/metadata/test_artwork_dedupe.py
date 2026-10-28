"""Regression: writing FLAC or MP3 artwork must collapse duplicate same-type
PICTURE blocks / APIC frames.

Bug: some files carry two type-3 (front cover) pictures because a
third-party tagger appended a new cover without removing the old one. The
writers stripped only the first duplicate and appended the new picture, so
the reader returned the stale one and verify_artwork_write rolled the whole
write back.
"""

import io
import struct

from PIL import Image
import pytest

from src.metadata.metadata_image_utils import find_picture_index_for_role, find_picture_indices_for_role
from src.metadata.readers.metadata_artwork import ArtworkExtractor
from src.metadata.writers.id3.metadata_id3_writer import ID3TagWriter
from src.metadata.writers.id3.metadata_mp3_file_writer import MP3FileWriter
from src.metadata.writers.id3.metadata_writer_id3_picture import Id3PictureWriter
from src.metadata.writers.vorbis.metadata_flac_file_writer import FlacFileWriter
from src.metadata.writers.vorbis.metadata_writer_flac_picture import FlacPictureWriter

# ---------------------------------------------------------------------------
# Regression: writing FLAC artwork must collapse duplicate same-type
# ---------------------------------------------------------------------------


_STREAMINFO = b"\x00" * 34  # contents irrelevant to the metadata surgery under test


def _png(size: int, colour: tuple[int, int, int]) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (size, size), colour).save(buf, "PNG")
    return buf.getvalue()


def _make_flac(path, picture_payloads: list[bytes]) -> None:
    """A minimal but structurally valid FLAC: fLaC + STREAMINFO + the given
    raw PICTURE payloads, no audio frames."""
    writer = FlacFileWriter()
    blocks = [(0, _STREAMINFO)] + [(6, payload) for payload in picture_payloads]
    path.write_bytes(writer._serialize_blocks(blocks, audio_tail=b"", prefix=b""))


def _front_block(size: int, colour=(10, 20, 30)) -> bytes:
    return FlacPictureWriter().build_picture_block("front", _png(size, colour))


def _unpack_type(item):
    """picture_type_for_item stand-in: items are (picture_type,) tuples."""
    return item[0]


def test_indices_helper_returns_every_match_for_role():
    items = [(3,), (4,), (3,), (99,)]

    assert find_picture_indices_for_role(items, "front", _unpack_type) == [0, 2]
    assert find_picture_indices_for_role(items, "rear", _unpack_type) == [1]
    assert find_picture_indices_for_role(items, "liner", _unpack_type) == []
    # singular helper still yields the first match / None
    assert find_picture_index_for_role(items, "front", _unpack_type) == 0
    assert find_picture_index_for_role(items, "liner", _unpack_type) is None


def test_untyped_single_picture_still_maps_to_front():
    assert find_picture_indices_for_role([(0,)], "front", _unpack_type) == [0]
    # ambiguous once there's more than one untyped picture
    assert find_picture_indices_for_role([(0,), (0,)], "front", _unpack_type) == []


def _metadata_blocks(flac):
    data = flac.read_bytes()
    writer = FlacFileWriter()
    return writer._parse_metadata_blocks(data, writer._prefix_length(data))


def test_write_artwork_collapses_duplicate_front_blocks(tmp_path):
    flac = tmp_path / "dup.flac"
    _make_flac(flac, [_front_block(60, (200, 0, 0)), _front_block(12, (0, 0, 200))])
    # sanity: the raw file really does carry two PICTURE blocks
    raw_pics = [b for b in _metadata_blocks(flac) if b[0] == 6]
    assert len(raw_pics) == 2

    new_cover = _png(40, (0, 200, 0))
    assert FlacFileWriter().write_artwork(str(flac), "front", new_cover) is True

    all_pictures = ArtworkExtractor()._extract_flac_artwork_all(flac.read_bytes())
    assert list(all_pictures) == [3]
    assert all_pictures[3]["data"] == new_cover


def test_write_artwork_removal_strips_all_duplicate_blocks(tmp_path):
    flac = tmp_path / "dup.flac"
    _make_flac(flac, [_front_block(60, (200, 0, 0)), _front_block(12, (0, 0, 200))])

    assert FlacFileWriter().write_artwork(str(flac), "front", None) is True

    raw_pics = [b for b in _metadata_blocks(flac) if b[0] == 6]
    assert raw_pics == []


# ---------------------------------------------------------------------------
# Regression: writing MP3 artwork must collapse duplicate same-type APIC
# ---------------------------------------------------------------------------


def _tit2(text: str) -> bytes:
    body = b"\x00" + text.encode("iso-8859-1")  # encoding byte + ISO-8859-1 text
    return b"TIT2" + struct.pack(">I", len(body)) + b"\x00\x00" + body


def _apic(role: str, size: int, colour=(10, 20, 30)) -> bytes:
    return Id3PictureWriter().build_apic_frame(role, _png(size, colour))


def _make_mp3(path, frames: list[bytes]) -> None:
    """A minimal MP3: just an ID3v2.3 tag built from the given raw frames,
    no audio."""
    path.write_bytes(ID3TagWriter().build_id3_tag(frames))


def _apic_count(path) -> int:
    return sum(1 for fid, _, _ in MP3FileWriter()._parse_frames(path.read_bytes()) if fid == "APIC")


def test_write_artwork_collapses_duplicate_front_frames(tmp_path):
    mp3 = tmp_path / "dup.mp3"
    _make_mp3(mp3, [_apic("front", 60, (200, 0, 0)), _apic("front", 12, (0, 0, 200))])
    assert _apic_count(mp3) == 2

    new_cover = _png(40, (0, 200, 0))
    assert MP3FileWriter().write_artwork(str(mp3), "front", new_cover) is True

    all_pictures = ArtworkExtractor()._extract_mp3_artwork_all(mp3.read_bytes())
    assert list(all_pictures) == [3]
    assert all_pictures[3]["data"] == new_cover
    assert _apic_count(mp3) == 1


def test_write_artwork_removal_strips_all_duplicate_frames(tmp_path):
    mp3 = tmp_path / "dup.mp3"
    _make_mp3(mp3, [_apic("front", 60, (200, 0, 0)), _apic("front", 12, (0, 0, 200))])

    assert MP3FileWriter().write_artwork(str(mp3), "front", None) is True

    assert _apic_count(mp3) == 0


def test_non_picture_frame_survives_dedup_write(tmp_path):
    mp3 = tmp_path / "dup.mp3"
    _make_mp3(mp3, [_tit2("Keep Me"), _apic("front", 60, (200, 0, 0)), _apic("front", 12, (0, 0, 200))])

    assert MP3FileWriter().write_artwork(str(mp3), "front", _png(40, (0, 200, 0))) is True

    frame_map = MP3FileWriter().get_existing_frame_map(str(mp3))
    assert b"Keep Me" in frame_map["TIT2"]
    assert _apic_count(mp3) == 1
