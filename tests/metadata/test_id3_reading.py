"""ID3 reading: TCON genre references and v2.3/v2.4 frame format flags
(unsync, compression, grouping, encryption).
"""

import struct
import zlib

import pytest

from src.metadata.metadata_id3_genres import ID3V1_GENRES, resolve_tcon_value
from src.metadata.readers.metadata_raw_tags import RawTagExtractor
from src.metadata.writers.id3.metadata_mp3_file_writer import MP3FileWriter
from src.metadata.writers.metadata_writer_types import WriteMode

# ---------------------------------------------------------------------------
# TCON numeric genre references resolve to ID3v1 genre names during raw ID3 read.
# ---------------------------------------------------------------------------


def _id3v23_with_tcon(text: bytes) -> bytes:
    body = b"\x00" + text  # latin-1 encoding byte
    frame = b"TCON" + struct.pack(">I", len(body)) + b"\x00\x00" + body
    size = len(frame)
    syncsafe = bytes([(size >> 21) & 0x7F, (size >> 14) & 0x7F, (size >> 7) & 0x7F, size & 0x7F])
    return b"ID3\x03\x00\x00" + syncsafe + frame + b"\x00" * 16


def test_table_has_winamp_extensions():
    assert len(ID3V1_GENRES) == 192
    assert ID3V1_GENRES[17] == "Rock"
    assert ID3V1_GENRES[191] == "Psybient"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("(17)", ["Rock"]),
        ("(17)Rock", ["Rock"]),
        ("17", ["Rock"]),
        ("(4)Eurodisco", ["Disco", "Eurodisco"]),
        ("(17)(RX)", ["Rock", "Remix"]),
        ("RX", ["Remix"]),
        ("(CR)", ["Cover"]),
        ("((Bracketed) Jazz", ["(Bracketed) Jazz"]),
        ("Shoegaze", ["Shoegaze"]),
        ("(255)", []),
        ("Electronic (Live)", ["Electronic (Live)"]),
    ],
)
def test_resolve_tcon_value(value, expected):
    assert resolve_tcon_value(value) == expected


def test_raw_reader_resolves_tcon_reference():
    tags = RawTagExtractor().extract_raw_tags(_id3v23_with_tcon(b"(17)Rock"), ".mp3")
    assert tags["TCON"] == ["Rock"]


def test_raw_reader_resolves_v24_null_separated_values():
    tags = RawTagExtractor().extract_raw_tags(_id3v23_with_tcon(b"8\x00RX"), ".mp3")
    assert tags["TCON"] == ["Jazz", "Remix"]


# ---------------------------------------------------------------------------
# Regression: rewriting an MP3 tag must decode frames that carry ID3 format flags.
# ---------------------------------------------------------------------------


_TEXT = b"\x00\xff\xe0 caf\xe9"  # latin-1 text body; FF E0 is a false sync that unsync must escape


def _syncsafe(n: int) -> bytes:
    return bytes((n >> shift) & 0x7F for shift in (21, 14, 7, 0))


def _unsync(data: bytes) -> bytes:
    return data.replace(b"\xff", b"\xff\x00")


def _v24_frame(frame_id: bytes, flags: int, payload: bytes) -> bytes:
    return frame_id + _syncsafe(len(payload)) + bytes((0, flags)) + payload


def _v23_frame(frame_id: bytes, flags: int, payload: bytes) -> bytes:
    return frame_id + struct.pack(">I", len(payload)) + bytes((0, flags)) + payload


def _tag(version_major: int, frames: list[bytes], header_flags: int = 0) -> bytes:
    body = b"".join(frames)
    return b"ID3" + bytes((version_major, 0, header_flags)) + _syncsafe(len(body)) + body


def _plain_frame(frame_id: bytes, body: bytes) -> bytes:
    return frame_id + struct.pack(">I", len(body)) + b"\x00\x00" + body


def _rewrite(tmp_path, tag: bytes) -> dict[str, bytes]:
    """Write tag to an MP3, rewrite it through MP3FileWriter, and return {frame_id: whole v2.3 frame}."""
    path = tmp_path / "t.mp3"
    path.write_bytes(tag + b"\xff\xfbAUDIO")
    assert MP3FileWriter().write_tags(str(path), [_plain_frame(b"TIT3", b"\x00new")], WriteMode.UPDATE_EXISTING)
    data = path.read_bytes()
    assert data[3] == 3
    assert data.endswith(b"\xff\xfbAUDIO")
    return {frame_id: data[pos : pos + size] for frame_id, pos, size in MP3FileWriter()._parse_frames(data)}


def test_v24_unsync_and_data_length_indicator_frame_is_decoded(tmp_path):
    payload = _unsync(_syncsafe(len(_TEXT)) + _TEXT)
    frames = _rewrite(tmp_path, _tag(4, [_v24_frame(b"TIT2", 0x02 | 0x01, payload)]))
    assert frames["TIT2"] == _plain_frame(b"TIT2", _TEXT)


def test_v24_compressed_frame_is_decompressed(tmp_path):
    payload = _syncsafe(len(_TEXT)) + zlib.compress(_TEXT)
    frames = _rewrite(tmp_path, _tag(4, [_v24_frame(b"TALB", 0x08 | 0x01, payload)]))
    assert frames["TALB"] == _plain_frame(b"TALB", _TEXT)


def test_v24_tag_level_unsync_is_undone(tmp_path):
    frames = _rewrite(tmp_path, _tag(4, [_v24_frame(b"TPE1", 0, _unsync(_TEXT))], header_flags=0x80))
    assert frames["TPE1"] == _plain_frame(b"TPE1", _TEXT)


def test_grouped_frames_lose_only_the_group_byte(tmp_path):
    v24 = _rewrite(tmp_path, _tag(4, [_v24_frame(b"TCON", 0x40, b"\x07" + _TEXT)]))
    v23 = _rewrite(tmp_path, _tag(3, [_v23_frame(b"TCON", 0x20, b"\x07" + _TEXT)]))
    assert v24["TCON"] == v23["TCON"] == _plain_frame(b"TCON", _TEXT)


def test_v23_compressed_frame_is_decompressed(tmp_path):
    payload = struct.pack(">I", len(_TEXT)) + zlib.compress(_TEXT)
    frames = _rewrite(tmp_path, _tag(3, [_v23_frame(b"TALB", 0x80, payload)]))
    assert frames["TALB"] == _plain_frame(b"TALB", _TEXT)


def test_encrypted_frame_is_dropped_and_others_kept(tmp_path):
    tag = _tag(4, [_v24_frame(b"TPE2", 0x04, b"\x80secret"), _v24_frame(b"TIT2", 0, _TEXT)])
    frames = _rewrite(tmp_path, tag)
    assert "TPE2" not in frames
    assert frames["TIT2"] == _plain_frame(b"TIT2", _TEXT)
    assert frames["TIT3"] == _plain_frame(b"TIT3", b"\x00new")
