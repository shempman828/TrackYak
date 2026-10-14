"""TCON numeric genre references resolve to ID3v1 genre names during raw ID3 read."""

import struct

import pytest

from src.metadata.metadata_id3_genres import ID3V1_GENRES, resolve_tcon_value
from src.metadata.readers.metadata_raw_tags import RawTagExtractor


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
