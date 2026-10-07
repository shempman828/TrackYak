"""Small binary-parsing helpers shared across metadata format readers and writers."""

import re
import struct

# ID3 text encodings: 0 = ISO-8859-1, 1 = UTF-16 with BOM, 2 = UTF-16BE, 3 = UTF-8.
_ID3_CODECS = {0: "latin-1", 1: "utf-16", 2: "utf-16-be", 3: "utf-8"}

_VALID_FRAME_ID = re.compile(rb"^[A-Z0-9]{3,4}$")


def syncsafe_to_int(data: bytes) -> int:
    """Decode an ID3v2 syncsafe integer (7 usable bits per byte)."""
    result = 0
    for byte in data:
        result = (result << 7) | (byte & 0x7F)
    return result


def is_valid_frame_id(frame_id: bytes) -> bool:
    """Return True if frame_id is a well-formed 3- or 4-character ID3 frame ID."""
    return bool(_VALID_FRAME_ID.match(frame_id))


def id3_frames_offset(data: bytes) -> int:
    """Return the offset of the first ID3v2 frame, past the header and any extended header."""
    if len(data) < 10 or data[0:3] != b"ID3":
        return 0
    version_major, flags = data[3], data[5]
    if not flags & 0x40 or version_major not in (3, 4) or len(data) < 14:
        return 10
    if version_major == 3:
        # v2.3: plain size that excludes the 4-byte size field itself.
        return 10 + 4 + struct.unpack(">I", data[10:14])[0]
    # v2.4: syncsafe size that includes the size field.
    return 10 + syncsafe_to_int(data[10:14])


def id3_tag_end(data: bytes) -> int:
    """Return the offset just past the ID3v2 tag (and its v2.4 footer), or 0 if there is no tag."""
    if len(data) < 10 or data[0:3] != b"ID3":
        return 0
    footer = 10 if data[3] == 4 and data[5] & 0x10 else 0
    return 10 + syncsafe_to_int(data[6:10]) + footer


def id3_terminator(encoding: int) -> bytes:
    """Return the string terminator for an ID3 text encoding."""
    return b"\x00\x00" if encoding in (1, 2) else b"\x00"


def split_id3_string(data: bytes, encoding: int) -> tuple[bytes, bytes]:
    """Split data at the first encoding-correct terminator into (string, rest)."""
    terminator = id3_terminator(encoding)
    step = len(terminator)
    pos = 0
    while pos <= len(data) - step:
        # UTF-16 terminators only count on a 2-byte boundary.
        if data[pos : pos + step] == terminator:
            return data[:pos], data[pos + step :]
        pos += step
    return data, b""


def decode_id3_string(data: bytes, encoding: int) -> str:
    """Decode an ID3 string in the given encoding, stripping trailing NULs."""
    return data.decode(_ID3_CODECS.get(encoding, "latin-1"), errors="ignore").strip("\x00")


def parse_id3_lang_frame(body: bytes) -> tuple[int, str, str, bytes] | None:
    """Parse a COMM/USLT body into (encoding, language, description, text_bytes)."""
    if len(body) < 4:
        return None
    if body[0] > 3 and body[3] <= 3:
        # Legacy layout written by older app versions: language before the encoding byte, no description.
        return body[3], body[0:3].decode("latin-1", errors="ignore"), "", body[4:]
    encoding = body[0]
    language = body[1:4].decode("latin-1", errors="ignore")
    description, text = split_id3_string(body[4:], encoding)
    return encoding, language, decode_id3_string(description, encoding), text
